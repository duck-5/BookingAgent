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
        self.bookings = []
        self.history = self._load_json(config.HISTORY_FILE, [])
        self.agents = [] 

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

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_agent = {executor.submit(a.login): a for a in temp_agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent = future_to_agent[future]
                if future.result(): self.agents.append(agent)
        
        logger.info(f"Agents Ready: {len(self.agents)}")

    def get_valid_slots(self):
        now = datetime.now()
        # Horizon Logic: Local time
        horizon = now + timedelta(days=7, hours=-1)
        
        valid_slots = []
        self.history = self._load_json(config.HISTORY_FILE, [])
        self.bookings = self._load_json(config.BOOKINGS_FILE, [])

        for req in self.bookings:
            day_diff = req['day_of_week'] - now.weekday()
            
            # STRICT RULE: Next Week Only
            delta_days = day_diff + 7
            target_date = (now + timedelta(days=delta_days)).date()
            
            days_gap = (target_date - now.date()).days
            if days_gap < 7: continue

            for h in range(req['start_hour'], req['end_hour']):
                # 1. Calculate LOCAL Start/End (for logic and history)
                slot_start = datetime.combine(target_date, datetime.min.time()).replace(hour=h)
                slot_end = slot_start + timedelta(hours=1)
                slot_key = slot_start.strftime("%Y-%m-%d %H:%M")

                if slot_start <= now: continue
                if slot_start > horizon: continue 

                if any(x['slot_key'] == slot_key for x in self.history):
                    continue
                
                # 2. Calculate UTC Strings for API (Local - 2 Hours)
                # Format: 2026-01-08T08:00:00.000Z
                utc_start_dt = slot_start - timedelta(hours=2)
                utc_end_dt = slot_end - timedelta(hours=2)
                
                utc_start_str = utc_start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                utc_end_str = utc_end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

                valid_slots.append({
                    "start": slot_start, "end": slot_end,
                    "key": slot_key, "day_name": slot_start.strftime("%A"),
                    "utc_start": utc_start_str,
                    "utc_end": utc_end_str
                })

        return sorted(valid_slots, key=lambda x: x['start'])

    def run_cycle(self):
        if not self.agents:
            self.initialize_agents()
            if not self.agents: return

        slots = self.get_valid_slots()
        if not slots: return

        exhausted_emails = set()
        # Ensure we prioritize a valid room from the new list
        preferred_room_id = list(config.ROOMS.keys())[0]

        logger.info(f"--- Cycle Start: {len(slots)} Targetable Slots ---")

        for slot in slots:
            logger.info(f"Targeting: {slot['key']} ({slot['day_name']})")
            success = False
            rooms = list(config.ROOMS.keys())
            if preferred_room_id in rooms:
                rooms.remove(preferred_room_id)
                rooms.insert(0, preferred_room_id)

            for rid in rooms:
                if success: break
                for agent in self.agents:
                    if agent.email in exhausted_emails: continue
                    time.sleep(1.0) 

                    # Pass the pre-calculated UTC strings
                    res = agent.book_room(rid, slot['utc_start'], slot['utc_end'])
                    rname = config.ROOMS[rid]

                    if res == "SUCCESS":
                        logger.info(f"   [SUCCESS] Booked {rname} using {agent.email}")
                        self._save_history({
                            "slot_key": slot['key'], "room": rname,
                            "user": agent.email, "booked_at": datetime.now().isoformat()
                        })
                        preferred_room_id = rid
                        success = True
                        break
                    elif res == "USER_LIMIT":
                        logger.warning(f"   [LIMIT] User exhausted: {agent.email}")
                        exhausted_emails.add(agent.email)
                    elif res == "ROOM_TAKEN":
                        break
            
            if not success:
                logger.info(f"   [FAILED] Could not book {slot['key']}")