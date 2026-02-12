# Automated Library Booking Agent

## Overview
This project is an automated "sniping" system designed to secure high-demand library study rooms immediately upon their release. The system monitors a university booking schedule and employs a pool of user accounts to book rooms the instant the reservation window opens (typically 7 days in advance).

## Key Features
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

### 2. Desired Bookings (`bookings.json`)
Define the weekly schedule you want the bot to secure.
```json
[
    {
        "day_of_week": 4, 
        "start_hour": 10,
        "end_hour": 12,
        "comment": "Friday Morning Study"
    }
]
```
*   `day_of_week`: 0=Monday, 6=Sunday.

### 3. Room Priorities (`config.py`)
Edit `config.py` to define your preferred rooms.
```python
HIGH_PRIORITY_ROOMS = {
    125: "Room 108",
    126: "Room 109"
}
```

## Usage

1.  **Install Dependencies**:
    ```bash
    pip install requests
    ```
2.  **Run the Scheduler**:
    ```bash
    python main.py
    ```
    The system will start, scan for the next target slot, and sleep until the booking window triggers.

## File Structure
*   `main.py`: Entry point. Starts the scheduler.
*   `scheduler.py`: Core logic for time calculation, wait strategies, and coordinating the booking attack.
*   `booking_agent.py`: Handles direct HTTP communication with the booking system (Login, CSRF, Reservation POSTs).
*   `docs/booking_rules.md`: High-level business rules and reservation constraints.
*   `docs/system_logic.md`: Deep-dive technical documentation of algorithms and error handling.
*   `docs/server_errors.md`: Reference for server error codes and handling.
*   `system.log`: Execution logs.

## Documentation
For detailed insights into the system's operation:
1.  [**Booking Rules**](docs/booking_rules.md): High-level constraints, booking windows, and priority definitions.
2.  [**System Logic (Deep Dive)**](docs/system_logic.md): Technical explanation of the "Sniper" algorithm, precision timing, and internal decision trees.
3.  [**Server Error Codes**](docs/server_errors.md): collaborative guide to known error keys and how the agent handles them.

## Disclaimer
This tool is for educational purposes only. Automated booking may violate the terms of service of the target system. Use responsibly.
