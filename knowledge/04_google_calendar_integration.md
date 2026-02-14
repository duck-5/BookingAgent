# Google Calendar Integration

## Overview
The booking agent features **bidirectional** Google Calendar integration:
1. **Incoming**: Calendar events → Booking requests
2. **Outgoing**: Server bookings → Calendar events (sync)

## Calendar Configuration

### Target Calendar
- **Name**: `"Library Bookings"` (configurable in `config.CALENDAR_NAME`)
- **Time Zone**: `Asia/Jerusalem`
- **Creation**: Auto-created if doesn't exist on first run

### Authentication
- **Method**: OAuth2 with offline access
- **Credentials File**: `client_secret.json` (download from Google Cloud Console)
- **Token File**: `token.json` (auto-generated after first auth)
- **Scopes**: `https://www.googleapis.com/auth/calendar` (full access)

### First-Time Setup
1. Place `client_secret.json` in project root
2. Run scheduler → OAuth flow opens in browser
3. User grants permissions
4. Token saved to `token.json` for future use
5. Token auto-refreshes when expired

## Incoming: Calendar → Booking System

### Event Format (User Creates)
```
Summary: Booking: Study Session
Start: 2026-02-21 10:00
End: 2026-02-21 12:00
Description: (optional notes)
Color: Default (not set)
```

### Recognition Logic
Event is **valid booking request** if:
- ✅ Summary starts with `"Booking"`
- ✅ Does NOT start with `"[P]"` (already processed)
- ✅ Does NOT start with `"[S]"` (server-synced booking)
- ✅ Start time is in the future

### Processing Workflow

#### Step 1: Detection
- `Scheduler._fetch_calendar_bookings()` scans calendar
- Range: Now to +`CALENDAR_SCAN_DAYS` (default 8 days)
- Filters events by summary prefix

#### Step 2: Opening Time Calculation
```python
opening_time = event_start - timedelta(days=7) + timedelta(hours=1)
```
Example:
- Event: Friday 10:00 AM
- Opening: Previous Friday 11:00 AM

#### Step 3: Waiting
If opening time > 60 seconds away:
- Sleep until T-60s
- Re-login all agents
- Sleep until T-5s

#### Step 4: Processing State (Yellow)
```python
self._update_calendar_event(event_id, 'PROCESSING', original_event)
```
- Color changes to **Yellow (5)**
- Title remains unchanged (or `[P]` prefix added)
- User sees visual feedback that booking started

#### Step 5: Booking Attempt
Execute normal booking logic:
- Try high-priority rooms first
- Rotate users
- Retry on `TOO_EARLY`

#### Step 6A: Success (Green)
```python
self._update_calendar_event(
    event_id, 
    'SUCCESS', 
    original_event,
    room_name="Room 108",
    ref_num="BA3382D5",
    user="user@example.com"
)
```

**Event Updated**:
```
Summary: [P] Booking: Study Session - Room 108
Color: Green (10)
Location: Room 108
Description:
  <original description>
  
  Booked for User: user@example.com
  Room: Room 108
  Ref: BA3382D5
```

#### Step 6B: Failure (Red)
```python
self._update_calendar_event(
    event_id, 
    'FAILURE', 
    original_event,
    reason="All Users Quotas Exhausted"
)
```

**Event Updated**:
```
Summary: [P] Booking: Study Session
Color: Red (11)
Description:
  <original description>
  
  Error: All Users Quotas Exhausted
```

### Multi-Hour Event Handling

#### Single-Hour Booking
If event is 1 hour:
- Update event in-place
- Attempt merge with adjacent bookings

#### Multi-Hour Booking (Partial Success)
If event is >1 hour (e.g., 10:00-13:00) but only 1 hour booked:

**Before**:
```
Event: Booking: Study Session
Start: 10:00
End: 13:00
Color: Yellow (processing)
```

**After** (first hour booked):
```
Event 1 (NEW): [P] Booking: Study Session - Room 108
Start: 10:00
End: 11:00
Color: Green

Event 2 (UPDATED): Booking: Study Session
Start: 11:00
End: 13:00
Color: Default (ready to process again)
```

System will retry Event 2 on next scan cycle.

### Event Merging
After successful booking, system searches for adjacent bookings:
- Same room name (in summary)
- Same user (in description)
- Adjacent times (within 60s tolerance)

