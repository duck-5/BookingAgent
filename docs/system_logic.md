# System Logic: Technical Deep Dive

This document details the internal algorithms, decision trees, and error-handling mechanisms of the Booking Agent.

## 1. The Booking Lifecycle

The system operates on a continuous scanning loop driven by the `Scheduler`, delegating specific tasks to specialized Services (`CalendarManager`, `BookingManager`, `AgentManager`).

### Phase 1: Discovery (CalendarScanning)
1.  **Poll**: The `Scheduler` triggers `CalendarManager.scan_for_bookings()` every few seconds.
2.  **Filter**: It looks for "Booking" events in the next `CALENDAR_SCAN_DAYS`.
3.  **Parse**: Converts event times to `BookingRequest` objects, calculating the target `opening_time` (7 days prior + 1 hour).
4.  **Ready Check**: The Scheduler processes requests that are currently valid or opening soon.

### Phase 2: Orchestration
1.  **Status Update**: The event on Google Calendar is marked as **PROCESSING** (Yellow).
2.  **Execution**: The `Scheduler` passes the request to `BookingManager.attempt_booking`.

### Phase 3: The Attack Loop (BookingManager)
The `BookingManager` executes a high-frequency loop to secure the slot:

1.  **Room Strategy**: Iterates through **High Priority** rooms first, then **Low Priority**.
2.  **Agent Strategy**: Uses `AgentManager` to retrieve a pool of authenticated agents.
    -   *Logic*: Can prioritize the "Last Successful User" to maintain session persistence.
    -   *Rotation*: If a user hits a quota (`USER_LIMIT`), they are temporarily removed from the pool for this attempt.
3.  **Retry Mechanism**:
    -   **TOO_EARLY**: If the window isn't open yet, it retries immediately (effectively "waiting" for the millisecond it opens).
    -   **ROOM_TAKEN**: Immediately switches to the next room in the priority list.
    -   **Timeout**: Aborts after 120 seconds to prevent infinite loops.

## 2. Decision Logic & Error Handling

### A. Room Priority & Exhaustion
*   **Structure**: Rooms are grouped into Batches (High vs Low).
*   **Flow**: `(High Room 1 -> High Room 2 -> ...) -> (Low Room 1 -> ...)`
*   **Constraint**: If a room returns `ROOM_TAKEN`, it is marked as failed for this attempt and skipped by subsequent agents.

### B. User Quota Management
*   **Detection**: Server returns error `"user has reached their booking limit"`.
*   **Action**: The `BookingManager` adds the agent's email to an `exhausted_emails` set.
*   **Result**: The loop continues with the next available agent for the *same* room (if not taken) or next room.

### C. Multi-Hour Logic (Splitting)
*   **Scenario**: User requests a 2-hour block (e.g., 10:00-12:00), but only 10:00-11:00 is successfully booked.
*   **Action**:
    1.  `BookingManager` returns success for the first hour.
    2.  `Scheduler` detects the duration difference.
    3.  `CalendarManager.split_event()` is called:
        -   Creates a **Success** event for 10:00-11:00 (Green).
        -   Updates the original "Booking" event to start at 11:00 (remains actionable).

## 3. Visual Logic Flow

### A. Orchestration Flow (Scheduler)

This diagram illustrates how the Scheduler discovers and dispatches booking requests.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef process fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef success fill:#E8F5E9,stroke:#2E7D32,stroke-width:3px,color:#000;
    classDef fail fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#000;

    Start([Start Scan Loop]) --> ScanCall["Call CalendarManager.scan_for_bookings()"]:::process
    ScanCall --> FoundEvents{Found Events?}:::decision
    
    FoundEvents -- No --> Sleep["Sleep (Interval)"]:::process
    Sleep --> Start
    
    FoundEvents -- Yes --> Iterate[Iterate Request]:::process
    Iterate --> TimeCheck{Target Time Reached?}:::decision
    
    TimeCheck -- No (>60s) --> WaitLong["Sleep until T-60s"]:::process
    WaitLong --> ReLogin["Re-Login Agents"]:::process
    ReLogin --> WaitShort["Sleep until T-5s"]:::process
    
    TimeCheck -- Yes (<60s) --> ReLogin
    WaitShort --> MarkProcessing["Mark Calendar: PROCESSING"]:::process
    
    MarkProcessing --> Dispatch["Call BookingManager.attempt_booking()"]:::process
    Dispatch --> Result{"Result?"}:::decision
    
    Result -- "Success" --> SplitLogic[Go to C: Result Logic]:::process
    Result -- "Failure" --> MarkFail["Mark Calendar: FAILURE"]:::fail
    MarkFail --> Iterate
    SplitLogic --> Iterate
