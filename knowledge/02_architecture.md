# System Architecture

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          USER LAYER                              │
├─────────────────────────────────────────────────────────────────┤
│  bookings.json  │  credentials.json  │  Google Calendar Events  │
└────────┬─────────────────┬──────────────────────┬───────────────┘
         │                 │                       │
         ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SCHEDULER (scheduler.py)                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │Calendar Scan │  │Timing Logic  │  │Agent Manager │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         └──────────────────┴──────────────────┘                 │
└────────┬────────────────────────────────────────┬───────────────┘
         │                                        │
         ▼                                        ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│  BOOKING AGENT          │         │  GOOGLE CALENDAR CLIENT      │
│  (booking_agent.py)     │         │  (utils/google_calendar.py)  │
│  ┌──────────────────┐   │         │  ┌──────────────────┐        │
│  │ Session Manager  │   │         │  │ OAuth Handler    │        │
│  │ CSRF Handler     │   │         │  │ Event CRUD       │        │
│  │ API Requests     │   │         │  │ CalendarSync     │        │
│  └──────────────────┘   │         │  └──────────────────┘        │
└────────┬────────────────┘         └────────┬────────────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│  TAU BOOKING SERVER     │         │  GOOGLE CALENDAR API         │
│  schedule.tau.ac.il     │         │  calendar.googleapis.com     │
└─────────────────────────┘         └─────────────────────────────┘
```

## Data Flow

### Flow 1: Proactive Booking (Original Mode)
```
1. Scheduler reads bookings.json
2. Calculates next opening time (7 days - 1 hour)
3. Sleeps until T-60 seconds
4. Initializes all agents (login)
5. Waits until T-5 seconds
6. Attempts booking:
   - Try High Priority rooms first
   - Rotate users on quota/failure
   - Retry on "TOO_EARLY"
7. Log result to system.log
```

### Flow 2: Calendar-Driven Booking
```
1. User creates event in Google Calendar: "Booking: Study Session"
2. Scheduler scans calendar (every cycle)
3. Finds unprocessed event (no [P] prefix)
4. Marks event YELLOW (Processing)
5. Calculates opening time from event start
6. Executes booking attempt
7. Updates event:
   SUCCESS → GREEN + [P] prefix + room metadata
   FAILURE → RED + [P] prefix + error message
```

### Flow 3: Server → Calendar Sync
```
1. CalendarSync.sync_all_users() runs periodically
2. For each user:
   - Login to booking server
   - Fetch events from my-calendar.php (SID 1-5)
   - Filter by className: "mine", "coowner", "participating"
3. Compare with existing Google Calendar events
4. Create missing events with [S] prefix, GREEN color
5. Include User, Room, and Ref in description
```

### Flow 4: DELETE Request Processing
```
1. User adds "DELETE" keyword to event title
2. Scheduler._process_delete_requests() runs every 5 minutes
3. Finds events with DELETE + ([P] or [S] prefix)
4. Extracts reference number from description
5. Calls BookingAgent.delete_booking(ref_num)
6. On success:
   - Remove DELETE keyword
   - Change prefix to [DELETED]
   - Set color to RED
   - Append deletion timestamp
