import logging
import sys
import time
from datetime import datetime, timedelta

from booking_agent import BookingAgent
from booking_utils import TimeUtils, ConfigLoader, HistoryManager

# --- CONFIGURATION ---

ROOM_PRIORITY_LIST = [108+7, 109+7, 110+7] #, 13, 14, 15, 16, 17, 18, 19]

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
        self.history = HistoryManager() # Initialize History
        self.active_agents = []
        self.preferred_room = None

        # Clean old bookings on startup
        self.history.clean_history()

    def prepare_agents(self):
        self.active_agents = []
        if not self.users: return
        
        logger.info("--- Preparing Agents ---")
        for u in self.users:
            agent = BookingAgent(u)
            if agent.login():
                self.active_agents.append(agent)
        
        logger.info(f"Agents Ready: {len(self.active_agents)}")

    def get_room_order(self):
        if self.preferred_room and self.preferred_room in ROOM_PRIORITY_LIST:
            ordered = [self.preferred_room] + [r for r in ROOM_PRIORITY_LIST if r != self.preferred_room]
            return ordered
        return ROOM_PRIORITY_LIST

    def run_attack(self, target_class_time):
        start_utc, end_utc = TimeUtils.get_utc_times(target_class_time)
        attack_start = datetime.now()
        current_room_list = self.get_room_order()
        
        logger.info(f"--- ATTACK STARTED for {target_class_time.strftime('%H:%M')} ---")

        while (datetime.now() - attack_start).total_seconds() < MAX_ATTACK_DURATION:
            for room_num in current_room_list:
                resource_id = room_num + 10 
                
                for agent in self.active_agents:
                    if agent.book_room(resource_id, start_utc, end_utc):
                        logger.info(f"VICTORY! Room {room_num} secured by {agent.email}")
                        
                        # --- SAVE TO HISTORY ---
                        self.history.add_booking(target_class_time, room_num, agent.email)
                        
                        self.preferred_room = room_num
                        return True 
            
            time.sleep(0.5) 

        return False

    def start_loop(self):
        logger.info("Scheduler Started.")
        if not self.users or not self.rules:
            logger.error("Configuration missing.")
            return

        while True:
            # Clean history periodically (e.g., start of every loop)
            self.history.clean_history()
            
            now = datetime.now()
            next_event = None 

            # 1. SCAN
            for rule in self.rules:
                # Pass 'self.history' so we skip slots that are done
                open_time, class_time = TimeUtils.get_next_opening_time(
                    rule, now, self.history, BOOKING_DELAY_HOURS
                )
                
                if not open_time:
                    continue

                if open_time < now - timedelta(seconds=ATTACK_START_BUFFER):
                    # Should have been caught by history check or scanner logic,
                    # but safety check to avoid infinite loops on missed slots
                    continue

                if next_event is None or open_time < next_event[0]:
                    next_event = (open_time, class_time, rule)

            if not next_event:
                logger.info("No unbooked future slots found. Waiting 60s...")
                time.sleep(60)
                continue

            target_open_time, target_class_time, target_rule = next_event
            
            # 2. WAIT
            wait_seconds = (target_open_time - datetime.now()).total_seconds() - ATTACK_START_BUFFER
            
            logger.info(f"NEXT TARGET: {target_rule.get('comment')}")
            logger.info(f"  -> Class Time:   {target_class_time}")
            logger.info(f"  -> Booking Opens:{target_open_time}")
            
            if wait_seconds > 0:
                logger.info(f"  -> Sleeping for {wait_seconds/60:.2f} minutes...")
                time.sleep(wait_seconds)
            
            # 3. ATTACK
            self.prepare_agents()
            if self.active_agents:
                if self.run_attack(target_class_time):
                    logger.info("Booking successful. Cooling down...")
                    time.sleep(300) 
                else:
                    logger.warning("Attack failed. Rescanning...")
                    time.sleep(10)
            else:
                logger.error("No agents available. Sleeping 30s.")
                time.sleep(30)

if __name__ == "__main__":
    with open ("gaga", "a") as f:
        f.write("start\n")
    bot = BookingScheduler()
    bot.start_loop()