import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timezone, timedelta
import sys
import os
import urllib.parse

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

def test_fetch_converts_to_utc_z_string(scheduler):
    """
    Verify that _fetch_calendar_bookings converts a Local/Aware time 
    to a UTC string ending in 'Z', which fixes the Booking Agent time shift issue.
    """
    # Mock Calendar Event: 08:00 AM Local (IDT, UTC+2)
    # Google API returns ISO string with offset
    start_str = "2026-02-19T08:00:00+02:00"
    end_str = "2026-02-19T09:00:00+02:00"
    
    mock_event = {
        'id': 'evt1',
        'summary': 'Booking: Test',
        'start': {'dateTime': start_str},
        'end': {'dateTime': end_str}
    }
    
    scheduler.calendar_client.service.events().list.return_value.execute.return_value = {
        'items': [mock_event]
    }
    
    # Run Fetch
    # access private method via name mangling if needed, but it's single underscore so accessible
    candidates = scheduler._fetch_calendar_bookings()
    
    assert len(candidates) == 1
    cand = candidates[0]
    
    # EXPECTED: 08:00+02:00 -> 06:00 UTC
    # Format must match: %Y-%m-%dT%H:%M:%S.000Z
    expected_utc_start = "2026-02-19T06:00:00.000Z"
    expected_utc_end = "2026-02-19T07:00:00.000Z"
    
    assert cand['utc_start'] == expected_utc_start
    assert cand['utc_end'] == expected_utc_end

def test_merge_url_params_valid_iso(scheduler):
    """
    Verify that _merge_consecutive_bookings generates valid ISO strings for timeMin/timeMax.
    Specifically checking for the double suffix issue (+02:00Z).
    """
    # Inputs
    start_time = datetime(2026, 2, 19, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))) # 10:00 Local
    end_time = datetime(2026, 2, 19, 11, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    
    # Run _merge (Mocking list call to capture params)
    mock_list = scheduler.calendar_client.service.events().list
    mock_list.return_value.execute.return_value = {'items': []}
    
    scheduler._merge_consecutive_bookings('evt1', 'Room 1', 'user', start_time, end_time)
    
    # Check Call Args
    _, kwargs = mock_list.call_args
    timeMin = kwargs['timeMin']
    timeMax = kwargs['timeMax']
    
    # Calculation: start_time - 12h. 
    # 10:00+02:00 - 12h = Prev Day 22:00+02:00.
    # Convert to UTC -> Prev Day 20:00 UTC.
    # Format MUST be ...Z
    
    # We don't need exact time check if math is hard, but we MUST verify it ends in 'Z' 
    # and DOES NOT contain '+' (offset).
    
    assert timeMin.endswith('Z')
    assert '+' not in timeMin
    assert timeMax.endswith('Z')
    assert '+' not in timeMax
    
    # Optional: Check exact value "2026-02-18T20:00:00Z"
    assert "2026-02-18T20:00:00Z" in timeMin