```

## Thread Architecture

### Main Thread
- Runs `Scheduler.run()` infinite loop
- Handles primary booking logic
- Sleeps between booking windows

### Sync Daemon Thread
- Background worker: `_sync_worker()`
- Runs every `SYNC_INTERVAL_SECONDS` (default: 300s)
- Pauses during active booking attempts
- Calls `CalendarSync.sync_all_users()`

### Delete Daemon Thread
- Background worker: `delete_worker()`
- Runs every `DELETE_CHECK_INTERVAL` (default: 300s)
- Scans calendar for DELETE keywords
- Processes deletion requests

### Thread Synchronization
- Uses `threading.Event`: `_booking_in_progress`
- `set()` = no booking (sync/delete can run)
- `clear()` = booking active (pause background tasks)

## Configuration Files

### `config.py`
- `HIGH_PRIORITY_ROOMS`: Dict[int, str] - Room ID → Name
- `LOW_PRIORITY_ROOMS`: Dict[int, str]
- `ALL_ROOMS`: Combined dict
- `CALENDAR_NAME`: Target Google Calendar name
- `CALENDAR_SCAN_DAYS`: Lookahead for event scanning
- `SYNC_INTERVAL_SECONDS`: Sync frequency
- `DELETE_CHECK_INTERVAL`: Delete check frequency
- `DELETE_KEYWORD`: Keyword to trigger deletion
- `CalendarStatus`: Enum for color IDs (5=Yellow, 10=Green, 11=Red)

### `credentials.json`
```json
[
  {
    "email": "user1@example.com",
    "password": "secret",
    "owner_id": "12345"
  }
]
```

### `bookings.json` (Legacy Mode)
```json
[
  {
    "day_of_week": 4,
    "start_hour": 10,
    "end_hour": 12,
    "comment": "Friday Study"
  }
]
```

### `client_secret.json`
Google OAuth2 credentials (download from Google Cloud Console)

### `token.json`
Auto-generated OAuth2 token (persisted after first auth)

## Error Handling Strategy

### BookingResult Enum
- `SUCCESS`: Booking confirmed
- `ROOM_TAKEN`: Conflict detected
- `USER_LIMIT`: Quota exceeded
- `TOO_EARLY`: Window not open yet
- `ERROR`: Network/unknown error

### Retry Logic
- `ROOM_TAKEN` → Try next room
- `USER_LIMIT` → Mark user exhausted, try next user
- `TOO_EARLY` → Sleep 0.5s, retry same room/user
- `ERROR` → Log and continue
- Max retry time: 120 seconds

### Calendar Event States
1. **Unprocessed**: `"Booking: ..."`
2. **Processing**: Yellow, may have `[P]` prefix
3. **Success**: Green, `[P] Booking: ... - Room XXX`
4. **Failure**: Red, `[P] Booking: ...` + error in description
5. **Synced**: Green, `[S] Room XXX ...`
6. **Deleted**: Red, `[DELETED] ...`

## Session Management

### BookingAgent Session Lifecycle
1. `__init__`: Create `requests.Session()`
2. Set default headers (User-Agent, X-Requested-With)
3. `login()`: POST to `index.php`, store cookies
4. `get_csrf_token()`: Scrape from `schedule.php`
5. Reuse session for all API calls
6. Session persists for lifetime of agent instance

### Login Strategy
- Parallel login on startup (`ThreadPoolExecutor`)
- Max 8 concurrent logins
- Only logged-in agents added to active pool
- Re-login before each booking window (safety)

## API Integration Points

### TAU Booking Server
- **Base URL**: `https://schedule.tau.ac.il/scilib/Web`
- **Login**: `POST /index.php`
- **Create Booking**: `POST /api/reservation.php?action=create`
- **Update/Extend**: `POST /api/reservation.php?action=update`
- **Delete**: `POST /api/reservation.php?api=delete`
- **Fetch Events**: `GET /my-calendar.php?dr=events`

### Google Calendar API
- **Service**: `calendar/v3`
- **Authentication**: OAuth2 with offline access
- **Methods Used**:
  - `calendarList().list()` - Find calendar
  - `calendars().insert()` - Create calendar
  - `events().list()` - Scan events
  - `events().insert()` - Create events
  - `events().update()` - Update metadata
  - `events().delete()` - Remove events

## Concurrency & Race Conditions

### Handled Scenarios
- ✅ Multiple users trying same room → First wins, others get ROOM_TAKEN
- ✅ Sync running during booking → Blocked by `_booking_in_progress` flag
- ✅ DELETE during booking → Blocked by same flag
- ✅ Parallel logins → ThreadPoolExecutor manages

### Potential Issues
- ⚠️ User manually edits event while processing → May cause metadata conflicts
- ⚠️ Network timeout during critical booking → Logged as ERROR, retry logic applies
- ⚠️ Google Calendar API rate limits → No explicit handling (relies on reasonable frequency)
