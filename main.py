import json
import logging
import sys
import time
from datetime import datetime, timedelta
from booking_agent import BookingAgent

# --- CONFIGURATION ---
ROOM_PRIORITY_LIST = [13, 14, 15, 16, 17, 18, 19] 
BOOKING_DELAY_HOURS = 2  # Slot opens 2 hours AFTER its start time

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("booking_system.log")
    ]
)
logger = logging.getLogger(__name__)

def get_utc_times(target_datetime):
    """
    Creates UTC timestamps for the slot.
    Logic: Target H -> Send H+1 (API Offset) -> Convert to UTC (-2 for Israel Winter)
    """
    base_dt = target_datetime.replace(minute=0, second=0, microsecond=0)
    
    # 15:00 becomes 16:00 (API Requirement)
    api_start_dt = base_dt + timedelta(hours=1)
    api_end_dt = api_start_dt + timedelta(hours=1)
    
    # Convert to UTC
    start_utc = api_start_dt - timedelta(hours=2)
    end_utc = api_end_dt - timedelta(hours=2)
    
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return start_utc.strftime(fmt), end_utc.strftime(fmt)

def is_biweekly_match(target_date, anchor_str):
    try:
        anchor = datetime.strptime(anchor_str, "%Y-%m-%d")
        anchor = anchor.replace(hour=0, minute=0, second=0)
        target = target_date.replace(hour=0, minute=0, second=0)
        
        delta = target - anchor
        weeks_diff = delta.days // 7
        
        return delta.days >= 0 and (weeks_diff % 2 == 0)
    except Exception as e:
        logger.error(f"Bi-weekly calculation error: {e}")
        return False

def load_json_file(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except Exception:
        return []

def main():
    logger.info("--- Starting Scheduler Cycle ---")

    # 1. Load Data
    users = load_json_file("credentials.json")
    rules = load_json_file("bookings.json")
    
    if not users or not rules:
        logger.error("Missing credentials.json or bookings.json")
        return

    # 2. Pre-login ALL users
    # We do this once per cycle so we don't spam login inside the booking loop
    active_agents = []
    for user in users:
        agent = BookingAgent(user)
        if agent.login():
            active_agents.append(agent)
        else:
            logger.warning(f"User {user.get('email')} failed to login. Skipping.")
            
    if not active_agents:
        logger.error("No active users available. Exiting cycle.")
        return

    # 3. Calculate Target Slot
    now = datetime.now()
    # We want to book 1 week ahead, but the slot opens 'delay' hours after real time.
    target_slot_time = now + timedelta(days=7) - timedelta(hours=BOOKING_DELAY_HOURS)
    
    target_weekday = target_slot_time.weekday()
    target_hour = target_slot_time.hour
    
    logger.info(f"Current Time: {now.strftime('%H:%M')}")
    logger.info(f"Targeting Slot: {target_slot_time.strftime('%Y-%m-%d')} @ {target_hour}:00")

    # 4. Check Rules
    should_book = False
    for rule in rules:
        if rule['day_of_week'] != target_weekday:
            continue
        if not (rule['start_hour'] <= target_hour < rule['end_hour']):
            continue
        if rule.get('biweekly'):
            if not is_biweekly_match(target_slot_time, rule['anchor_date']):
                logger.info(f"Skipping bi-weekly rule (Week mismatch).")
                continue
        
        should_book = True
        logger.info(f"Matched Rule: {rule.get('comment', 'Unnamed Rule')}")
        break

    if not should_book:
        logger.info("No matching rules for this specific hour.")
        return

    # 5. Attempt Booking (Nested Loop: Room -> Users)
    start_utc, end_utc = get_utc_times(target_slot_time)
    
    booked_successfully = False
    
    for room_num in ROOM_PRIORITY_LIST:
        resource_id = room_num + 10
        logger.info(f"Trying Room {room_num}...")

        # Try every user for this room
        for agent in active_agents:
            if agent.book_room(resource_id, start_utc, end_utc):
                logger.info(f"SUCCESS! Booked Room {room_num} with User {agent.email}")
                booked_successfully = True
                break # Break user loop
            else:
                logger.info(f"User {agent.email} failed for Room {room_num}. Trying next user...")
        
        if booked_successfully:
            break # Break room loop

    if not booked_successfully:
        logger.error("All rooms and users failed for this slot.")

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            logger.error(f"Critical Error in main loop: {e}")
        
        # Wait 30 seconds before retrying/polling
        time.sleep(30)