import logging
import sys
import time
from datetime import datetime, timedelta

from booking_agent import BookingAgent
from booking_utils import TimeUtils, ConfigLoader, HistoryManager

# --- CONFIGURATION ---
ROOM_PRIORITY_LIST = [108+7, 109+7, 110+7] #, 13, 14, 15, 16]

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
        self.history = HistoryManager()
        self.active_agents = []
        self.preferred_room = None
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
        # Move preferred room to start of list
        if self.preferred_room and self.preferred_room in ROOM_PRIORITY_LIST:
            ordered = [self.preferred_room] + [r for r in ROOM_PRIORITY_LIST if r != self.preferred_room]
            return ordered
        return ROOM_PRIORITY_LIST

    def run_attack(self, target_class_time):
        start_utc, end_utc = TimeUtils.get_utc_times(target_class_time)
        attack_start = datetime.now()
        
        # We work with copies to allow removing items dynamically during the loop
        valid_rooms = self.get_room_order().copy()
        valid_agents = self.active_agents.copy()
        
        logger.info(f"--- ATTACK STARTED for {target_class_time.strftime('%Y-%m-%d %H:%M')} ---")

        while (datetime.now() - attack_start).total_seconds() < MAX_ATTACK_DURATION:
            
            # Stop if we ran out of options
            if not valid_rooms:
                logger.error("All rooms are marked as Taken/Unavailable.")
                return False
            if not valid_agents:
                logger.error("All users are marked as Limited/Failed.")
                return False

            rooms_to_remove = []

            for room_num in valid_rooms:
                resource_id = room_num + 10 
                room_is_dead = False
                agents_to_remove = []

                for agent in valid_agents:
                    # Attempt Booking
                    status = agent.book_room(resource_id, start_utc, end_utc)
                    
                    if status == "SUCCESS":
                        logger.info(f"VICTORY! Room {room_num} secured by {agent.email}")
                        self.history.add_booking(target_class_time, room_num, agent.email)
                        self.preferred_room = room_num
                        return True 
                    
                    elif status == "ROOM_TAKEN":
                        # Room is unavailable for everyone
                        rooms_to_remove.append(room_num)
                        room_is_dead = True
                        break 
                    
                    elif status == "USER_LIMIT":
                        # User can't book anything anymore today
                        agents_to_remove.append(agent)
                        continue
                
                # Cleanup agents
                for a in agents_to_remove:
                    if a in valid_agents:
                        valid_agents.remove(a)
                
                if not valid_agents:
                    break

                if room_is_dead:
                    continue
            
            # Cleanup rooms
            for r in rooms_to_remove:
                if r in valid_rooms:
                    valid_rooms.remove(r)
            
            time.sleep(0.5) 

        return False

    def start_loop(self):
        logger.info("Scheduler Started.")
        if not self.users or not self.rules:
            logger.error("Configuration missing.")
            return

        while True:
            self.history.clean_history()
            now = datetime.now()
            next_event = None 

            # 1. SCAN
            for rule in self.rules:
                # Expecting 3 values now
                res = TimeUtils.get_next_opening_time(
                    rule, now, self.history, BOOKING_DELAY_HOURS
                )
                if not res: continue
                
                open_time, class_time, is_missed = res
                
                # If it's NOT missed, verify it's close enough to wait for
                if not is_missed and open_time < now - timedelta(seconds=ATTACK_START_BUFFER):
                    continue

                # Create event tuple
                current = (open_time, class_time, rule, is_missed)

                if next_event is None:
                    next_event = current
                else:
                    _, _, _, best_missed = next_event
                    # Prioritize Missed slots over Future slots
                    if is_missed and not best_missed:
                        next_event = current
                    elif (is_missed == best_missed) and (open_time < next_event[0]):
                        next_event = current

            if not next_event:
                logger.info("No unbooked slots found (Missed or Future). Waiting 60s...")
                time.sleep(60)
                continue

            target_open, target_class, target_rule, is_missed = next_event
            
            # 2. LOGIC BRANCH
            if is_missed:
                logger.info(f"FOUND MISSED BOOKING: {target_rule.get('comment')} @ {target_class.strftime('%Y-%m-%d %H:%M')}")
                logger.info("Attempting immediate retry...")
                self.prepare_agents()
                if self.active_agents:
                    # Run logic immediately
                    self.run_attack(target_class)
                else:
                    logger.error("No agents for missed retry.")
                
                # Pause briefly to prevent CPU spinning if it keeps failing
                time.sleep(5)
                continue 

            # Future Booking Logic
            wait_seconds = (target_open - datetime.now()).total_seconds() - ATTACK_START_BUFFER
            logger.info(f"NEXT TARGET: {target_rule.get('comment')}")
            logger.info(f"  -> Class Time:   {target_class.strftime('%Y-%m-%d %H:%M')}")
            logger.info(f"  -> Booking Opens:{target_open.strftime('%Y-%m-%d %H:%M')}")
            
            if wait_seconds > 0:
                logger.info(f"  -> Sleeping for {wait_seconds/60:.2f} minutes...")
                time.sleep(wait_seconds)
            
            self.prepare_agents()
            if self.active_agents:
                if self.run_attack(target_class):
                    logger.info("Booking successful. Cooling down...")
                    time.sleep(300) 
                else:
                    logger.warning("Attack failed. Rescanning...")
                    time.sleep(10)
            else:
                logger.error("No agents available. Sleeping 30s.")
                time.sleep(30)

if __name__ == "__main__":
    bot = BookingScheduler()
    bot.start_loop()