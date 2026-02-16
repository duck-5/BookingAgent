import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime
import sys
import os
import itertools

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Scheduler
from booking_agent import BookingAgent, BookingResult
from datetime import timezone

@pytest.fixture
def scheduler():
    # We need to mock os.path.exists and open for __init__ as it loads files immediately
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data='[]')), \
         patch('scheduler.GoogleCalendarClient'), \
         patch('scheduler.CalendarSync'):
        return Scheduler()

@patch('scheduler.BookingAgent')
@patch('scheduler.concurrent.futures.ThreadPoolExecutor')
def test_initialize_agents(mock_executor_cls, mock_booking_agent_cls, scheduler):
    # Setup mock agents
    mock_agent = MagicMock()
    mock_agent.login.return_value = True
    mock_booking_agent_cls.return_value = mock_agent
    
    # Setup mock executor to execute immediately
    mock_executor = mock_executor_cls.return_value
    mock_executor.__enter__.return_value = mock_executor
    
    # We need to simulate the future.result() call
    mock_future = MagicMock()
    mock_future.result.return_value = True
    
    # Mock submit to return a future
    mock_executor.submit.return_value = mock_future
    
    # Mock as_completed to return a list containing our future
    with patch('scheduler.concurrent.futures.as_completed', return_value=[mock_future]):
            scheduler.users_data = [{'email': 'test@example.com'}]
            scheduler.initialize_agents()
    
    assert len(scheduler.agents) == 1
    assert scheduler.agents[0] == mock_agent

def test_get_next_target_slot(scheduler):
    # Specific Monday at 10:00 AM
    fixed_now = datetime(2026, 1, 5, 10, 0, 0) # Monday
    
    mock_bookings = [{
        "comment": "Test Slot",
        "day_of_week": 0, # Monday
        "start_hour": 12,
        "end_hour": 13
    }]
    
    # Mock _fetch_calendar_bookings
    candidate = {
        "start": datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc),
        "opening_time": datetime(2026, 1, 5, 13, 0), # Naive is fine here as code handles it, or make aware too
        "key": "Test Slot",
        "utc_start": "utc",
        "utc_end": "utc",
        "event_id": "1",
        "original_event": {}
    }
    
    with patch.object(scheduler, '_fetch_calendar_bookings', return_value=[candidate]):
        with patch('scheduler.datetime') as mock_datetime:
            # Mock .now().astimezone() chain
            mock_now_obj = MagicMock()
            # If fixed_now is 2026-01-05 10:00:00 (naive)
            # And opening is 2026-01-05 13:00:00 (naive)
            # We want them to be comparable. 
            # Let's make astimezone() return fixed_now (naive treated as aware-ish context or just comparable)
            # Actually, target logic does: op_time.replace(tzinfo=now.tzinfo)
            # If astimezone() returns something with tzinfo, then op_time gets it.
            # Let's make fixed_now HAVE a tzinfo.
            fixed_now_aware = fixed_now.replace(tzinfo=timezone.utc)
            mock_now_obj.astimezone.return_value = fixed_now_aware
            
            mock_datetime.now.return_value = mock_now_obj
            
            # Since we replaced 'scheduler.datetime', we effectively hid 'scheduler.datetime.combine' etc.
            # But get_next_target_slot doesn't use them.
            
            # However, logic uses: op_time.replace(tzinfo=now.tzinfo)
            # candidate['opening_time'] is naive.
            
            target, wait_seconds = scheduler.get_next_target_slot()
            
            # Check if target is correct:
            assert target is not None
            assert target['start'] == datetime(2026, 1, 12, 12, 0, tzinfo=timezone.utc)
            assert wait_seconds == 10800

@patch('scheduler.datetime')
def test_attempt_booking_success_v2(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end',
        'event_id': 'evt1',
        'original_event': {
            'summary': 'Booking',
            'start': {'dateTime': '2026-01-10T10:00:00Z'},
            'end': {'dateTime': '2026-01-10T11:00:00Z'}
        },
        'start': datetime(2026, 1, 10, 10, 0)
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    mock_agent.book_room.return_value = (BookingResult.SUCCESS, "REF123")
    
    scheduler.agents = [mock_agent]
    scheduler.last_successful_user = None
    
    # Mock _update_calendar_event
    with patch.object(scheduler, '_update_calendar_event') as mock_update:
            print(f"DEBUG: slot for success = {slot}")
            assert 'start' in slot, "Slot missing 'start' key!"
            result = scheduler.attempt_booking(slot)
            
            assert result is True
            mock_update.assert_called()
            # Verify SUCCESS update was called
            # Calls: 1. PROCESSING, 2. SUCCESS/FAILURE
            assert mock_update.call_count >= 2
            args = mock_update.call_args_list[-1][0]
            assert args[1] == 'SUCCESS'

@patch('scheduler.datetime')
def test_attempt_booking_exhausted(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end',
        'event_id': 'evt1',
        'original_event': {
            'summary': 'Booking',
            'start': {'dateTime': '2026-01-10T10:00:00Z'},
            'end': {'dateTime': '2026-01-10T11:00:00Z'}
        },
        'start': datetime(2026, 1, 10, 10, 0)
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    mock_agent.book_room.return_value = (BookingResult.USER_LIMIT, None)
    
    scheduler.agents = [mock_agent]
    
    with patch.object(scheduler, '_update_calendar_event') as mock_update:
            result = scheduler.attempt_booking(slot)
            
            assert result is False
            mock_update.assert_called()
            args = mock_update.call_args_list[-1][0]
            assert args[1] == 'FAILURE'
            _, kwargs = mock_update.call_args_list[-1]
            assert kwargs['reason'] == 'All Users Quotas Exhausted'

@patch('scheduler.datetime')
def test_attempt_booking_timeout(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end',
        'event_id': 'evt1',
        'original_event': {
            'summary': 'Booking',
            'start': {'dateTime': '2026-01-10T10:00:00Z'},
            'end': {'dateTime': '2026-01-10T11:00:00Z'}
        },
        'start': datetime(2026, 1, 10, 10, 0)
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    # Simulate room taken repeatedly
    mock_agent.book_room.return_value = (BookingResult.ERROR, None)
    
    scheduler.agents = [mock_agent]
    
    # Need to mock time.time to simulate timeout
    # First call: start time
    # Subsequent calls: check timeout
    # We want start_time = 1000.
    # Then some calls return < 1120.
    # Then eventually > 1120.
    
    # Use iterator for side_effect
    # Use infinite iterator for side_effect to avoid StopIteration
    # Start at 1000, increment by 10
    # Timeout check: 120s.
    # 1000 -> start.
    # 1010 -> 10 < 120.
    # ...
    # 1130 -> 130 > 120. Timeout.
    time_iterator = itertools.count(start=1000, step=10)
    
    with patch('scheduler.time.time', side_effect=time_iterator):
            with patch('scheduler.time.sleep'): # Avoid sleeping
                with patch.object(scheduler, '_update_calendar_event') as mock_update:
                    result = scheduler.attempt_booking(slot)
                    
                    assert result is False
                    mock_update.assert_called()
                    args = mock_update.call_args_list[-1][0]
                    assert args[1] == 'FAILURE'
                    _, kwargs = mock_update.call_args_list[-1]
                    assert "Timeout" in kwargs['reason']

