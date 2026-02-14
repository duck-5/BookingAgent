# Knowledge Directory - README

## Purpose
This directory contains AI-optimized documentation for the Booking Agent project. It is designed for AI agents to quickly understand the system architecture, business logic, and implementation details.

## Document Structure

### [01_overview.md](01_overview.md)
**Purpose**: High-level introduction to the project
- What the system does
- Core components
- Dual-mode operation (proactive + calendar-driven)
- Current capabilities

**Read this first** to understand the big picture.

### [02_architecture.md](02_architecture.md)
**Purpose**: System architecture and design
- Component diagram
- Data flows (4 primary flows)
- Thread architecture
- Configuration files
- Error handling strategy
- Session management

**Essential for** understanding how components interact.

### [03_booking_rules.md](03_booking_rules.md)
**Purpose**: Business logic and constraints
- Booking window formula (7 days - 1 hour)
- User quotas and rotation
- Room priority hierarchy
- Event handling (split/merge)
- Server response interpretation
- Edge cases

**Critical for** understanding the "why" behind implementation decisions.

### [04_google_calendar_integration.md](04_google_calendar_integration.md)
**Purpose**: Bidirectional calendar sync
- Incoming: Calendar → Booking requests
- Outgoing: Server → Calendar sync
- DELETE feature
- Event metadata and color coding
- Thread synchronization

**Key for** calendar-driven features and sync behavior.

### [05_server_api.md](05_server_api.md)
**Purpose**: TAU booking server API reference
- Authentication flow
- Booking endpoints (create/update/delete)
- Data retrieval (my-calendar.php)
- Error response patterns
- Time formats
- Resource IDs (room mapping)

**Reference for** all server communication.

### [06_code_structure.md](06_code_structure.md)
**Purpose**: Code organization and patterns
- File organization
- Core classes and methods
- Configuration structure
- Data flow patterns
- Threading model
- Design patterns

**Guide for** navigating the codebase.

## How to Use This Knowledge Base

### For New AI Agents
1. **Start here**: Read this README
2. **Get context**: Read `01_overview.md`
3. **Understand flow**: Read `02_architecture.md`
4. **Deep dive**: Read domain-specific docs as needed

### For Specific Tasks

**Adding a new feature**:
- Check `02_architecture.md` for integration points
- Review `03_booking_rules.md` for business constraints
- Reference `06_code_structure.md` for patterns

**Debugging calendar issues**:
- Start with `04_google_calendar_integration.md`
- Check `02_architecture.md` for thread synchronization
- Review `03_booking_rules.md` for event handling logic

**Fixing API errors**:
- Reference `05_server_api.md` for endpoint details
- Check `03_booking_rules.md` for error interpretation
- Review `06_code_structure.md` for BookingAgent class

**Modifying booking logic**:
- Start with `03_booking_rules.md` for constraints
- Check `02_architecture.md` for data flows
- Reference `06_code_structure.md` for Scheduler class

## Key Concepts

### Prefixes
- **[P]**: Processed (calendar → server booking)
- **[S]**: Synced (server → calendar import)
- **[DELETED]**: Deletion confirmed

### Color Coding
- **Yellow (5)**: Processing
- **Green (10)**: Success/Synced
- **Red (11)**: Failure/Deleted

### Critical Timing
- Opening formula: `Target - 7 days + 1 hour`
- Wait strategy: Sleep until T-60s, wake, re-login, wait until T-5s
- Max retry: 120 seconds

### User Rotation
- Last successful user gets priority
- Rotate on quota exhaustion
- Give up when all users exhausted

### SID Iteration (CRITICAL)
- Must query SID 1-5 to find all bookings
- Bookings segmented by Schedule ID
- Missing this causes incomplete sync

## Maintenance

### Updating This Knowledge Base
When code changes:
1. Update relevant markdown file(s)
2. Keep examples consistent with actual code
3. Update error patterns if server responses change
4. Add new edge cases to `03_booking_rules.md`

### Document Dependencies
- `01_overview.md` ← References all other docs
- `02_architecture.md` ← References config.py structure
- `04_google_calendar_integration.md` ← References CalendarStatus colors
- `05_server_api.md` ← References actual endpoints
- `06_code_structure.md` ← Must match actual file structure

## Quick Reference

### Most Important Files
1. `scheduler.py` - Main logic
2. `booking_agent.py` - Server communication
3. `utils/google_calendar.py` - Calendar sync
4. `config.py` - All configuration

### Most Important Concepts
1. **Booking window rule**: 7 days - 1 hour
2. **SID iteration**: Query all SIDs (1-5)
3. **User rotation**: Bypass individual quotas
4. **Thread coordination**: `_booking_in_progress` Event
5. **Error detection**: Substring matching on server responses

### Common Pitfalls
- ❌ Forgetting to iterate SID 1-5 (incomplete booking list)
- ❌ Not converting to UTC for API requests
- ❌ Missing `X-Requested-With: XMLHttpRequest` header
- ❌ Not checking both `success` and `data.success`
- ❌ Hardcoding CSRF tokens (must fetch fresh)

## Version Info
- **Last Updated**: 2026-02-14
- **Code Version**: Current main branch
- **Python**: 3.7+
- **Key Libraries**: requests, google-api-python-client
