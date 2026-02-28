import logging
import os

# --- Configuration ---
CREDENTIALS_FILE = "credentials.json"

LOG_FILE = "system.log"

# Google Calendar
GOOGLE_CALENDAR_CREDENTIALS = "client_secret.json"
GOOGLE_CALENDAR_TOKEN = "token.json"
GOOGLE_CALENDAR_ID = 'primary'
CALENDAR_NAME = "Library Bookings"
CALENDAR_SCAN_DAYS = 8

CALENDAR_POLL_INTERVAL_SECONDS = 300 # 5 minutes

# Booking constraints
MAX_SINGLE_BOOKING_DURATION_HOURS = 3.0

# Timing constraints
LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING = 60
FINAL_WAKE_UP_SECONDS_BEFORE_OPENING = 5
BOOKING_RETRY_INTERVAL_SECONDS = 0.5
BOOKING_TIMEOUT_SECONDS = 120
MAX_SINGLE_BOOKING_DURATION_HOURS = 3

# Delete Feature
DELETE_KEYWORD = "DELETE"  # Keyword to search for in event titles (case-insensitive)
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
    26: "Room 16",
    27: "Room 17",
    28: "Room 18",
    29: "Room 19",
}

# Combined for lookup
ALL_ROOMS = {**HIGH_PRIORITY_ROOMS, **LOW_PRIORITY_ROOMS}

from utils.logger import setup_colored_logging
from dashboard.logger import DashboardHandler
import logging

def setup_logging():
    # 1. Setup Console/File logging
    root = setup_colored_logging(LOG_FILE)
    
    # 2. Attach Dashboard Handler
    dash_handler = DashboardHandler()
    dash_handler.setLevel(logging.DEBUG) 
    root.addHandler(dash_handler)

    # Silence libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("oauth2client").setLevel(logging.WARNING)