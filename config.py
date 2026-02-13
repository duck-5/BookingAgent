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

# High Priority: The preferred rooms (e.g., Ground Floor / New Wing)
HIGH_PRIORITY_ROOMS = {
    23: "Room 13",
    24: "Room 14",
    25: "Room 15",
    26: "Room 16"
}

# Low Priority: Fallback rooms (e.g., Upper floors / Old Wing)
LOW_PRIORITY_ROOMS = {
    125: "Room 108",
    126: "Room 109",
    127: "Room 110",
    128: "Room 111"
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