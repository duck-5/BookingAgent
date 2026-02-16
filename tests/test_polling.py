import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime, timedelta, timezone
import sys
import os
import time

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Scheduler
import config

@pytest.fixture
def scheduler():
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data='[]')), \
         patch('scheduler.GoogleCalendarClient'), \
         patch('scheduler.CalendarSync'), \
         patch('scheduler.threading.Thread'): # Prevent sync thread start
        return Scheduler()

def test_long_wait_is_capped(scheduler):
    """
    Verify that if the next target is far away (e.g. 10 hours),
    the scheduler sleeps for CALENDAR_POLL_INTERVAL_SECONDS (e.g. 5 mins)
    instead of the full duration, to allow re-scanning.
    """
    
    # Setup: 
    # Target is 10 hours away (36000 seconds)
    # Poll Interval is 300 seconds (5 mins)
    
    # Mock config
    with patch('config.CALENDAR_POLL_INTERVAL_SECONDS', 300):
        
        # Mock get_next_target_slot
        # First call: returns (target, 36000)
        # Second call: raises StopIteration to break the infinite loop for test
        mock_target = {'key': 'Future Event', 'opening_time': datetime.now(timezone.utc)}
        
        scheduler.get_next_target_slot = MagicMock(side_effect=[
            (mock_target, 36000.0),
            Exception("Break Loop") 
        ])
        
        # Mock time.sleep to verify called value
        with patch('time.sleep') as mock_sleep:
            try:
                scheduler.run()
            except Exception as e:
                # We expect "Break Loop" exception
                if str(e) != "Break Loop":
                    raise e
            
            # ASSERTIONS
            
            # 1. Verify sleep was called with CAP (300), not FULL (36000-60)
            # The logic: if seconds_wait > POLL_INTERVAL -> sleep(POLL_INTERVAL) -> continue
            mock_sleep.assert_called_with(300)
            
            # 2. Verify get_next_target_slot was called TWICE
            # (Once for first iteration, then 'continue' triggers second call)
            assert scheduler.get_next_target_slot.call_count == 2
