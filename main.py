import logging
import sys
import time
from datetime import datetime, timedelta

from booking_agent import BookingAgent
from booking_utils import TimeUtils, ConfigLoader

# --- CONFIGURATION ---
ROOM_PRIORITY_LIST = [13, 14, 15, 16, 17, 18, 19]

# 1 Hour Delay Logic (Class 12:00 -> Booking Opens 13:00)
BOOKING_DELAY_HOURS = 1 

ATTACK_START_BUFFER = 60
MAX_ATTACK_DURATION = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("booking_system.log")]
)
logger = logging.getLogger(__name__)

class BookingScheduler:
    def __init__(self):
        self.users, self.rules = ConfigLoader.load_data()
        self.active_agents = []
        self.preferred_room = None # Stores the last successful room

    def prepare_agents(self):
        """Logs in all users and stores valid sessions."""
        self.active_agents = []
        if not self.users: return
        
        logger.info("--- Preparing Agents (Logging In) ---")
        for u in self.users:
            agent = BookingAgent(u)
            if agent.login():
                self.active_agents.append(agent)
        
        logger.info(f"Agents Ready: {len(self.active_agents)}/{len(self.users)}")

    def get_room_order(self):
        """Returns room list with preferred room first."""
        if self.preferred_room and self.preferred_room in ROOM_PRIORITY_LIST:
            # Create a new list with preferred room at the front
            ordered = [self.preferred_room] + [r for r in ROOM_PRIORITY_LIST if r != self.preferred_room]
            return ordered
        return ROOM_PRIORITY_LIST

    def run_attack(self, target_class_time):
        start_utc, end_utc = TimeUtils.get_utc_times(target_class_time)
        attack_start = datetime.now()
        
        # Get prioritized list of rooms
        current_room_list = self.get_room_order()
        
        logger.info(f"--- ATTACK STARTED for {target_class_time.strftime('%H:%M')} (UTC: {start_utc}) ---")
        logger.info(f"Priority Room Order: {current_room_list}")

        while (datetime.now() - attack_start).total_seconds() < MAX_ATTACK_DURATION:
            # 1. Iterate Rooms
            for room_num in current_room_list:
                resource_id = room_num + 10 
                
                # 2. Iterate Users
                for agent in self.active_agents:
                    if agent.book_room(resource_id, start_utc, end_utc):
                        logger.info(f"VICTORY! Room {room_num} secured by {agent.email}")
                        
                        # Remember this room for the next consecutive slot
                        self.preferred_room = room_num
                        logger.info(f"Setting preferred room to {room_num} for next slot.")
                        
                        return True 
            
            time.sleep(0.5) 

        return False

    def start_loop(self):
        logger.info("Scheduler Started.")
        if not self.users or not self.rules:
            logger.error("Configuration missing. Exiting.")
            return

        while True:
            now = datetime.now()
            next_event = None # (open_time, class_time, rule)

            # 1. SCAN: Find nearest future rule (checking ALL sub-slots)
            for rule in self.rules:
                open_time, class_time = TimeUtils.get_next_opening_time(rule, now, BOOKING_DELAY_HOURS)
                
                # If a slot opened very recently (within buffer), we still want to attack it
                if open_time < now - timedelta(seconds=ATTACK_START_BUFFER):
                    # This specific slot missed, look ahead 1 week
                    open_time += timedelta(days=7)
                    class_time += timedelta(days=7)
                    # Re-check biweekly logic
                    if rule.get('biweekly') and not TimeUtils.is_biweekly_match(class_time, rule['anchor_date']):
                        open_time += timedelta(days=7)
                        class_time += timedelta(days=7)

                if next_event is None or open_time < next_event[0]:
                    next_event = (open_time, class_time, rule)

            if not next_event:
                logger.error("No valid future rules found.")
                time.sleep(60)
                continue

            target_open_time, target_class_time, target_rule = next_event
            
            # 2. WAIT
            wait_seconds = (target_open_time - datetime.now()).total_seconds() - ATTACK_START_BUFFER
            
            logger.info(f"NEXT TARGET: {target_rule.get('comment')}")
            logger.info(f"  -> Class Time:   {target_class_time}")
            logger.info(f"  -> Booking Opens:{target_open_time}")
            if self.preferred_room:
                logger.info(f"  -> Preferred Room: {self.preferred_room}")

            if wait_seconds > 0:
                logger.info(f"  -> Sleeping for {wait_seconds/60:.2f} minutes...")
                time.sleep(wait_seconds)
            else:
                logger.info("  -> Target is imminent!")
            
            # 3. PREPARE
            self.prepare_agents()
            if not self.active_agents:
                logger.error("No active agents. Sleeping 30s before retry.")
                time.sleep(30)
                continue

            # 4. ATTACK
            if self.run_attack(target_class_time):
                logger.info("Booking successful. Cooling down for 5 minutes...")
                time.sleep(300) 
            else:
                logger.warning("Attack window closed without success. Rescanning...")
                time.sleep(10)

if __name__ == "__main__":
    bot = BookingScheduler()
    bot.start_loop()