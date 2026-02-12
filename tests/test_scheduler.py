import pytest
from unittest.mock import MagicMock, patch, mock_open
from datetime import datetime
from scheduler import Scheduler

@pytest.fixture
def scheduler():
    # We need to mock os.path.exists and open for __init__ as it loads files immediately
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', mock_open(read_data='[]')):
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
    
    # We need to mock _load_json to return our bookings
    with patch.object(scheduler, '_load_json', side_effect=[[], mock_bookings]):
        with patch('scheduler.datetime') as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            mock_datetime.combine = datetime.combine
            mock_datetime.min = datetime.min
            
            target, wait_seconds = scheduler.get_next_target_slot()
            
            # Check if target is correct:
            assert target is not None
            assert target['start'] == datetime(2026, 1, 12, 12, 0)
            assert wait_seconds == 10800

@patch('scheduler.datetime')
def test_attempt_booking_success(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end'
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    mock_agent.book_room.return_value = ("SUCCESS", "REF123")
    
    scheduler.agents = [mock_agent]
    scheduler.last_successful_user = None
    
    # Mock _save_history to avoid file operations
    with patch.object(scheduler, '_save_history') as mock_save:
            result = scheduler.attempt_booking(slot)
            
            assert result is True
            mock_save.assert_called_once()
            args, _ = mock_save.call_args
            assert args[0]['status'] == 'SUCCESS'

@patch('scheduler.datetime')
def test_attempt_booking_exhausted(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end'
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    mock_agent.book_room.return_value = ("USER_LIMIT", None)
    
    scheduler.agents = [mock_agent]
    
    with patch.object(scheduler, '_save_history') as mock_save:
            result = scheduler.attempt_booking(slot)
            
            assert result is False
            mock_save.assert_called_once()
            args, _ = mock_save.call_args
            assert args[0]['status'] == 'FAILED'
            assert args[0]['reason'] == 'Users Exhausted'

@patch('scheduler.datetime')
def test_attempt_booking_timeout(mock_datetime, scheduler):
    mock_datetime.now.return_value = datetime(2026, 1, 1, 10, 0, 0)
    slot = {
        'key': '2026-01-10 10:00',
        'utc_start': 'utc_start',
        'utc_end': 'utc_end'
    }
    
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    # Simulate room taken repeatedly
    mock_agent.book_room.return_value = ("ROOM_TAKEN", None)
    
    scheduler.agents = [mock_agent]
    
    # Need to mock time.time to simulate timeout
    # First call: start time
    # Second call: check timeout (needs to be > start + 120)
    start_time = 1000
    with patch('scheduler.time.time', side_effect=[start_time, start_time + 130]):
            with patch('scheduler.time.sleep'): # Avoid sleeping
                with patch.object(scheduler, '_save_history') as mock_save:
                    result = scheduler.attempt_booking(slot)
                    
                    assert result is False
                    mock_save.assert_called_once()
                    args, _ = mock_save.call_args
                    assert args[0]['reason'] == 'Timeout'