**Example**:
```
Before:
- Event A: [P] Room 108, 10:00-11:00, user@example.com
- Event B: [P] Room 108, 11:00-12:00, user@example.com (just booked)
- Event C: [P] Room 108, 12:00-13:00, user@example.com

After Merge:
- Event B: [P] Room 108, 10:00-13:00, user@example.com
- Event A & C: DELETED
```

## Outgoing: Server → Calendar Sync

### Purpose
Import existing server bookings into Google Calendar for visibility.

### Trigger
- **Startup**: Immediate sync on scheduler start
- **Periodic**: Every `SYNC_INTERVAL_SECONDS` (default 300s)
- **Daemon Thread**: Runs in background
- **Paused During**: Active booking attempts

### Sync Workflow

#### Step 1: Fetch Server Bookings
For each user in `credentials.json`:
```python
for sid in range(1, 6):
    url = f"{BASE_URL}/my-calendar.php?dr=events&start={date}&end={date}&sid={sid}"
    events = agent.session.get(url).json()
```

**Important**: Must loop through SID 1-5 to find all bookings.

#### Step 2: Filter Relevant Events
```python
if any(cls in evt['className'] for cls in ["mine", "coowner", "participating"]):
    # Include this booking
```

Only include bookings where user is:
- Owner ("mine")
- Co-owner ("coowner")
- Participant ("participating")

#### Step 3: Fetch Existing Google Events
Query Google Calendar for events in sync range:
- Start: Now - 7 days
- End: Now + 30 days

#### Step 4: Duplicate Detection
For each server booking, check if it already exists in calendar:
```python
# Must match ALL:
- Start time matches
- Room name matches (in description: "Room: XXX")
- User email matches (in description: "User: xxx")
```

If match found → Skip (avoid duplicates)

#### Step 5: Create Missing Events
For each unique server booking:
```python
synced_summary = f"[S] {original_title} - {room_name}"

description = f"""User: {owner_email}
Room: {room_name}
Ref: {booking_id}
Synced from Booking System"""

add_event(
    summary=synced_summary,
    start_time=start,
    end_time=end,
    location=room_name,
    color_id=CalendarStatus.SYNCED,  # Green (10)
    description=description
)
```

**Example Synced Event**:
```
Summary: [S] Room 015 Study - Room 015
Start: 2026-02-19 20:00
End: 2026-02-19 21:00
Color: Green (10)
Location: Room 015
Description:
  User: user@example.com
  Room: Room 015
  Ref: C827789D
  Synced from Booking System
```

### Sync vs Processed Events

| Aspect | [P] Processed | [S] Synced |
|--------|--------------|------------|
| Origin | Calendar → Server | Server → Calendar |
| User Action | Created event manually | System imported |
| Color | Green (success) / Red (fail) | Always Green |
| Reference | From booking response | From `my-calendar.php` ID |
| Editable | User can delete/modify | Read-only import |

Both formats include same metadata structure for consistency.

## DELETE Feature

### User Workflow
1. Find event in Google Calendar (must have `[P]` or `[S]` prefix)
2. Edit event title, add keyword: `DELETE`
   - Example: `DELETE [P] Booking: Study Session - Room 108`
3. Save event
4. Wait for next delete check cycle (default 5 minutes)
5. Event updated to `[DELETED]` with timestamp

### Detection Logic
```python
if 'DELETE' in summary.upper():
    if '[P]' in summary or '[S]' in summary:
        if '[DELETED]' not in summary:
            # Process deletion
```

### Processing Workflow

#### Step 1: Extract Reference Number
```python
ref_match = re.search(r'Ref:\s*([A-Z0-9]+)', description)
ref_num = ref_match.group(1)  # e.g., "BA3382D5"
```

If no reference found → Mark RED with error.

#### Step 2: Extract Owner Email
```python
owner_match = re.search(r'(?:Booked for )?User:\s*([^\s\n]+)', description)
owner_email = owner_match.group(1)
```

#### Step 3: Attempt Deletion
Try owner's agent first (if found), otherwise try all agents:
```python
deleted = agent.delete_booking(ref_num)
```

**Server Request**:
```
POST /api/reservation.php?api=delete
Content-Type: application/json

{
  "referenceNumber": "BA3382D5",
  "scope": "full",
  "reason": "",
  "browserTimezone": "Asia/Jerusalem"
}
```

