# Booking Rules & Business Logic

## Booking Window Rule

### Core Formula
```
Opening Time = Target Slot Time - 7 Days + 1 Hour
```

### Examples
| Target Slot | Opens At |
|-------------|----------|
| Friday 10:00 AM | Previous Friday 11:00 AM |
| Monday 14:00 | Previous Monday 15:00 |
| Sunday 08:00 | Previous Sunday 09:00 |

### Rationale
The university library system releases slots exactly 7 days in advance, but with a 1-hour positive offset. This is the critical timing constraint that drives the entire sniping strategy.

## Reservation Constraints

### Duration
- Bookings are made in **1-hour intervals**
- Multi-hour requests require multiple bookings or extension
- Calendar events can span multiple hours (system handles splitting)

### User Quotas
Each user account has limits:
- **Daily limit**: Varies by user type (not explicitly defined in code)
- **Weekly limit**: Similar constraints
- System detects limit via server error: `"limited"` in response

### Quota Strategy
- Use multiple accounts collectively
- Rotate users automatically
- Exhaust all users before giving up
- `BookingResult.USER_LIMIT` triggers user rotation

### Consecutive Booking Strategy
- **Behavior**: The system treats each hour as a separate booking attempt.
- **Handling**: If multiple hours are requested via one Calendar event, the system (via `scheduler.py` and `calendar_manager.py`) detects successful partial bookings and splits the calendar event accordingly.
- **Legacy**: Older versions supported "extending" a single reservation ID, but this is currently replaced by the robustness of individual slot booking.

## Room Priority Hierarchy

### High Priority Rooms (Tier 1)
```python
HIGH_PRIORITY_ROOMS = {
    125: "Room 108",
    126: "Room 109",
    127: "Room 110",
    128: "Room 111"
}
```
- Preferred locations (likely better facilities, quiet, etc.)
- Always attempted first
- System exhausts all high-priority before moving to low

### Low Priority Rooms (Tier 2)
```python
LOW_PRIORITY_ROOMS = {
    23: "Room 13",
    24: "Room 14",
    25: "Room 15",
    26: "Room 16"
}
```
- Fallback options
- Only used if all high-priority rooms taken

### Booking Attempt Order
```
For each room batch (HIGH, then LOW):
  For each room in batch:
    For each agent (prioritize last_successful_user):
      Attempt booking
      If SUCCESS → Done
      If ROOM_TAKEN → Next room
      If USER_LIMIT → Next agent
      If TOO_EARLY → Retry same room/agent
```

## User Account Rotation

### Priority Logic
1. **Last Successful User**: Moved to front of queue
   - Maintains session continuity
   - Reduces login overhead
   - Likely has active cookies
   
2. **Remaining Users**: Rotated in order

### Exhaustion Detection
- Track `exhausted_emails` set during booking attempt
- When user hits quota: `exhausted_emails.add(agent.email)`
- Skip exhausted users in subsequent attempts
- If `len(exhausted_emails) == len(agents)` → Give up

### User State Management
```python
self.agents = []  # Active logged-in agents
self.last_successful_user = None  # Email of last success
```

## Google Calendar Event Rules

### Event Recognition (Incoming)
**Valid Booking Event**:
- Summary starts with: `"Booking"`
- Does NOT start with: `"[P]"` (already processed)
- Does NOT start with: `"[S]"` (server-synced, ignore)

**DELETE Request**:
- Summary contains: `"DELETE"` (case-insensitive)
- Must have prefix: `"[P]"` or `"[S]"` (only delete processed bookings)
- Must NOT have: `"[DELETED]"` (already deleted)

### Event Metadata (Outgoing)

**SUCCESS**:
```
Summary: [P] Booking: Study Session - Room 108
Color: Green (10)
Location: Room 108
Description:
  Original content...
  
  Booked for User: user@example.com
  Room: Room 108
  Ref: BA3382D5
```

**FAILURE**:
```
Summary: [P] Booking: Study Session
Color: Red (11)
Description:
  Original content...
  
  Error: All Users Quotas Exhausted
```

**SYNCED** (from server):
```
Summary: [S] Room 015 Study - Room 015
Color: Green (10)
Location: Room 015
Description:
  User: user@example.com
  Room: Room 015
  Ref: C827789D
  Synced from Booking System
```

**DELETED**:
```
Summary: [DELETED] Booking: Study Session - Room 108
Color: Red (11)
Description:
  Original content...
  
  Deleted on: 2026-02-13 23:00:00
```

