# System Diagrams

This document exists for developers to trace specific logic flows, decision trees, and error-handling mechanisms inside the Booking Agent codebase.

## 1. The Full System Flow

This flowchart encapsulates the entire lifecycle of a booking from the moment the user drops an event onto Google Calendar to the final result syncing back.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef process fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef success fill:#E8F5E9,stroke:#2E7D32,stroke-width:3px,color:#000;
    classDef fail fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#000;

    GoogleCalendar[(Google Calendar)] -->|Every Scan Interval| SchedulerScan["Scheduler calls<br>CalendarManager.scan_for_bookings(split_multi_hour=True)"]:::process
    
    SchedulerScan --> CheckMultiHour{"Duration > 1h?"}:::decision
    CheckMultiHour -- Yes --> UpfrontSplit["Split into 1h events<br>on Google Calendar upfront"]:::process
    CheckMultiHour -- No --> EachSlot
    UpfrontSplit --> EachSlot["Process each 1-hour slot"]:::process
    
    EachSlot --> CalcOpTime["Calculate Opening Time<br>(Slot Start - 7d + 1h)"]:::process
    CalcOpTime --> ReadyWait{"Is Slot Open?<br>(Opening <= Now)"}:::decision
    
    ReadyWait -- No --> Sleep["Sleep until Opening Time<br>(Warming up session at T-60s)"]:::process
    ReadyWait -- Yes --> MarkYellow["Update Event: PROCESSING (Yellow)"]:::process
    
    MarkYellow --> CheckPrev{"Does Preceding<br>Booking Exist?<br>(event.end == slot.start)"}:::decision
    
    CheckPrev -- Yes --> SetPreferred["Set Preferences:<br>Priority 1: Same Room<br>Priority 2: Same User"]:::process
    CheckPrev -- No --> DefaultHierarchy["Use Default Room Hierarchy<br>(High Priority -> Low Priority)"]:::process
    
    SetPreferred --> AttemptBooking["BookingManager.attempt_booking()"]:::process
    DefaultHierarchy --> AttemptBooking
    
    AttemptBooking --> ResCheck{"Response Status?"}:::decision
    ResCheck -- "TOO_EARLY" --> RetryEarly["Sleep 200ms & retry<br>SAME room & user"]:::process
    RetryEarly --> AttemptBooking
    
    ResCheck -- "ROOM_TAKEN" --> MarkRoomFail["Blacklist room for slot.<br>Advance to next room"]:::process
    MarkRoomFail --> AttemptBooking
    
    ResCheck -- "USER_LIMIT" --> MarkUserFail["Blacklist user for slot.<br>Advance to next user"]:::process
    MarkUserFail --> AttemptBooking
    
    ResCheck -- "FAILED / TIMEOUT" --> MarkRed["Update Event: FAILED (Red)"]:::fail
    
    ResCheck -- "SUCCESS" --> CheckMerge{"Is Preceding Same Room<br>& Same User<br>& Total Duration <= 3h?"}:::decision
    
    CheckMerge -- Yes --> Merge["Merge in Google Calendar<br>(Extend preceding event,<br>delete current event)"]:::success
    CheckMerge -- No --> MarkGreen["Update Event: SUCCESS (Green)<br>Keep as separate event"]:::success
```

## 2. Server to Calendar Sync Mechanism

This diagram specifically details how the `CalendarSync._sync_worker` ensures the server logic remains perfectly synchronized with the user's Google Calendar.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef sync fill:#E8F5E9,stroke:#2E7D32,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef fetch fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;

    Start(["Daemon Wake Up<br>(Every config.CALENDAR_POLL_INTERVAL_SECONDS)"]) --> CheckLock{"Is _booking_in_progress<br>Set True?"}:::decision
    
    CheckLock -- Yes --> Pause["Block Thread until booking is complete<br>(Prevents overriding mid-booking metadata)"]:::fetch
    Pause --> FetchGCal
    
    CheckLock -- No --> FetchGCal["Fetch Existing Google Calendar Events<br>(Range: T-7 Days -> T+30 Days)"]:::fetch
    
    FetchGCal --> UserLoop{"For User in `credentials.json`"}:::decision
    
    UserLoop -- Next User --> SidLoop{"Iterate SID 1-5<br>(CRITICAL)"}:::decision
    
    SidLoop -- Next SID --> GET["GET /my-calendar.php?dr=events&sid={SID}"]:::fetch
    
    GET --> FilterMine{"Is user owner/co-owner<br>of the server event?"}:::decision
    FilterMine -- No --> SidLoop
    FilterMine -- Yes --> CheckDuplicate{"Is there an identical event<br>in Google Calendar?<br>(Exact match on Time, Room, and User Email)"}:::decision
    
    CheckDuplicate -- "Yes" --> SidLoop
    CheckDuplicate -- "No" --> AddSync["Create [S] Event in Google Calendar<br>Color = GREEN (10)"]:::sync
    
    AddSync --> SidLoop
    SidLoop -- "Done (1 to 5)" --> UserLoop
    
    UserLoop -- "Done" --> End(["Sleep config.CALENDAR_POLL_INTERVAL_SECONDS"])
```

## 3. The DELETE Mechanism

This diagram details the flow when the user cancels an event via Google Calendar.

```mermaid
flowchart TD
    %% --- STYLING ---
    classDef process fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#000;
    classDef decision fill:#FFF8E1,stroke:#FF8F00,stroke-width:2px,color:#000;
    classDef fail fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#000;
    classDef del fill:#e1e1e1,stroke:#777,stroke-width:2px,color:#000;

    Start(["Daemon Wake Up<br>(Every config.CALENDAR_POLL_INTERVAL_SECONDS)"]) --> GetEvents["Scan GCal for events containing<br>'DELETE' in Summary"]:::process
    
    GetEvents --> Valid{"Does Summary start with<br>[P] or [S]?"}:::decision
    Valid -- "No (e.g., Unprocessed)" --> Skip[Skip Event]:::process
    Valid -- Yes --> GetRef["Extract Reference Numbers (one or multiple for merged events)<br>& User Email from Description"]:::process
    
    GetRef --> HasRef{"Reference found?"}:::decision
    HasRef -- No --> ErrorEvent["Update Event: RED<br>Error: No reference found"]:::fail
    
    HasRef -- Yes --> FindAgent{"Find active Agent<br>matching User Email"}:::decision
    
    FindAgent -- "Found" --> Attempt[Use matching Agent]:::process
    FindAgent -- "Not Found" --> LoopAll[Fallback: Try all available Agents]:::process
    
    Attempt --> CallDel["For each Ref Number:<br>POST /api/reservation.php?api=delete"]:::process
    LoopAll --> CallDel
    
    CallDel --> Result{"All Refs<br>Cancelled?"}:::decision
    
    Result -- Yes --> ConfirmDel["Update Event: [DELETED]<br>Color = GRAY (8)<br>Append Deletion Timestamp"]:::del
    Result -- No --> ErrorEvent2["Update Event: RED<br>Error: Delete failed"]:::fail
```
