import json
import logging
import os
import time
import concurrent.futures
from datetime import datetime, timedelta
import config
from booking_agent import BookingAgent, BookingResult
from google_calendar_client import GoogleCalendarClient
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self.users_data: List[Dict[str, str]] = self._load_json(config.CREDENTIALS_FILE, [])
        self.history: List[Dict[str, Any]] = self._load_json(config.HISTORY_FILE, [])
        self.agents: List[BookingAgent] = [] 
        self.last_successful_user: Optional[str] = None # Tracks who booked the previous slot
        self.calendar_client = GoogleCalendarClient(
            credentials_file=config.GOOGLE_CALENDAR_CREDENTIALS,
            token_file=config.GOOGLE_CALENDAR_TOKEN
        )
        self.calendar_id = self.calendar_client.get_or_create_calendar("Library Bookings") 

    def _load_json(self, filepath: str, default: Any) -> Any:
        if not os.path.exists(filepath): return default
        try:
            with open(filepath, 'r') as f: return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load JSON from {filepath}: {e}")
            return default

    def _save_history(self, record: Dict[str, Any]):
        self.history.append(record)
        try:
            with open(config.HISTORY_FILE, 'w') as f:
                json.dump(self.history, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def initialize_agents(self):
        if not self.users_data: return
        logger.info(f"--- Initializing {len(self.users_data)} Agents ---")
        temp_agents = [BookingAgent(u) for u in self.users_data]
        self.agents = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_agent = {executor.submit(a.login): a for a in temp_agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent = future_to_agent[future]
                if future.result(): self.agents.append(agent)
        
        logger.info(f"Agents Ready: {len(self.agents)}")

    def get_next_target_slot(self) -> Tuple[Optional[Dict[str, Any]], float]:
        """
        Finds the single most urgent slot to book.
        We strictly look for slots opening SOON or NOW.
        """
        now = datetime.now()
        
        candidates = []
        # Reload history to ensure we see the failures we just wrote
        self.history = self._load_json(config.HISTORY_FILE, [])
        bookings = self._load_json(config.BOOKINGS_FILE, [])

        for req in bookings:
            day_diff = req['day_of_week'] - now.weekday()
            
            # Next Week's Slot (Offset 7)
            delta_days = day_diff + 7
            target_date = (now + timedelta(days=delta_days)).date()
            
            # Ignore if it's less than 7 days away
            days_gap = (target_date - now.date()).days
            if days_gap < 7: continue

            for h in range(req['start_hour'], req['end_hour']):
                slot_start = datetime.combine(target_date, datetime.min.time()).replace(hour=h)
                
                # UTC logic
                utc_start = (slot_start - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                utc_end = (slot_start + timedelta(hours=1) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                
                # Opening Time: T - 7 Days + 1 Hour
                opening_time = slot_start - timedelta(days=7) + timedelta(hours=1)
                
                slot_key = slot_start.strftime("%Y-%m-%d %H:%M")
                
                # CHECK HISTORY: This will now filter out "FAILED" slots too
                if any(x['slot_key'] == slot_key for x in self.history): continue

                candidates.append({
                    "start": slot_start,
                    "opening_time": opening_time,
                    "key": slot_key,
                    "utc_start": utc_start, "utc_end": utc_end,
                    "comment": req.get("comment", "")
                })

        # Sort by opening time
        candidates.sort(key=lambda x: x['opening_time'])
        
        if not candidates: return None, 0

        target = candidates[0]
        seconds_until_open = (target['opening_time'] - now).total_seconds()
        
        return target, seconds_until_open

    def attempt_booking(self, slot: Dict[str, Any]) -> bool:
        exhausted_emails = set()
        
        # 1. ORGANIZE ROOMS (High Priority First)
        room_batches = [
            list(config.HIGH_PRIORITY_ROOMS.keys()), # Batch 1
            list(config.LOW_PRIORITY_ROOMS.keys())   # Batch 2
        ]
        
        # 2. ORGANIZE USERS
        active_agents = list(self.agents) # shallow copy
        if self.last_successful_user:
            priority_agent = next((a for a in active_agents if a.email == self.last_successful_user), None)
            if priority_agent:
                active_agents.remove(priority_agent)
                active_agents.insert(0, priority_agent)

        # --- RETRY LOOP (For Sniping) ---
        start_time = time.time()
        
        while True:
            # --- TIMEOUT / GIVE UP LOGIC ---
            if time.time() - start_time > 120:
                logger.info(f"   [TIMEOUT] Could not book {slot['key']} within 2 mins.")
                
                # SAVE FAILURE RECORD
                self._save_history({
                    "slot_key": slot['key'],
                    "status": "FAILED",
                    "reason": "Timeout",
                    "timestamp": datetime.now().isoformat()
                })
                return False

            for batch_name, rooms in zip(["HIGH", "LOW"], room_batches):
                for rid in rooms:
                    rname = config.ALL_ROOMS[rid]
                    
                    for agent in active_agents:
                        if agent.email in exhausted_emails: continue

                        # CHECK FOR CONSECUTIVE BOOKING
                        # Search logic: Same User, Same Room, EndTime == New StartTime
                        prev_booking = None
                        for h in self.history:
                            if (h.get('user') == agent.email and 
                                h.get('room') == rname and 
                                h.get('end_utc') == slot['utc_start'] and
                                h.get('status') == 'SUCCESS' and
                                h.get('ref_num')):
                                prev_booking = h
                                break
                        
                        res = BookingResult.ERROR
                        ref_num = None

                        if prev_booking:
                            # ATTEMPT EXTENSION
                            original_start = prev_booking.get('start_utc', slot['utc_start']) # Fallback if missing? Should not happen if history is good.
                            # If history is missing start_utc, we might have an issue. 
                            # But if end_utc matched, it must have been a valid record.
                            # IMPORTANT: If 'start_utc' is missing from old records, we can't extend reliably.
                            # We'll assume new records have it. If missing, maybe fallback to book_room?
                            if not original_start:
                                logger.warning(f"Found consecutive booking for {agent.email} but missing start_utc. Fallback to create.")
                                res, ref_num = agent.book_room(rid, slot['utc_start'], slot['utc_end'])
                            else:
                                logger.info(f"   [EXTENDING] {agent.email} in {rname} (Ref: {prev_booking['ref_num']})")
                                res, ref_num = agent.extend_booking(rid, prev_booking['ref_num'], original_start, slot['utc_end'])
                                
                                # If Extension failed due to limit or generic error, try Creating New?
                                # User says: "If the user can't schedual anymore, then a new event needs to be created."
                                # "can't schedule anymore" implies USER_LIMIT. But creating new would also hit USER_LIMIT?
                                # Unless the limit is per-reservation duration.
                                if res != BookingResult.SUCCESS:
                                    logger.info(f"   [EXTENSION FAILED] {res}. Retrying as new booking.")
                                    res, ref_num = agent.book_room(rid, slot['utc_start'], slot['utc_end'])
                        else:
                            # CREATE NEW
                            res, ref_num = agent.book_room(rid, slot['utc_start'], slot['utc_end'])

                        if res == BookingResult.SUCCESS:
                            display_start = prev_booking.get('start_utc', slot['utc_start']) if (prev_booking and ref_num == prev_booking['ref_num']) else slot['utc_start']
                            
                            logger.info(f"   [SUCCESS] {slot['key']} | {rname} | {agent.email} | Ref: {ref_num}")
                            self._save_history({
                                "slot_key": slot['key'], "room": rname, 
                                "user": agent.email, "status": "SUCCESS",
                                "booked_at": datetime.now().isoformat(),
                                "ref_num": ref_num,
                                "start_utc": display_start, # Track the orginal start
                                "end_utc": slot['utc_end']  # Track where we ended up
                            })
                            self.last_successful_user = agent.email
                            
                            # --- GOOGLE CALENDAR SYNC ---
                            try:
                                summary = f"Booking: {rname}"
                                description = f"Booked by {agent.email}. Ref: {ref_num}"
                                location = rname
                                self.calendar_client.add_event(summary, display_start, slot['utc_end'], description, location, calendar_id=self.calendar_id or 'primary')
                            except Exception as e:
                                logger.error(f"Failed to add to calendar: {e}")

                            return True
                        
                        elif res == BookingResult.USER_LIMIT:
                            exhausted_emails.add(agent.email)
                        
                        elif res == BookingResult.ROOM_TAKEN:
                            break # Agent valid, Room dead. Next Room.
                        
                        elif res == BookingResult.TOO_EARLY:
                            # No point rotating users or rooms, the Window isn't open.
                            # We break out of agent loop (to sleep) but DON'T mark failed.
                            logger.warning(f"   [TOO EARLY] Window not open yet for {slot['key']}. Retrying...")
                            break

            # --- ALL USERS EXHAUSTED LOGIC ---
            if len(exhausted_emails) == len(self.agents):
                logger.warning(f"   [FAILED] All users exhausted limit for {slot['key']}.")
                
                # SAVE FAILURE RECORD
                self._save_history({
                    "slot_key": slot['key'],
                    "status": "FAILED",
                    "reason": "Users Exhausted",
                    "timestamp": datetime.now().isoformat()
                })
                return False
            
            # Likely "Not Open Yet". Sleep tiny bit and Retry.
            time.sleep(0.5)

    def run(self):
        while True:
            target, seconds_wait = self.get_next_target_slot()
            
            if not target:
                logger.info("No targets found. Sleeping 10m.")
                time.sleep(600)
                continue

            if seconds_wait > 60:
                sleep_time = seconds_wait - 60
                wake_time = datetime.now() + timedelta(seconds=sleep_time)
                logger.info(f"Target: {target['key']}. Opens in {int(seconds_wait/60)}m.")
                logger.info(f"Sleeping until {wake_time.strftime('%H:%M:%S')}...")
                time.sleep(sleep_time)
            
            # WAKE UP SEQUENCE
            logger.info(f"--- PREPARING FOR: {target['key']} ---")
            
            # 1. Re-Verify Login
            self.initialize_agents()
            
            # 2. Wait exactly for opening time
            final_wait = (target['opening_time'] - datetime.now()).total_seconds() - 5
            if final_wait > 0:
                time.sleep(final_wait)
            
            # 3. ATTACK
            logger.info(f"--- EXECUTING BOOKING: {target['key']} ---")
            self.attempt_booking(target)