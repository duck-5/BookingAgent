# Google Calendar Integration

The booking system acts as a bidirectional synchronization layer between the University API and a user's Google Calendar. The Google Calendar is both the graphical user interface for scheduling requests and the dashboard for viewing successful server bookings.

## 1. Calendar Configuration
*   **Target Calendar:** Automatically creates or finds a calendar named `"Library Bookings"` (adjustable via `config.CALENDAR_NAME`).
*   **Authentication:** Requires an OAuth2 `client_secret.json`. On the first run, it drops a `token.json` for persistent offline access.
*   **Time Zone Requirement:** Hardcoded to `Asia/Jerusalem`.

## 2. Visual Status Indicators (Colors)

The `CalendarStatus` enum mapping dictates the visual state of an event.

| Color ID | Name | Visual | Meaning |
| :--- | :--- | :--- | :--- |
| **5** | Yellow | #fbd75b | **PROCESSING:** The scheduler has locked this event and is attempting to snipe the room. |
| **10** | Green | #51b749 | **SUCCESS / SYNCED:** The room is secured. |
| **11** | Red | #dc2127 | **FAILURE / DELETED:** The system could not secure the room (timeout, all agents exhausted) or the user successfully deleted the event. |
| **8** | Gray | #e1e1e1 | **DELETED:** Standard deleted state. |

## 3. Metadata Prefixes

The system relies on strict string prefixes in the Google Event `Summary` (Title) to determine state, prevent infinite processing loops, and differentiate origins.

*   `[P]`: **Processed**. The system has finished trying to book this user-created request. It will ignore this event on future scans.
*   `[S]`: **Synced**. The background sync daemon imported this event directly from the university server. It is read-only.
*   `[DELETED]`: A user has successfully sent a cancel request and the server removed the booking.

## 4. Workflows

### A. The User Booking Request
1.  User creates a standard event titled: `Booking: Study Session`
2.  System scanning range: Next `CALENDAR_SCAN_DAYS` (default 8 days).
3.  **Recognition:** The summary *must* start with `"Booking"` and *must not* start with `[P]` or `[S]`.
4.  **Processing:** System turns it Yellow.
5.  **Result Update:**
    *   *Success:* Turns Green. Title becomes `[P] Booking: Study Session - Room 108`. Description logs the Agent Email and Reference ID.
    *   *Failure:* Turns Red. Title becomes `[P] Booking: Study Session`. Description appends the failure error.

### B. The Sync Import
Runs on the background `_sync_worker` thread.

1.  Fetches all SIDs 1-5 from the server.
2.  Checks Google Calendar for identical events (Matching Start Time + Room Name in Description + User Email in Description).
3.  If no match, creates a new Green event starting with `[S]`.

### C. The DELETE Request
Allows users to cancel existing bookings without logging into the clunky university portal.

1.  User edits an existing *Processed* event title in Google Calendar: `DELETE [P] Booking: ...`
2.  **Recognition:** Summary contains the substring `DELETE` (case-insensitive) AND starts with a `[P]` or `[S]` prefix.
3.  **Execution:** The scanner extracts the server Reference ID string (e.g. `Ref: BA3382D5`) from the calendar description block.
4.  It calls the server delete API.
5.  **Result Update:**
    *   *Success:* Event title shrinks to `[DELETED] Booking...` and color turns Gray (8).
    *   *Failure:* Turns Red. Description states `"Permission denied or server error"`.