```

### B. Booking Loop (BookingManager)

This diagram details the core "attack" logic, including retry mechanisms and error handling.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef process fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef fail fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#000;
    classDef success fill:#E8F5E9,stroke:#2E7D32,stroke-width:3px,color:#000;

    Start([Start Attempt]) --> InitVars[Reset Failed Rooms & Exhausted Users]:::process
    
    InitVars --> TimeCheck{Timeout > 120s?}:::decision
    TimeCheck -- Yes --> FailTime["Return: TIMEOUT"]:::fail
    TimeCheck -- No --> CheckAllFailed{All Rooms Failed?}:::decision
    
    CheckAllFailed -- Yes --> FailAll["Return: ROOM_TAKEN"]:::fail
    CheckAllFailed -- No --> BatchHigh["Load High Priority Rooms"]:::process
    
    BatchHigh --> LoopRooms{Iterate Rooms}:::decision
    LoopRooms -- Next --> CheckFailedRoom{Is Room Failed?}:::decision
    CheckFailedRoom -- Yes --> LoopRooms
    CheckFailedRoom -- No --> LoopAgents{Iterate Agents}:::decision
    
    LoopAgents -- Next --> CheckExhausted{Is User Exhausted?}:::decision
    CheckExhausted -- Yes --> LoopAgents
    CheckExhausted -- No --> Attempt["API: Create Reservation"]:::process
    
    Attempt --> Result{"API Result?"}:::decision
    
    Result -- "SUCCESS" --> Success["Return: SUCCESS"]:::success
    
    Result -- "ROOM_TAKEN" --> MarkRoomFailed["Add to Failed Rooms"]:::fail
    MarkRoomFailed --> LoopRooms
    
    Result -- "USER_LIMIT" --> MarkUserExhausted["Add to Exhausted Users"]:::fail
    MarkUserExhausted --> LoopAgents
    
    Result -- "TOO_EARLY" --> Wait["Sleep 0.5s"]:::process
    Wait --> TimeCheck
    
    LoopAgents -- Done --> LoopRooms
    LoopRooms -- Done --> LoadLow[Load Low Priority Rooms]:::process
    LoadLow --> LoopRooms
```

### C. Result & Split Logic (Post-Booking)

This diagram shows how the system handles the result of a booking attempt, specifically focusing on multi-hour event splitting.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef process fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef success fill:#E8F5E9,stroke:#2E7D32,stroke-width:3px,color:#000;
    classDef fail fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#000;

    Start([Booking Result Received]) --> CheckSuccess{Is Success?}:::decision
    
    CheckSuccess -- No --> UpdateFail["Update Event: RED (Failure Message)"]:::fail
    UpdateFail --> End([End])
    
    CheckSuccess -- Yes --> CalcDuration["Calculate Booking Duration"]:::process
    CalcDuration --> CheckSplit{Duration > 1h?}:::decision
    
    CheckSplit -- No (Exact Match) --> UpdateSuccess["Update Event: GREEN (Room & Ref)"]:::success
    UpdateSuccess --> End
    
    CheckSplit -- Yes (Partial) --> CallSplit["Call CalendarManager.split_event()"]:::process
    
    CallSplit --> CreateBooked["Create New Event: 1h BOOKED (Green)"]:::success
    CallSplit --> UpdateOriginal["Update Original Event: Shift Start Time +1h"]:::process
    
    CreateBooked --> End
    UpdateOriginal --> End
```