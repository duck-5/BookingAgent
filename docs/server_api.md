
# Server API Documentation

This document summarizes the findings regarding the server-side API and endpoints of the lab booking system (`schedule.php`). It details authentication, data retrieval, and booking management.

## Base Configuration
- **Base URL**: `https://schedule.tau.ac.il/scilib/Web`
- **User Agent**: Requires a standard browser User-Agent string.
- **Headers**:
    - `X-Requested-With: XMLHttpRequest` (Crucial for AJAX endpoints)
    - `Referer`: `.../schedule.php` (Often required)

## Authentication

### 1. Login
- **Endpoint**: `index.php`
- **Method**: `POST`
- **Parameters**:
    - `email`: User's email
    - `password`: User's password
    - `login`: `submit` (Static value)
    - `persistLogin`: `on` (Optional)
    - `CSRF_TOKEN`: Required. Extracted from the login page source.
- **Session**: The server sets `PHPSESSID` and `login_token` cookies. These must be maintained for all subsequent requests.

### 2. CSRF Token
- Most POST requests require a `CSRF_TOKEN`.
- It can be scraped from the `value` attribute of any input named `CSRF_TOKEN` on the login page or `schedule.php`.

## Data Retrieval

### 1. My Calendar Events (Bookings)
This is the **primary endpoint** for retrieving existing bookings for the logged-in user.

- **Endpoint**: `my-calendar.php`
- **Method**: `GET`
- **Parameters**:
    - `dr`: `events` (Action)
    - `start`: `YYYY-MM-DD` (Start date)
    - `end`: `YYYY-MM-DD` (End date)
    - `sid`: Schedule ID (Integer)
    - `rid`: (Empty)
    - `gid`: (Empty)

#### Important: Schedule IDs (SIDs)
The system segments resources into different "Schedules" (SIDs). A booking exists within a specific SID.
- **Observed SIDs**: 1 through 5.
- **Constraint**: You **must iterate through all SIDs** (e.g., 1 to 5) to find all bookings. A booking in SID=3 will not appear if you query SID=1.
- **Response**: JSON array of event objects.
    ```json
    [
        {
            "id": "C827789D",
            "title": "Room 015 Study",
            "start": "2026-02-19 20:00",
            "end": "2026-02-19 21:00",
            "className": "reserved mine", // "mine", "coowner", "participating"
            "allDay": false,
            "startEditable": true
        }
    ]
    ```

### 2. Schedule View (HTML)
The main schedule grid.
- **Endpoint**: `schedule.php`
- **Method**: `GET`
- **Parameters**:
    - `sd`: `YYYY-MM-DD` (Target Date)
    - `sid`: Schedule ID
- **Response**: HTML Page.
    - **Session State**: Visiting this page often initializes session variables required for other API calls.
    - **Data**: Contains `td` elements with class `slot`. Slots have `data-start`, `data-end`, `data-resourceId` attributes. Open slots contain `&nbsp;`. Booked slots may not be explicitly marked with a reserved class in the HTML for all users, making programmatic scraping difficult compared to the API.

### 3. Reservation List (API)
An alternative endpoint that lists reservations, typically used by the frontend JS.
- **Endpoint**: `schedule.php?dr=reservations`
- **Method**: `GET` or `POST`
- **Parameters**: `start` (Timestamp), `end` (Timestamp), `sid`.
- **Finding**: For the specific user `yuvalmantin`, this endpoint returned empty arrays `[]`, even when bookings existed. It may be restricted to administrative views or specific contexts. **Not recommended** for retrieving user bookings.

## Booking Management

### 1. Create Booking
- **Endpoint**: `api/reservation.php?action=create`
- **Method**: `POST`
- **Headers**: `X-Requested-With: XMLHttpRequest`
- **Payload (Multipart Form Data)**:
    - `CSRF_TOKEN`: (Token)
    - `BROWSER_TIMEZONE`: `Asia/Jerusalem`
    - `request`: JSON String containing booking details.
        ```json
        {
            "reservation": {
                "ownerId": "...",
                "resourceIds": [25], // Room ID
                "start": "2026-02-19T20:00:00.000Z", // UTC ISO String
                "end": "2026-02-19T21:00:00.000Z",
                "title": "Study",
                "description": "",
                "recurrence": { ... }
            },
            "updateScope": "full"
        }
        ```
- **Response**: JSON.
    - Success: `{"success": true, "data": { "referenceNumber": "..." }}`
    - Failure: `{"success": false, "data": { "errors": [...] }}` OR `{"message": "..."}`. Check for "conflicting", "limited", or "too early" strings in errors.

### 2. Update/Extend Booking
- **Endpoint**: `api/reservation.php?action=update`
- **Method**: `POST`
- **Payload**: Similar to Create, but includes `referenceNumber` in the `reservation` object.

### 3. Delete Booking
- **Endpoint**: `api/reservation.php?api=delete`
- **Method**: `POST`
- **Authentication**: Requires session cookie and `X-CSRF-Token` header.
- **Headers**:
    - `X-CSRF-Token`: [Token Value]
    - `Referer`: `https://schedule.tau.ac.il/scilib/Web/schedule.php`
    - `Content-Type`: `application/json`
- **Payload (JSON)**:
    ```json
    {
        "referenceNumber": "116A1288",
        "scope": "full",
        "reason": "",
        "browserTimezone": "Asia/Jerusalem"
    }
    ```
- **Response**: 
    - Success: `{"data": {"success": true, "errors": []}}`
    - Failure: `{"data": {"success": false, "errors": ["Error message"]}}`

## Room / Resource IDs
Commonly observed Resource IDs (RIDs) map to physical rooms.
- **High Priority**:
    - 125: Room 108
    - 126: Room 109
    - 127: Room 110
    - 128: Room 111
- **Low Priority**:
    - 23: Room 13
    - 24: Room 14
    - 25: Room 15
    - 26: Room 16
