# Project Overview

## Purpose
An automated "sniping" system for securing high-demand university library study rooms immediately upon their release. The system operates with precision timing to book rooms the instant the reservation window opens (typically 7 days in advance).

## Core Concept
This is a booking automation agent that:
- **Monitors**: Tracks upcoming desired booking slots
- **Calculates**: Determines exact opening times for each slot
- **Executes**: Attempts bookings with microsecond precision
- **Manages**: Rotates through multiple user accounts to bypass quotas
- **Syncs**: Bidirectional integration with Google Calendar

## Primary Components

### 1. Scheduler (`scheduler.py`)
The brain of the operation. Handles:
- Calendar event scanning (Google Calendar integration)
- Timing calculations (when to execute bookings)
- Booking coordination and retry logic
- Agent management and rotation
- Background sync processes

### 2. BookingAgent (`booking_agent.py`)
The executor. Manages:
- HTTP communication with the booking server
- Session management and authentication
- CSRF token handling
- Booking creation, extension, and deletion
- Server booking retrieval

### 3. Google Calendar Integration (`utils/google_calendar.py`)
Two-way sync system:
- **CalendarSync**: Syncs server bookings → Google Calendar
- **GoogleCalendarClient**: Low-level Google Calendar API wrapper
- **Event Processing**: Google Calendar → Booking requests

## Key Innovation: Dual-Mode Operation

### Mode 1: Proactive Sniping (Original)
- User defines desired slots in `bookings.json`
- System calculates opening times (7 days - 1 hour formula)
- Executes automated booking at precise moment

### Mode 2: Calendar-Driven (Enhanced)
- User creates "Booking" events in Google Calendar
- System scans calendar, processes events
- Updates events with booking status (colors + metadata)
- Supports DELETE requests for cancellation

## Technical Stack
- **Language**: Python 3.7+
- **HTTP Library**: `requests` (session management)
- **Google API**: `google-api-python-client`, OAuth2
- **Concurrency**: `threading`, `concurrent.futures`
- **Configuration**: JSON files for credentials and config

## User Pool Strategy
Multiple accounts work as a collective:
- Each user has daily/weekly booking quotas
- System rotates users to exceed individual limits
- Prioritizes "last successful user" for session continuity
- Automatically switches when a user hits quota

## Room Priority System
Two-tier hierarchy:
- **High Priority Rooms**: Attempted first (e.g., Room 108-111)
- **Low Priority Rooms**: Fallback options (e.g., Room 13-16)
- System exhausts all high-priority rooms before trying low-priority

## Status & Logging
- Comprehensive logging to `system.log`
- Google Calendar color coding:
  - **Yellow (5)**: Processing
  - **Green (10)**: Success
  - **Red (11)**: Failure/Deleted

## Current Capabilities
✅ Automated sniping with precision timing  
✅ User pool rotation and quota management  
✅ Google Calendar bidirectional sync  
✅ Event splitting (multi-hour bookings)  
✅ Event merging (consecutive slots)  
✅ DELETE support (cancel bookings)  
✅ Room priority system  
✅ Consecutive booking extension  
