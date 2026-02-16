import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta, timezone
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Scheduler
import config

@pytest.fixture
def scheduler():
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data='[]')), \
         patch('scheduler.GoogleCalendarClient'), \
         patch('scheduler.CalendarSync'):
        return Scheduler()

def test_missing_event_is_detected(scheduler):
    """
    Reproduce user scenario:
    Event at Feb 18, 17:00-19:00.
    Current time: Feb 14, 18:15.
    
    Verify that get_next_target_slot returns this event.
    """
    
    # Setup Times
    # Current Time: Feb 14, 18:15 Local (+02:00)
    current_time = datetime(2026, 2, 14, 18, 15, 0, tzinfo=timezone(timedelta(hours=2)))
    
    # Event Time: Feb 18, 17:00 Local (+02:00)
    event_start = datetime(2026, 2, 18, 17, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    event_start_str = event_start.isoformat() # "2026-02-18T17:00:00+02:00"
    
    event_end = datetime(2026, 2, 18, 19, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    event_end_str = event_end.isoformat()
    
    # Mock Event
    mock_event = {
        'id': 'evt_missing',
        'summary': 'Booking',
        'start': {'dateTime': event_start_str},
        'end': {'dateTime': event_end_str},
        'description': ''
    }
    
    # Mock Calendar Client list response
    scheduler.calendar_client.service.events().list.return_value.execute.return_value = {
        'items': [mock_event]
    }
    
    # Mock datetime.now() to return our current_time
    # We must patch 'scheduler.datetime' but keeping timedelta etc working is tricky.
    # Safe way: Mock ONLY 'datetime.now' if possible, or use a side effect wrapper?
    # Or just rely on the fact that the code uses datetime.now() in get_next_target_slot.
    
    # We will patch 'scheduler.datetime' with a wrapper
    real_datetime = datetime
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            # If tz provided, return current_time in that tz? 
            # get_next_target_slot calls .astimezone() on result. 
            # So we return something that behaves like current_time.
            return current_time.astimezone(tz)
        
        @classmethod
        def utcnow(cls):
            return current_time.astimezone(timezone.utc).replace(tzinfo=None) # Naive UTC
            
        # fromisoformat needs to fall back to real
        @classmethod
        def fromisoformat(cls, *args, **kwargs):
            return real_datetime.fromisoformat(*args, **kwargs)
            
        @classmethod
        def strptime(cls, *args, **kwargs):
             return real_datetime.strptime(*args, **kwargs)

    # Patch!
    with patch('scheduler.datetime', MockDatetime):
        target, wait = scheduler.get_next_target_slot()
        
        # INVESTIGATION
        print(f"Target found: {target}")
        print(f"Wait seconds: {wait}")
        
        assert target is not None, "Event was ignored!"
        assert target['key'] == 'Booking'
        assert target['start'] == event_start
