import pytest
from unittest.mock import MagicMock, patch, mock_open
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

def test_delete_formatting_and_execution(scheduler):
    """Verify delete requests use [D] prefix and Gray color (8)."""
    
    # Mock Calendar Event with DELETE keyword
    mock_event = {
        'id': 'evt1',
        'summary': 'DELETE [P] Booking: Room 101',
        'description': 'User: test@example.com\nRoom: Room 101\nRef: REF123',
        'start': {'dateTime': '2026-02-14T10:00:00Z'},
        'end': {'dateTime': '2026-02-14T11:00:00Z'}
    }
    
    # Mock Calendar List response
    scheduler.calendar_client.service.events().list.return_value.execute.return_value = {
        'items': [mock_event]
    }
    
    # Mock Agent
    mock_agent = MagicMock()
    mock_agent.email = 'test@example.com'
    mock_agent.delete_booking.return_value = True
    scheduler.agents = [mock_agent]
    
    # Run Delete Process
    with patch.object(scheduler.calendar_client.service.events(), 'update') as mock_update:
        scheduler._process_delete_requests()
        
        # Verify Update Call
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        body = kwargs['body']
        
        # CHECK 1: Prefix [DELETED]
        # Use exact match or 'in'
        assert '[DELETED]' in body['summary']
        assert 'Booking: Room 101' in body['summary']
        
        # CHECK 2: Color Red (11)
        assert body['colorId'] == config.CalendarStatus.DELETED
        # assert body['colorId'] == '11'

def test_sync_order(scheduler):
    """Verify that Delete requests are processed BEFORE Server Sync."""
    
    # Create Manager to track order
    manager = MagicMock()
    
    # Attach mocks to scheduler
    scheduler._process_delete_requests = manager.process_delete
    scheduler.syncer.sync_all_users = manager.sync_all
    
    # Mock sleep to return None on first call (start loop), then raise exception on second call (break loop)
    with patch('scheduler.time.sleep', side_effect=[None, InterruptedError("Stop Loop")]): 
        try:
            scheduler._sync_worker()
        except InterruptedError:
            pass

    # Assert Call Order
    # Check that process_delete called first
    assert manager.mock_calls[0][0] == 'process_delete'
    assert manager.mock_calls[1][0] == 'sync_all'

