# Server Error Codes & Failure Modes

This document lists the known error responses returned by the library booking system and how the `BookingAgent` handles them.

## Response Structure

The server typically returns an HTTP 200 OK status even when the booking fails. Errors are indicated within the JSON payload:

```json
{
  "success": false,
  "data": {
    "success": false,
    "errors": [
      "Error message string here"
    ]
  }
}
```

## Known Error Messages

### 1. Room Already Taken
**Error String Match:** `"conflicting"`
**Full Message Example:** *(Exact full message varies, but contains "conflicting")*
**Cause:** The specific room and time slot has already been booked by another user.
**System Action:** `ROOM_TAKEN`
*   The system abandons the current room for this slot.
*   It immediately tries the next room in the priority list.

### 2. User Quota Exceeded
**Error String Match:** `"limited"`
**Full Message Example:** *(Exact full message varies, but contains "limited")*
**Cause:** The current user account has reached its maximum allowed booking hours (daily or weekly limit).
**System Action:** `USER_LIMIT`
*   The system marks the current user as "exhausted" for this slot.
*   It swaps to the next available user in the pool and retries the *same* room and slot.

### 3. Booking Window Not Open (Too Early)
**Error String Match:** `"This reservation cannot be made this far in the future"`
**Full Message Example:** `This reservation cannot be made this far in the future. The latest date and time that can be reserved is 19/02/2026 18:28:33.`
**Cause:** The system attempted to book a slot before the 7-day window officially opened. This can happen if the system clock is slightly out of sync or if the retry loop is slightly too aggressive.
**System Action:** `ERROR` (Logged)
*   The system logs the specific error message to `system.log` with the prefix `Booking Error (Success=True but has errors)`.
*   It returns a generic `ERROR` status, causing the scheduler to retry until the timeout is reached. This is generally the desired behavior as the window will eventually open.

### 4. Generic/Unknown Errors
**Error String Match:** *(Any other message)*
**Cause:** Network issues, session expiry, malformed requests, or unknown server-side constraints.
**System Action:** `ERROR`
*   The system logs the full failure response to `system.log` with the prefix `Booking Failed`.
*   It retries the booking until the 2-minute timeout is reached.
