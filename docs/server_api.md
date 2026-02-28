# Server API Reference

This document covers all interactions, payload structures, and error parsing related to the University Library Booking Server.

## 1. Connection & Authentication

*   **Base URL**: `https://schedule.tau.ac.il/scilib/Web`
*   **Timezone Requirement**: All requests *must* use `Asia/Jerusalem` context.
*   **Required Headers**: For AJAX API endpoints, you must include `X-Requested-With: XMLHttpRequest` and a valid `Referer`.

### Login Flow
1.  `POST /index.php`
2.  **Parameters (Form Data):** `email`, `password`, `login=submit`, and `CSRF_TOKEN`.
3.  **Note on CSRF:** The initial CSRF token must be scraped from the `value` attribute of the hidden input field on the login page's HTML.
4.  **Session:** The server returns `PHPSESSID` and `login_token` cookies. The `TauClient` (`requests.Session`) must persist these for all subsequent calls.

## 2. Reading Data (The Sync Mechanism)

Retrieving a user's current bookings requires querying the `my-calendar.php` endpoint.

*   **Endpoint**: `GET /my-calendar.php`
*   **Parameters**:
    *   `dr=events`
    *   `start=YYYY-MM-DD`
    *   `end=YYYY-MM-DD`
    *   `sid={Integer}`

### The SID (Schedule ID) Iteration Rule [CRITICAL]
The university system partitions resources into different "Schedules". A booking made in SID 3 will **not** appear if you query SID 1.
To find all bookings for a user, the syncing logic **must** iterate through SIDs 1 through 5.

**Valid Response Format:**
```json
[
    {
        "id": "C827789D",
        "title": "Room 015 Study",
        "start": "2026-02-19 20:00",
        "end": "2026-02-19 21:00",
        "className": "reserved mine"
    }
]
```
*Note: We only process events where the `className` includes "mine", "coowner", or "participating".*

## 3. Modifying Data (Booking & Deleting)

### A. Create Booking
*   **Endpoint**: `POST /api/reservation.php?action=create`
*   **Method**: `POST`
*   **Payload Type:** **Multipart Form Data** (Not raw JSON)
    *   `CSRF_TOKEN`: (Scraped token)
    *   `BROWSER_TIMEZONE`: `Asia/Jerusalem`
    *   `request`: A **JSON String** containing the complex booking object:

```json
{
    "reservation": {
        "ownerId": "...",
        "resourceIds": [25], // The Room ID
        "start": "2026-02-19T20:00:00.000Z", // Must be UTC ISO String
        "end": "2026-02-19T21:00:00.000Z",
        "title": "Study",
        "description": "",
        "recurrence": { }
    },
    "updateScope": "full"
}
```

### B. Update / Extend Booking
*   **Endpoint:** `POST /api/reservation.php?action=update`
*   **Constraint:** The payload must include the **FULL** reservation object (exactly as in the create payload), but with the `referenceNumber` and `userId` injected. Partial updates will fail with an HTML server error.

### C. Delete Booking
*   **Endpoint:** `POST /api/reservation.php?api=delete`
*   **Payload Type:** Raw JSON string.
    ```json
    {
        "referenceNumber": "116A1288",
        "scope": "full",
        "reason": "",
        "browserTimezone": "Asia/Jerusalem"
    }
    ```

## 4. API Error Parsing

The TAU server typically returns an HTTP `200 OK` status even when a logical booking failure occurs. The `BookingAgent` relies on **case-insensitive substring matching** within the specific error messages returned in the JSON payload `{"data": {"errors": [...]}}`.

### 1. `ROOM_TAKEN`
*   **Substring Match:** `"conflicting"` or `"unavailable"`
*   **Meaning:** The room is already booked by another student for that exact time.
*   **Agent Action:** The system Abandons this Room ID and attempts the next high/low priority room.

### 2. `USER_LIMIT`
*   **Substring Match:** `"limited"`
*   **Meaning:** The specific agent account has hit the university's daily or weekly maximum hours.
*   **Agent Action:** The `BookingManager` suppresses this agent for the remainder of the *current* booking loop iteration, and swaps to the next authenticated account.

### 3. `TOO_EARLY`
*   **Substring Match:** `"this far in the future"`
*   **Meaning:** The request fired milliseconds before the 7-day-advance window actually opened on the server clock.
*   **Agent Action:** The system sleeps for `config.BOOKING_RETRY_INTERVAL_SECONDS` and retries the exact same request.

### 4. `TOO_LONG`
*   **Substring Match:** `"not last longer than 3 hours"`
*   **Meaning:** The requested duration exceeds the 3-hour per-block maximum.
*   **Agent Action:** Caught by pre-booking split logic, but if this error is returned, the system marks the attempt as a failure.

## 5. Resource IDs (Room Mapping)
*   **High Priority (`HIGH_PRIORITY_ROOMS`)**:
    *   125: Room 108
    *   126: Room 109
    *   127: Room 110
    *   128: Room 111
*   **Low Priority (`LOW_PRIORITY_ROOMS`)**:
    *   23: Room 13
    *   24: Room 14
    *   25: Room 15
    *   26: Room 16