## Multi-Hour Event Handling

### Event Splitting
When a multi-hour event is partially booked:

**Scenario**: Event is 10:00-13:00, only 10:00-11:00 booked

**Result**:
1. Create new event: `[P] Booking: ... - Room 108` (10:00-11:00, GREEN)
2. Update original event: `Booking: ...` (11:00-13:00, unchanged)

**Scenario**: Only middle hour booked (11:00-12:00)
- Current implementation doesn't handle this (would need two events)
- System books from event start time

### Event Merging
When consecutive bookings exist for same user + room:

**Scenario**: Three separate events
- Event A: 10:00-11:00, Room 108, user@example.com
- Event B: 11:00-12:00, Room 108, user@example.com (newly booked)
- Event C: 12:00-13:00, Room 108, user@example.com

**Result**:
- Delete Event A and C
- Update Event B to span 10:00-13:00

**Detection Logic**:
- Must match: Room name + User email
- Must be adjacent: End time ≈ Start time (within 60s tolerance)
- Search window: ±12 hours from current booking

## Server Response Interpretation

### Success Indicators
```json
{
  "success": true,
  "data": {
    "referenceNumber": "BA3382D5",
    "errors": []
  }
}
```
- `success: true` OR `data.success: true`
- `errors` array empty or absent
- `referenceNumber` present

### Failure Patterns

**Room Taken**:
```json
{
  "success": false,
  "data": {
    "errors": ["conflicting reservation exists"]
  }
}
```
- Contains keyword: `"conflicting"`

**User Limit**:
```json
{
  "data": {
    "errors": ["user has reached their booking limit"]
  }
}
```
- Contains keyword: `"limited"`

**Too Early**:
```json
{
  "data": {
    "errors": ["cannot book this far in the future"]
  }
}
```
- Contains phrase: `"this far in the future"`

### Error Classification
All error detection is **substring matching** (case-insensitive):
```python
if "conflicting" in json.dumps(response):
    return BookingResult.ROOM_TAKEN
```

## Timing & Retry Strategy

### Initial Wait
If opening time is >60 seconds away:
- Sleep until T-60 seconds
- Wake up and re-login all agents

### Final Wait
- Recalculate wait time
- Sleep until T-5 seconds
- Execute booking attempt

### Retry Loop
- Max duration: **120 seconds**
- Retry on `TOO_EARLY` with 0.5s delay
- Switch rooms on `ROOM_TAKEN`
- Switch users on `USER_LIMIT`
- Give up if all users exhausted OR timeout

### Timeout Behavior
After 120 seconds:
- Mark event as FAILED
- Reason: `"Timeout - Could not secure room"`
- Move to next event in queue

## Scan & Sync Frequencies

### Calendar Scan
- **Trigger**: Main loop (continuous)
- **Range**: Next `CALENDAR_SCAN_DAYS` (default: 8 days)
- **Purpose**: Find unprocessed booking events

### Server → Calendar Sync
- **Frequency**: Every `SYNC_INTERVAL_SECONDS` (default: 300s = 5 min)
- **Range**: Last 7 days to next 30 days
- **Purpose**: Import server bookings to calendar
- **Blocked during**: Active booking attempts

### DELETE Check
- **Frequency**: Every `DELETE_CHECK_INTERVAL` (default: 300s = 5 min)
- **Range**: Next `CALENDAR_SCAN_DAYS`
- **Purpose**: Process deletion requests
- **Blocked during**: Active booking attempts

## Edge Cases & Constraints

### Past Events
- Events with start time < now are marked FAILED
- Reason: `"Event in the past"`
- Prevents wasted attempts

### Missing Reference Number (DELETE)
- Cannot delete without reference number
- Mark event RED with error: `"DELETE failed - No reference number found"`

### Session Expiry During Sync
- If `my-calendar.php` returns login page → Skip SID
- No automatic re-login during sync (prevents infinite loops)
- Next sync cycle will attempt fresh login

### Duplicate Detection (Sync)
Enhanced logic checks:
- Start time must match
- Room name must match (in description: `"Room: XXX"`)
- User email must match (in description: `"User: xxx"`)
- Prevents duplicate `[S]` events for same booking

### SID Iteration Requirement
- Server segments bookings by Schedule ID (SID 1-5)
- **MUST** query all SIDs to find all bookings
- Booking in SID=3 will NOT appear in SID=1 query
- Sync loops through SID 1 to 5 with 0.2s delay between requests
