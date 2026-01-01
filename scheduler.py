import json
import logging
import os
import time
import concurrent.futures
from datetime import datetime, timedelta
import config
from booking_agent import BookingAgent

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self.users_data = self._load_json(config.CREDENTIALS_FILE, [])
        self.history = self._load_json(config.HISTORY_FILE, [])
        self.agents = [] 
        self.last_successful_user = None # Tracks who booked the previous slot

    def _load_json(self, filepath, default):
        if not os.path.exists(filepath): return default
        try:
            with open(filepath, 'r') as f: return json.load(f)
        except: return default

    def _save_history(self, record):
        self.history.append(record)
        with open(config.HISTORY_FILE, 'w') as f:
            json.dump(self.history, f, indent=4)

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

    def get_next_target_slot(self):
        """
        Finds the single most urgent slot to book.
        We strictly look for slots opening SOON or NOW.
        """
        now = datetime.now()
        # Horizon: We are willing to book slots up to 7 days ahead
        # But crucially, we need to know WHEN they open.
        # Open Time = Slot Start Time - 7 Days + 1 Hour.
        
        candidates = []
        self.history = self._load_json(config.HISTORY_FILE, [])
        bookings = self._load_json(config.BOOKINGS_FILE, [])

        for req in bookings:
            day_diff = req['day_of_week'] - now.weekday()
            
            # Next Week's Slot (Offset 7)
            delta_days = day_diff + 7
            target_date = (now + timedelta(days=delta_days)).date()
            
            # Ignore if it's less than 7 days away (e.g. "Next Wed" when today is "Thu")
            days_gap = (target_date - now.date()).days
            if days_gap < 7: continue

            for h in range(req['start_hour'], req['end_hour']):
                slot_start = datetime.combine(target_date, datetime.min.time()).replace(hour=h)
                
                # Logic to convert to Israel time -> UTC-2 for API
                # IMPORTANT: API expects UTC time. Israel Winter is UTC+2.
                utc_start = (slot_start - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                utc_end = (slot_start + timedelta(hours=1) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                
                # Calculate Opening Time
                # A slot at T opens at (T - 7 days + 1 hour)
                # Correction: Based on your input, "13:10 -> 12:00 available".
                # Means: Slot T available if Now >= T - 7days + 1hour?
                # No, your example was: "Thu 13:10, last avail is Thu 12:00".
                # This means Slot 13:00 (Next Thu) opens at 14:00 (This Thu).
                # Formula: Opening Time = Slot_Start - 7 Days + 1 Hour.
                
                opening_time = slot_start - timedelta(days=7) + timedelta(hours=1)
                
                slot_key = slot_start.strftime("%Y-%m-%d %H:%M")
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

        # Return the first candidate and seconds until it opens
        target = candidates[0]
        seconds_until_open = (target['opening_time'] - now).total_seconds()
        
        return target, seconds_until_open

    def attempt_booking(self, slot):
        exhausted_emails = set()
        
        # 1. ORGANIZE ROOMS (High Priority First)
        room_batches = [
            list(config.HIGH_PRIORITY_ROOMS.keys()), # Batch 1
            list(config.LOW_PRIORITY_ROOMS.keys())   # Batch 2
        ]
        
        # 2. ORGANIZE USERS
        # Put the user who booked the last slot at the front of the line
        active_agents = list(self.agents) # shallow copy
        if self.last_successful_user:
            # Find agent object
            priority_agent = next((a for a in active_agents if a.email == self.last_successful_user), None)
            if priority_agent:
                active_agents.remove(priority_agent)
                active_agents.insert(0, priority_agent) # Move to front

        # --- RETRY LOOP (For Sniping) ---
        # We try this whole block. If it fails due to "Not Open Yet", we loop.
        start_time = time.time()
        
        while True:
            # Timeout check (don't loop forever, max 2 minutes of trying)
            if time.time() - start_time > 120:
                logger.info("   [TIMEOUT] Gave up on slot.")
                return False

            for batch_name, rooms in zip(["HIGH", "LOW"], room_batches):
                for rid in rooms:
                    for agent in active_agents:
                        if agent.email in exhausted_emails: continue

                        # logger.info(f"   [TRY] {batch_name} Room {rid} -> {agent.email}")
                        res = agent.book_room(rid, slot['utc_start'], slot['utc_end'])

                        if res == "SUCCESS":
                            rname = config.ALL_ROOMS[rid]
                            logger.info(f"   [SUCCESS] {slot['key']} | {rname} | {agent.email}")
                            self._save_history({
                                "slot_key": slot['key'], "room": rname, 
                                "user": agent.email, "booked_at": datetime.now().isoformat()
                            })
                            self.last_successful_user = agent.email
                            return True
                        
                        elif res == "USER_LIMIT":
                            exhausted_emails.add(agent.email)
                        
                        elif res == "ROOM_TAKEN":
                            break # Agent valid, Room dead. Next Room.
                        
                        # If ERROR, we assume it *might* be "Not Open Yet" or Server Error.
                        # We continue iterating through rooms/users, but we stay in the While Loop.

            # If we went through all Rooms and all Users and didn't succeed:
            if len(exhausted_emails) == len(self.agents):
                logger.warning("   [FAILED] All users exhausted limit.")
                return False
            
            # If we are here, it means we failed but have users left. 
            # Likely "Not Open Yet". Sleep tiny bit and Retry.
            time.sleep(0.5)

    def run(self):
        while True:
            target, seconds_wait = self.get_next_target_slot()
            
            if not target:
                logger.info("No targets found. Sleeping 10m.")
                time.sleep(600)
                continue

            # LOGIC:
            # If > 60 seconds away: Sleep until 60s before.
            # If <= 60 seconds away: WAKE UP, Login, Start Spamming.
            
            if seconds_wait > 60:
                sleep_time = seconds_wait - 60
                wake_time = datetime.now() + timedelta(seconds=sleep_time)
                logger.info(f"Target: {target['key']}. Opens in {int(seconds_wait/60)}m.")
                logger.info(f"Sleeping until {wake_time.strftime('%H:%M:%S')}...")
                time.sleep(sleep_time)
            
            # WAKE UP SEQUENCE
            logger.info(f"--- PREPARING FOR: {target['key']} ---")
            
            # 1. Re-Verify Login (Multithreaded)
            self.initialize_agents()
            
            # 2. Wait exactly for opening time (minus 2 seconds offset for latency)
            # Actually, better to start spamming 5 seconds early.
            final_wait = (target['opening_time'] - datetime.now()).total_seconds() - 5
            if final_wait > 0:
                time.sleep(final_wait)
            
            # 3. ATTACK
            logger.info(f"--- EXECUTING BOOKING: {target['key']} ---")
            self.attempt_booking(target)