# System Architecture & Overview

The Booking Agent is an automated "sniping" system designed to secure high-demand university library study rooms immediately upon their release (exactly 7 days in advance + 1 hour).

## 1. Core Concept

The system operates via a continuous loop that:
*   **Monitors:** Tracks upcoming desired booking slots added to a Google Calendar.
*   **Calculates:** Determines exact opening times for each slot.
*   **Executes:** Attempts bookings with microsecond precision.
*   **Manages:** Rotates through multiple user accounts to bypass individual quotas.
*   **Syncs:** Maintains bidirectional integration with Google Calendar, serving as both the UI and database.

## 2. Component Diagram

The following diagram illustrates the relationship between the major system layers.

```mermaid
┌─────────────────────────────────────────────────────────────────┐
│                          USER LAYER                             │
├─────────────────────────────────────────────────────────────────┤
│    dashboard    │  credentials.json  │  Google Calendar Events  │
└────────┬─────────────────┬──────────────────────┬───────────────┘
         │                 │                      │
         ▼                 ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                       SCHEDULER (scheduler.py)                  │
│                     (Service Orchestrator)                      │
└─────┬──────────────────────────┬───────────────────────────┬────┘
      │                          │                           │
      ▼                          ▼                           ▼
┌─────────────┐        ┌──────────────────┐        ┌────────────────┐
│AgentManager │        │ BookingManager   │        │CalendarManager │
│(Pool)       │        │ (Logic & Retry)  │        │(Scanner)       │
└─────┬───────┘        └─────────┬────────┘        └──────┬─────────┘
      │                          │                        │
      ▼                          ▼                        ▼
┌─────────────┐        ┌──────────────────┐        ┌────────────────┐
│ TauClient   │        │ TauClient        │        │ GoogleCalClient│
│ (Login)     │        │ (Book/Delete)    │        │ (API)          │
└─────┬───────┘        └─────────┬────────┘        └──────┬─────────┘
      │                          │                        │
      ▼                          ▼                        ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│  TAU BOOKING SERVER     │         │  GOOGLE CALENDAR API        │
│  schedule.tau.ac.il     │         │  calendar.googleapis.com    │
└─────────────────────────┘         └─────────────────────────────┘
```

## 3. Code Structure

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
│   ├── booking_manager.py  # Central Booking Loop & Strategy
│   └── calendar_manager.py # Translation layer (Calendar <-> Entities)
├── dashboard/              # Monitoring UI
│   ├── app.py              # FastAPI app
│   ├── state.py            # Shared State
│   └── logger.py           # UI logging handler
├── docs/                   # AI/Developer Documentation
└── tests/                  # Unit and integration tests
```

## 4. Concurrency & Threading Model

The `BookingAgent` is highly concurrent, utilizing a multi-threaded architecture to ensure critical booking operations are never stalled by background tasks like syncing or updating the dashboard.

### A. Threading Architecture
1.  **Main Thread (`_scan_loop`):** Runs the continuous Orchestrator loop inside `Scheduler.run()`. It is solely responsible for discovering, calculating, and executing booking attacks.
2.  **Sync Daemon Thread (`_sync_worker`):** A background worker initiated on startup that runs the Server $\rightarrow$ Calendar Sync logic and processes active DELETE requests every 5 minutes.
3.  **Dashboard Thread:** Runs the `uvicorn` (FastAPI) ASGI server, providing a local monitoring UI at `http://localhost:8000`.

### B. Thread Synchronization (`_booking_in_progress`)
To prevent race conditions, the system uses a `threading.Event()` flag named `_booking_in_progress`.

*   **Set (True):** The system is currently scanning or idle. The `_sync_worker` thread is permitted to send requests to Google Calendar or the University API.
*   **Clear (False):** The main thread has officially locked onto a booking request and is actively executing the "Attack Loop".
    *   *Action:* The `_sync_worker` checks this flag before executing a sync. If it is clear, the Daemon blocks (pauses) until the booking finishes.
    *   *Reasoning:* This prevents the sync worker from accidentally overwriting `[P]` (Processed) metadata tags or triggering rate limits right when the system needs maximum performance.

### C. Handled Edge Cases
*   **Parallel Logins:** Managed via `ThreadPoolExecutor` (max 8 concurrent workers) during startup to quickly hydrate the `AgentManager`.
*   **Target Conflict:** If multiple valid `BookingRequests` share the exact same opening time, the loops trigger sequentially in memory, with network latency bridging the gap.