#### Step 4A: Success
Update event:
```python
new_summary = summary.replace('[P]', '[DELETED]').replace('[S]', '[DELETED]')
new_summary = new_summary.replace('DELETE', '').strip()

updated_event = {
    'summary': new_summary,
    'colorId': '11',  # Red
    'description': f"{original_desc}\n\nDeleted on: {timestamp}"
}
```

**Result**:
```
Summary: [DELETED] Booking: Study Session - Room 108
Color: Red (11)
Description:
  <original description>
  
  Deleted on: 2026-02-13 23:00:00
```

#### Step 4B: Failure
Update event with error:
```python
updated_event = {
    'colorId': '11',  # Red
    'description': f"{original_desc}\n\nError: DELETE failed - Permission denied or server error"
}
```

### Edge Cases

**No reference number**:
- Skip event
- Add error to description
- Reason: `"DELETE failed - No reference number found"`

**Unprocessed event** (no `[P]` or `[S]`):
- Skip event
- Log warning
- User must have processed booking to delete

**Already deleted** (`[DELETED]` prefix):
- Skip event
- Prevents duplicate delete API calls

**Permission denied**:
- Owner agent not found → Try all agents
- All agents fail → Mark RED with error
- Possible if booking was made by account not in pool

## Google Calendar Color IDs

| ID | Name | Hex | Use Case |
|----|------|-----|----------|
| **5** | Yellow | #fbd75b | Processing (booking in progress) |
| **10** | Green | #51b749 | Success (booked) / Synced |
| **11** | Red | #dc2127 | Failure / Deleted |

Set via:
```python
event['colorId'] = '10'  # Must be string
```

## Utilities & Scripts

### `utils/google_calendar.py`
**GoogleCalendarClient**:
- `authenticate()` - OAuth2 flow
- `list_calendars()` - List all calendars
- `get_or_create_calendar(name)` - Find or create calendar
- `add_event(...)` - Create new event

**CalendarSync**:
- `sync_all_users()` - Main sync method
- Instantiated in `Scheduler.__init__()`
- Called by `_sync_worker()` thread

### Scheduler Methods
- `_fetch_calendar_bookings()` - Scan for booking events
- `_update_calendar_event()` - Update event metadata
- `_delete_event()` - Remove event
- `_create_event_for_slot()` - Create new event
- `_split_event_after_booking()` - Handle multi-hour events
- `_merge_consecutive_bookings()` - Merge adjacent bookings
- `_process_delete_requests()` - DELETE keyword handler

## Configuration Constants

```python
# config.py
CALENDAR_NAME = "Library Bookings"
CALENDAR_SCAN_DAYS = 8  # Lookahead for event scanning
SYNC_INTERVAL_SECONDS = 300  # 5 minutes
DELETE_CHECK_INTERVAL = 300  # 5 minutes
DELETE_KEYWORD = "DELETE"

class CalendarStatus:
    PROCESSING = "5"  # Yellow
    SUCCESS = "10"    # Green
    SYNCED = "10"     # Green (same as success)
    FAILURE = "11"    # Red
    DELETED = "11"    # Red
```

## Thread Synchronization

### Booking vs Background Tasks
```python
self._booking_in_progress = threading.Event()
```

**During Booking**:
```python
self._booking_in_progress.clear()  # Pause sync/delete
try:
    # Attempt booking
finally:
    self._booking_in_progress.set()  # Resume sync/delete
```

**Background Threads**:
```python
while True:
    time.sleep(interval)
    self._booking_in_progress.wait()  # Block if booking active
    # Run sync or delete
```

Ensures:
- No sync API calls during critical booking window
- No delete processing during booking
- Clean separation of concerns

## Error Handling

### Calendar API Errors
- Network failures → Logged, next cycle retries
- Rate limits → Not explicitly handled (relies on reasonable frequency)
- Invalid events → Skipped, logged

### Token Expiry
```python
if self.creds.expired and self.creds.refresh_token:
    self.creds.refresh(Request())
```
Auto-refresh on expiry. If refresh fails → Force re-login.

### Missing Calendar
`get_or_create_calendar()` handles:
1. Search all calendars
2. If not found → Create new
3. Return calendar ID

### Malformed Events
- Missing start/end times → Skip
- Invalid ISO format → Try fallback parsing
- Missing description → Use empty string
