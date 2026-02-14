# Google Calendar Integration Knowledge

## Overview
This document summarizes the findings and logic for integrating the Booking Agent with Google Calendar. The goal is to allow users to create events in a specific calendar ("Library Bookings") which are then automatically processed by the system to make actual room bookings.

## Calendar Configuration
- **Calendar Name**: `Library Bookings`
- **Time Zone**: `Asia/Jerusalem`
- **Scanning Range**: The system scans for events in the **next 30 days** to catch upcoming booking requests.

## Event Processing Logic

### 1. Trigger
The system scans the "Library Bookings" calendar for events.
- **Match Criteria**: Event summary (title) MUST start with `"Booking"`.
- **Ignore Criteria**: Event summary starts with `"[P]"`. This prefix indicates the event has already been **P**rocessed.

### 2. Processing State (Yellow)
When a valid "Booking" event is found:
- The system immediately updates its color to **Yellow (ID: 5)**.
- Ideally, the title is updated to prepend `[P]` at this stage or the user is notified that processing has started. (Currently, the `[P]` prefix is applied after processing to allow re-tries if it crashes mid-way, but visually it turns Yellow).

### 3. Booking Action
The system attempts to book the room for the specified time range.
- **Mock Implementation**: Currently, this is a simulation (`mock_book_room`).
- **Real Implementation**: This will eventually call the `BookingAgent` to perform the actual HTTP request to the library system.

### 4. Outcome & Metadata Updates
Based on the result of the booking attempt, the event is updated:

#### Success
- **Color**: **Green (ID: 10)**
- **Title**: Prefixed with `[P] ` and suffixed with ` - Room [Room Number]`.
    - Example: `[P] Booking: Study Session - Room 101`
- **Location**: Set to the booked room name (e.g., `Room 101`).
- **Description**: Appended with:
    ```text
    Booked for User: [User Name]
    Room: [Room Number]
    ```

#### Failure
- **Color**: **Red (ID: 11)**
- **Title**: Prefixed with `[P] `.
    - Example: `[P] Booking: Study Session`
- **Description**: Appended with the error reason:
    ```text
    Error: [Error Message]
    ```

## Delete Feature

### Trigger
To delete an existing booking, add the keyword **"DELETE"** to the event title in Google Calendar.

### Processing Logic
The system periodically scans the calendar (every 5 minutes) for delete requests:

1. **Detection**: Events containing "DELETE" in the title (case-insensitive)
2. **Validation**: Only processes events with `[P]` or `[S]` prefix (processed/synced bookings)
3. **Reference Extraction**: Extracts the reference number from the event description (`Ref: XXXXXXXX`)
4. **Deletion**: Calls the library system API to delete the booking
5. **Update**: On success, marks the event as `[DELETED]` with red color

### Outcome

#### Success
- **Color**: **Red (ID: 11)**
- **Title**: Prefixed with `[DELETED]` and DELETE keyword removed
    - Example: `[DELETED] Booking: Study Session - Room 101`
- **Description**: Appended with deletion timestamp:
    ```text
    Deleted on: 2026-02-13 23:00:00
    ```

#### Failure
- **Color**: **Red (ID: 11)**
- **Description**: Appended with the error reason:
    ```text
    Error: DELETE failed - No reference number found
    ```
    or
    ```text
    Error: DELETE failed - Server error
    ```

### Important Notes
- Already deleted events (starting with `[DELETED]`) are not re-processed
- Delete requests for unprocessed events (no `[P]` or `[S]` prefix) are skipped
- The delete check runs every **5 minutes** and respects ongoing booking attempts

## Google Calendar Colors (Event Color IDs)
| Color ID | Name | Visual Color | Use Case |
| :--- | :--- | :--- | :--- |
| **5** | Yellow | #fbd75b | **Processing** / In Progress |
| **10** | Green | #51b749 | **Success** / Booked |
| **11** | Red | #dc2127 | **Failure** / Error / **Deleted** |

## Scripts & Tools
- `google_calendar_client.py`: Core wrapper for Google Calendar API (Authentication, List, Insert, Update).
- `poc_import_calendar.py`: Script to create a test event in the calendar.
- `process_calendar_bookings.py`: Main tool that scans, processes, and updates events based on the logic above.
- `list_library_events.py`: Utility to list upcoming events in the next 30 days.
