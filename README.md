# Automated Library Booking Agent

## Overview
This project is a Python-based automation tool for booking library rooms. It integrates with **Google Calendar** to allow users to schedule, monitor, and manage bookings directly from their calendar interface.

The system uses a **Service-Oriented Architecture**:
-   **Scheduler**: Orchestrates background tasks.
-   **Services**: `AgentManager`, `BookingManager`, `CalendarManager` handle logic.
-   **Clients**: Wrappers for University and Google APIs.
-   **Dashboard**: Real-time web UI for monitoring.

## Features
*   **Precision Timing**: Calculates the exact millisecond a booking window opens and schedules the execution accordingly.
*   **User Pool Management**: Rotates through a configured list of user accounts to bypass individual booking quotas (e.g., daily/weekly limits).
*   **Priority System**:
    *   **Room Priority**: Attempts to book "High Priority" rooms first, falling back to "Low Priority" rooms if necessary.
    *   **User Priority**: Prioritizes the "Last Successful User" to maintain active sessions, optimizing speed.
*   **Consecutive Booking Intelligence**: Detects if a user already has a booking ending at the target start time and attempts to **extend** the existing reservation instead of creating a new one (reducing friction).
*   **Robust Failure Handling**: Automatically handles "Room Taken", "User Limit Reached", and network timeouts by switching rooms or users instantly.

## Configuration

### 1. Credentials (`credentials.json`)
Create a `credentials.json` file in the root directory with a list of user accounts to be used by the bot.
```json
[
    {
        "email": "user1@example.com",
        "password": "secret_password",
        "owner_id": "12345"
    },
    {
        "email": "user2@example.com",
        "password": "secret_password",
        "owner_id": "67890"
    }
]
```

### 2. Schedule Requests via Google Calendar
The bot scans your Google Calendar (named `Library Bookings`) for requests:
*   **Booking Request**: Create an event with summary `Booking: Study Session` (or any title starting with `Booking:`). Multi-hour events are automatically decomposed into atomic 1-hour slots.
*   **Cancellation Request**: Add `DELETE` to the event summary (e.g., `DELETE [P] Booking: Room 108`). The bot will cancel all associated reservation references on the university portal and update the event to `[DELETED]`.

### 3. Room Priorities (`config.py`)
Edit `config.py` to define your preferred rooms.
```python
HIGH_PRIORITY_ROOMS = {
    125: "Room 108",
    126: "Room 109"
}
```

## Usage

### Option 1: Local Setup
1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **Run the Scheduler**:
    ```bash
    python main.py
    ```

### Option 2: Docker Setup (Recommended)
1.  **Ensure Configuration Files Exist**:
    Make sure `credentials.json`, `client_secret.json`, and `token.json` are in the project root.
2.  **Start with Docker Compose**:
    ```bash
    docker compose up --build -d
    ```
3.  **Access Dashboard**:
    Open `http://localhost:8000` in your browser.

The system will start, scan for the next target slot, and sleep until the booking window triggers.

## File Structure
*   `main.py`: Entry point. Starts the scheduler.
*   `scheduler.py`: Core logic for time calculation, wait strategies, and coordinating booking/sync cycles.
*   `services/booking_manager.py`: Coordinates agent attempts and room priority hierarchies.
*   `clients/tau_client.py`: Handles direct HTTP communication with the booking system (Login, CSRF, Reservation POSTs).
*   `services/calendar_manager.py`: Google Calendar integration, event parsing, chunk splitting, and merging.
*   `docs/booking_rules.md`: High-level business rules and reservation constraints.
*   `docs/architecture.md`: System architecture and service layout.
*   `docs/diagrams.md`: Flowcharts for booking and deletion pipelines.
*   `docs/server_api.md`: Reference for server endpoints, payload formats, and error codes.
*   `system.log`: Execution logs.

## Documentation
For detailed insights into the system's operation:
1.  [**Docker Guide**](docs/docker.md): Instructions for containerized deployment and troubleshooting Google Auth in headless environments.
2.  [**Booking Rules**](docs/booking_rules.md): High-level constraints, booking windows, and priority definitions.
3.  [**Architecture**](docs/architecture.md): Technical breakdown of modules, data flow, and state management.
4.  [**Diagrams**](docs/diagrams.md): Visual flowcharts of the simplified 1-hour booking and sync pipeline.
5.  [**Server API**](docs/server_api.md): Endpoint specifications, payload structures, and error handling.

## Disclaimer
This tool is for educational purposes only. Automated booking may violate the terms of service of the target system. Use responsibly.
