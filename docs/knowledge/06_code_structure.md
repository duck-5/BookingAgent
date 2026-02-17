# Code Structure

## File Organization

```
booking_agent/
├── main.py                 # Entry point
├── scheduler.py            # Orchestrator (initializes services)
├── config.py               # Configuration and constants
├── credentials.json        # User accounts (list of email/pass/owner_id)
├── system.log              # Runtime logs
├── clients/                # External API wrappers
│   ├── __init__.py
│   ├── tau_client.py       # TAU Booking System Client
│   └── google_calendar.py  # Google Calendar API Client
├── core/                   # Shared Entities and Enums
│   ├── __init__.py
│   ├── entities.py         # Data classes (BookingRequest, UserCredentials)
│   └── enums.py            # BookingResult, CalendarStatus
├── services/               # Business Logic Managers
│   ├── __init__.py
│   ├── agent_manager.py    # Manages user pool and rotation
│   ├── booking_manager.py  # Booking strategies (retry, logic)
│   └── calendar_manager.py # Calendar scanning and updating logic
├── dashboard/              # Monitoring UI
│   ├── app.py              # FastAPI app
│   ├── state.py            # Shared state
│   └── logger.py           # Dashboard logging handler
├── docs/knowledge/         # Documentation
└── tests/                  # Unit and integration tests
```

## Core Components

### Scheduler (`scheduler.py`)
The main entry point for the background process. It does not contain business logic anymore.
**Responsibilities**:
-   Initializes `AgentManager`, `CalendarManager`, `BookingManager`.
-   Runs the main `while` loop for scanning.
-   Runs background threads for Sync.
-   Coordinates the dashboard updates.

### Services Layer

#### AgentManager (`services/agent_manager.py`)
Manages the pool of authenticated `TauClient` instances.
-   `_load_agents()`: Parallel login for all users.
-   `get_rotational_agents()`: Returns list of agents (round-robin or priority).

#### CalendarManager (`services/calendar_manager.py`)
Abstractions for Google Calendar operations specific to the Booking domain.
-   `scan_for_bookings()`: Returns `BookingRequest` objects.
-   `scan_for_deletions()`: Returns `DeletionRequest` objects.
-   `update_event_status()`: Updates color, title, and description.
-   `split_event()`: Handles multi-hour event splitting.

#### BookingManager (`services/booking_manager.py`)
The "Brain" of the booking process.
-   `attempt_booking(request)`: Tries to book a room using available agents and retry logic.
-   `cancel_booking(request)`: Handles deletion via an appropriate agent.

### Clients Layer

#### TauClient (`clients/tau_client.py`)
Low-level HTTP client for the University system.
-   `login()`: Authenticates and manages session.
-   `book_room()`: Sends booking payload.
-   `delete_booking()`: Sends delete payload.

#### GoogleCalendarClient (`clients/google_calendar.py`)
Low-level wrapper for Google Calendar API.

## Core (`core/`)
-   `entities.py`: Data classes (`BookingRequest`, `DeletionRequest`, `UserCredentials`, `Room`).
-   `enums.py`: `BookingResult` (SUCCESS, ROOM_TAKEN...), `CalendarStatus` (Colors).

## Threading Model
1.  **Main Thread**: Runs `Scheduler.run()` -> `_scan_loop()`.
2.  **Sync Thread**: Runs `_sync_worker()` for periodic syncing.
3.  **Dashboard Thread**: Runs `uvicorn` server for the UI.

## Error Handling
-   **Services** catch exceptions from **Clients** and return Enums/False.
-   **Scheduler** logs errors but keeps the loop running.
-   **Dashboard** reflects error states in the UI.
