# Server API Reference

## Base Configuration
- **URL**: `https://schedule.tau.ac.il/scilib/Web`
- **Headers**: Requires `X-Requested-With: XMLHttpRequest` for AJAX endpoints
- **Timezone**: Asia/Jerusalem

## Authentication
**Login**: `POST /index.php`
- Parameters: email, password, CSRF_TOKEN
- Response: Sets `PHPSESSID` and `login_token` cookies

## Booking Endpoints

### Create Booking
```
POST /api/reservation.php?action=create
```
Request includes:
- CSRF_TOKEN
- reservation object with ownerId, resourceIds[], start/end (UTC ISO)
- BROWSER_TIMEZONE: Asia/Jerusalem

Success Response:
```json
{"success": true, "data": {"referenceNumber": "BA3382D5"}}
```

### Update/Extend
```
POST /api/reservation.php?action=update
```
Same as create, but includes `referenceNumber` in reservation object.

### Delete
```
POST /api/reservation.php?api=delete
```
Payload:
```json
{
  "referenceNumber": "BA3382D5",
  "scope": "full",
  "browserTimezone": "Asia/Jerusalem"
}
```

## Data Retrieval

### Fetch User Bookings
```
GET /my-calendar.php?dr=events&start={date}&end={date}&sid={1-5}
```
**CRITICAL**: Must iterate SID 1-5 to find all bookings.

Response:
```json
[{"id": "C827789D", "title": "Room 015 Study", "start": "2026-02-19 20:00", "className": "reserved mine"}]
```

## Error Detection (Substring Matching)
- `"conflicting"` → ROOM_TAKEN
- `"limited"` → USER_LIMIT  
- `"this far in the future"` → TOO_EARLY

## Resource IDs (Rooms)
High Priority: 125-128 (Rooms 108-111)
Low Priority: 23-26 (Rooms 13-16)

## Time Formats
- **Request**: `"2026-02-21T10:00:00.000Z"` (UTC ISO with milliseconds)
- **Response**: `"2026-02-19 20:00"` (Server local time)
