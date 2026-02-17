# System Architecture

## Component Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                          USER LAYER                              │
├─────────────────────────────────────────────────────────────────┤
│    dashboard    │  credentials.json  │  Google Calendar Events   │
└────────┬─────────────────┬──────────────────────┬───────────────┘
         │                 │                       │
         ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SCHEDULER (scheduler.py)                   │
│                     (Service Orchestrator)                      │
└─────┬──────────────────────────┬───────────────────────────┬────┘
      │                          │                           │
      ▼                          ▼                           ▼
┌─────────────┐        ┌──────────────────┐        ┌────────────────┐
│AgentManager │        │ BookingManager   │        │CalendarManager │
│(Pool)       │        │ (Logic & Retry)  │        │(Scanner)       │
└─────┬───────┘        └─────────┬────────┘        └──────┬─────────┘
      │                          │                        │
      ▼                          ▼                        ▼
┌─────────────┐        ┌──────────────────┐        ┌────────────────┐
│ TauClient   │        │ TauClient        │        │ GoogleCalClient│
│ (Login)     │        │ (Book/Delete)    │        │ (API)          │
└─────┬───────┘        └─────────┬────────┘        └──────┬─────────┘
      │                          │                        │
      ▼                          ▼                        ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│  TAU BOOKING SERVER     │         │  GOOGLE CALENDAR API         │
│  schedule.tau.ac.il     │         │  calendar.googleapis.com     │
└─────────────────────────┘         └─────────────────────────────┘
```

## Data Flow

### Flow 1: Calendar-Driven Booking
```
1. User creates event in Google Calendar: "Booking: Study Session"
2. Scheduler._scan_loop triggers CalendarManager.scan_for_bookings()
3. CalendarManager parses events and returns BookingRequest objects
4. Scheduler calls BookingManager.attempt_booking(request)
5. BookingManager:
   - Selects Agent from AgentManager
   - Tries High/Low priority rooms
   - Connects via TauClient to book
6. CalendarManager.update_event_status():
   SUCCESS → GREEN + [P] prefix + room metadata
   FAILURE → RED + [P] prefix + error message
```

### Flow 2: Server → Calendar Sync
```
1. Scheduler._sync_worker runs periodically
2. Calls CalendarSync.sync_all_users()
3. For each user (via TauClient):
   - Fetch events (SID 1-5)
4. Compare with Google Calendar
5. Create [S] events for missing items
```

### Flow 3: DELETE Request Processing
```
1. User adds "DELETE" keyword to event title
2. Scheduler._sync_worker triggers CalendarManager.scan_for_deletions()
3. Returns DeletionRequest objects
4. Scheduler calls BookingManager.cancel_booking(request)
   - Finds owner's agent
   - Calls TauClient.delete_booking()
5. CalendarManager updates event to [DELETED] (Gray)
```

## Thread Architecture

### Main Thread
-   Runs `Scheduler.run()` -> `_scan_loop()`
-   Orchestrates scanning and booking via Managers

### Sync Daemon Thread
-   Background worker: `_sync_worker()`
-   Runs Sync logic and Delete logic sequentially
-   Pauses when `_booking_in_progress` is set

### Dashboard Thread (New)
-   Runs `uvicorn` (FastAPI)
-   Serves monitoring UI at `http://localhost:8000`

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
