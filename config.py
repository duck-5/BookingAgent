import logging
import os

# --- Configuration ---
CREDENTIALS_FILE = "credentials.json"
BOOKINGS_FILE = "bookings.json"
HISTORY_FILE = "booking_history.json"
LOG_FILE = "system.log"

# Google Calendar
GOOGLE_CALENDAR_CREDENTIALS = "client_secret.json"
GOOGLE_CALENDAR_TOKEN = "token.json"
GOOGLE_CALENDAR_ID = 'primary'
CALENDAR_NAME = "Library Bookings"
CALENDAR_SCAN_DAYS = 8

SYNC_INTERVAL_SECONDS = 30  # 1 hour

# Delete Feature
DELETE_KEYWORD = "DELETE"  # Keyword to search for in event titles (case-insensitive)
DELETE_CHECK_INTERVAL = 30  # 5 minutes

# Calendar Status / Colors
class CalendarStatus:
    PROCESSING = '5'   # Yellow
    SUCCESS = '10'     # Green
    SYNCED = '10'      # Green (same as SUCCESS)
    FAILURE = '11'     # Red
    DELETED = '11'     # Red (same as FAILURE)


# High Priority: The preferred rooms (e.g., Ground Floor / New Wing)
HIGH_PRIORITY_ROOMS = {
    125: "Room 108",
    126: "Room 109",
    127: "Room 110",
    128: "Room 111"
}

# Low Priority: Fallback rooms (e.g., Upper floors / Old Wing)
LOW_PRIORITY_ROOMS = {
    23: "Room 13",
    24: "Room 14",
    25: "Room 15",
    26: "Room 16"
}

# Combined for lookup
ALL_ROOMS = {**HIGH_PRIORITY_ROOMS, **LOW_PRIORITY_ROOMS}

def setup_logging():
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.DEBUG)
    
    file_handler = logging.FileHandler(LOG_FILE, mode='a')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    
    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

    # Silence libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)