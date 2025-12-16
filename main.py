import json
import logging
import sys
from datetime import datetime, timedelta
from booking_agent import BookingAgent

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("booking_system.log")
    ]
)
logger = logging.getLogger(__name__)

def calculate_utc_times(date_str, start_time_str, end_time_str):
    try:
        start_dt = datetime.strptime(f"{date_str} {start_time_str}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{date_str} {end_time_str}", "%Y-%m-%d %H:%M")
        
        # Israel Winter Time is UTC+2
        start_utc = start_dt - timedelta(hours=2)
        end_utc = end_dt - timedelta(hours=2)
        
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        return start_utc.strftime(fmt), end_utc.strftime(fmt)
    except ValueError as e:
        logger.error(f"Date format error: {e}")
        return None, None

def load_json_file(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading {filename}: {e}")
        return []

def main():
    logger.info("Starting Booking System...")

    users = load_json_file("credentials.json")
    bookings = load_json_file("bookings.json")

    if not users or not bookings:
        logger.error("Missing data files. Exiting.")
        return

    user_map = {u['email']: u for u in users}

    for booking in bookings:
        user_email = booking.get('user_email')
        
        if user_email not in user_map:
            logger.warning(f"Skipping: User {user_email} not found in credentials.")
            continue
            
        user_data = user_map[user_email]
        room_num = booking.get('room_number', 0)
        resource_id = room_num + 10 
        
        start_utc, end_utc = calculate_utc_times(
            booking['date'], 
            booking['start_time'], 
            booking['end_time']
        )
        
        if not start_utc:
            continue

        agent = BookingAgent(user_data)
        if agent.login():
            agent.book_room(resource_id, start_utc, end_utc)
            
    logger.info("All tasks finished.")

if __name__ == "__main__":
    main()