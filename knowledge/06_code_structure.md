# Code Structure

## File Organization

```
booking_agent/
├── main.py                 # Entry point
├── scheduler.py            # Core scheduling and booking logic
├── booking_agent.py        # HTTP client for booking server
├── config.py               # Configuration and constants
├── credentials.json        # User accounts
├── bookings.json           # Desired bookings (legacy mode)
├── client_secret.json      # Google OAuth credentials
├── token.json              # Google OAuth token (auto-generated)
├── system.log              # Runtime logs
├── utils/
│   ├── __init__.py
│   └── google_calendar.py  # Google Calendar integration
├── docs/
│   ├── booking_rules.md
│   ├── google_calendar_integration.md
│   └── server_api.md
└── knowledge/              # AI agent documentation (this directory)
    ├── 01_overview.md
    ├── 02_architecture.md
    ├── 03_booking_rules.md
    ├── 04_google_calendar_integration.md
    ├── 05_server_api.md
    └── 06_code_structure.md
```

## Core Classes

### Scheduler (`scheduler.py`)
Main orchestrator class.

**Key Methods**:
- `run()` - Main infinite loop
- `get_next_target_slot()` - Find next booking to attempt
- `attempt_booking()` - Execute booking with retry logic
- `initialize_agents()` - Parallel login for all users
- `_fetch_calendar_bookings()` - Scan Google Calendar
- `_update_calendar_event()` - Update event status/metadata
- `_split_event_after_booking()` - Handle multi-hour events
- `_merge_consecutive_bookings()` - Merge adjacent bookings
- `_process_delete_requests()` - Handle DELETE keyword
- `_sync_worker()` - Background sync thread
- `_booking_in_progress` - Threading.Event for coordination

### BookingAgent (`booking_agent.py`)
HTTP client for TAU booking server.

**Key Methods**:
- `login()` - Authenticate user
- `get_csrf_token()` - Extract CSRF from schedule page
- `book_room(rid, start_utc, end_utc)` - Create booking
- `extend_booking(rid, ref_num, start, end)` - Update booking
- `delete_booking(ref_num)` - Delete booking
- `get_user_bookings(start, end)` - Fetch user's bookings (SID 1-5)
- `_send_reservation_request()` - Unified request handler

**Enum**: `BookingResult` - SUCCESS, ROOM_TAKEN, USER_LIMIT, TOO_EARLY, ERROR

### GoogleCalendarClient (`utils/google_calendar.py`)
Low-level Google Calendar API wrapper.

**Key Methods**:
- `authenticate()` - OAuth2 flow
- `list_calendars()` - Get all calendars
- `get_or_create_calendar(name)` - Find or create calendar
- `add_event(summary, start, end, ...)` - Create event

### CalendarSync (`utils/google_calendar.py`)
High-level sync utility.

**Key Methods**:
- `sync_all_users()` - Main sync method (server → calendar)
- Fetches bookings from all users (SID 1-5)
- Creates [S] events for missing bookings
- Enhanced duplicate detection (time + room + user)

## Configuration (`config.py`)

```python
# Room Mappings
HIGH_PRIORITY_ROOMS = {125: "Room 108", ...}
LOW_PRIORITY_ROOMS = {23: "Room 13", ...}
ALL_ROOMS = {**HIGH_PRIORITY_ROOMS, **LOW_PRIORITY_ROOMS}

# Google Calendar
CALENDAR_NAME = "Library Bookings"
CALENDAR_SCAN_DAYS = 8
SYNC_INTERVAL_SECONDS = 300
DELETE_CHECK_INTERVAL = 300
DELETE_KEYWORD = "DELETE"

# Credentials
CREDENTIALS_FILE = "credentials.json"
GOOGLE_CALENDAR_CREDENTIALS = "client_secret.json"
GOOGLE_CALENDAR_TOKEN = "token.json"

# Calendar Status Colors
class CalendarStatus:
    PROCESSING = "5"  # Yellow
    SUCCESS = "10"    # Green
    SYNCED = "10"     # Green
    FAILURE = "11"    # Red
    DELETED = "11"    # Red
```

## Data Flow Patterns

### Pattern 1: User → Calendar → Booking
1. User creates event in Google Calendar
2. Scheduler scans calendar every cycle
3. Finds "Booking" events without [P] prefix
4. Calculates opening time, waits
5. Executes booking attempt → Updates event

### Pattern 2: Server → Calendar (Sync)
1. Background thread runs every 5 minutes
2. CalendarSync fetches from all users (SID 1-5)
3. Compares with existing calendar events
4. Creates [S] events for missing bookings

### Pattern 3: Calendar → Server (Delete)
1. User adds DELETE to event title
2. Background thread scans every 5 minutes
3. Extracts reference number from description
4. Calls delete API → Updates event to [DELETED]

## Threading Model

1. **Main Thread**: Runs `Scheduler.run()` infinite loop
2. **Sync Daemon**: Periodic server→calendar sync
3. **Delete Daemon**: Periodic DELETE request processing

Coordination via `_booking_in_progress` Event (pauses background tasks during booking).

## Logging Strategy

```python
import logging
logger = logging.getLogger(__name__)

# Usage
logger.info("Normal flow events")
logger.warning("Unusual but handled situations")
logger.error("Failures requiring attention")
logger.debug("Detailed debugging info")
```

Log outputs to `system.log` and console.

## Key Design Patterns

### Session Management
- One `requests.Session()` per BookingAgent instance
- Cookies persist automatically
- Headers set once on initialization

### Retry Logic
- Max 120 seconds per booking attempt
- 0.5s sleep on TOO_EARLY
- Immediate switch on ROOM_TAKEN or USER_LIMIT

### Concurrency
- Parallel login: `ThreadPoolExecutor(max_workers=8)`
- Background tasks: Daemon threads
- Synchronization: `threading.Event`

### Error Handling
- Enum-based result types
- Substring matching for server errors
- Try/except around all external calls

## Entry Point (`main.py`)

```python
import logging
from scheduler import Scheduler

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("system.log"),
            logging.StreamHandler()
        ]
    )
    
    scheduler = Scheduler()
    scheduler.initialize_agents()
    scheduler.run()
```

## Dependencies

```
requests
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
```

Install via: `pip install requests google-api-python-client google-auth-httplib2 google-auth-oauthlib`
