
import pytest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime, timedelta, timezone

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.calendar_manager import CalendarManager
from core.enums import CalendarStatus
import config

@pytest.fixture
def mock_google_client():
    client = MagicMock()
    # Mock service.events().list().execute() chain
    client.service.events.return_value.list.return_value.execute.return_value = {'items': []}
    client.get_or_create_calendar.return_value = "cal123"
    return client

@pytest.fixture
def calendar_manager(mock_google_client):
    return CalendarManager(mock_google_client)

def test_scan_for_bookings_parsing(calendar_manager, mock_google_client):
    # Mock Calendar List
    # Uses UTC time in mock
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_book',
            'summary': 'Booking: My Study Session',
            'start': {'dateTime': '2026-02-20T10:00:00Z'}, 
            'end': {'dateTime': '2026-02-20T12:00:00Z'}
        }]
    }
    
    with patch('services.calendar_manager.datetime') as mock_dt:
        # Mock UTC now
        mock_dt.utcnow.return_value = datetime(2026, 2, 10, 10, 0, 0)
        # We need side_effect for fromisoformat and strptime if used
        # But actually only utcnow is called directly.
        # Oh, the code uses datetime.fromisoformat inside. 
        # Mocking datetime completely is tricky because we need the real methods too.
        # It's better NOT to mock datetime unless necessary. 
        # Since logic uses utcnow for range calculation, we can just rely on real datetime
        # but mock the result of list() to be independent of "now".
        pass

    # Actually, scan_for_bookings calls datetime.utcnow().
    # If we don't mock it, tmin/tmax will be correct relative to "real now".
    # The key is that the event must be within CALENDAR_SCAN_DAYS (default 60?).
    # If the event is in 2026, and real time is 2026 (or 2025), it might be filtered or not.
    # The snippet says "The current local time is: 2026...". So we run in 2026.
    
    candidates = calendar_manager.scan_for_bookings()
    
    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.summary == 'Booking: My Study Session'
    assert cand.utc_start == '2026-02-20T10:00:00.000Z'
    
    # Verify opening time calc: Start - 7 days + 1 hour
    # 20th 10:00 - 7d = 13th 10:00. + 1h = 13th 11:00.
    assert cand.opening_time.day == 13
    assert cand.opening_time.hour == 11

def test_scan_for_deletions_parsing(calendar_manager, mock_google_client):
    # Mock Calendar Events with DELETE request
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt1',
            'summary': 'DELETE [P] Booking: Room - Study',
            'description': 'User: test@example.com\nRef: REF123',
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    
    requests = calendar_manager.scan_for_deletions()
    
    assert len(requests) == 1
    req = requests[0]
    assert req.ref_num == "REF123"
    assert req.owner_email == "test@example.com"
    assert req.event_id == "evt1"

def test_update_event_status_processing(calendar_manager, mock_google_client):
    evt = {'id': 'e1', 'summary': 'Booking', 'description': 'desc'}
    
    calendar_manager.update_event_status('e1', CalendarStatus.PROCESSING, evt)
    
    mock_google_client.service.events().update.assert_called_once()
    _, kwargs = mock_google_client.service.events().update.call_args
    
    assert kwargs['body']['colorId'] == CalendarStatus.PROCESSING
    assert '[P]' in kwargs['body']['summary']

def test_update_event_status_success(calendar_manager, mock_google_client):
    evt = {'id': 'e1', 'summary': '[P] Booking', 'description': 'desc'}
    
    calendar_manager.update_event_status(
        'e1', CalendarStatus.SUCCESS, evt, 
        room_name="Room 101", ref_num="REF", user="u1"
    )
    
    mock_google_client.service.events().update.assert_called_once()
    _, kwargs = mock_google_client.service.events().update.call_args
    
    body = kwargs['body']
    assert body['colorId'] == CalendarStatus.SUCCESS
    assert "Room: Room 101" in body['description']
    assert "- Room 101" in body['summary']

def test_update_event_status_deleted(calendar_manager, mock_google_client):
    evt = {'id': 'e1', 'summary': '[P] Booking', 'description': 'desc'}
    
    calendar_manager.update_event_status('e1', CalendarStatus.DELETED, evt)
    
    mock_google_client.service.events().update.assert_called_once()
    _, kwargs = mock_google_client.service.events().update.call_args
    
    body = kwargs['body']
    assert body['colorId'] == CalendarStatus.DELETED
    assert "Deleted on:" in body['description']

def test_split_event(calendar_manager, mock_google_client):
    orig_event = {
        'id': 'orig1',
        'summary': 'Booking: Long',
        'start': {'dateTime': '2026-02-14T10:00:00'},
        'end': {'dateTime': '2026-02-14T12:00:00'}
    }
    
    booked_start = datetime(2026, 2, 14, 10, 0, 0)
    booked_end = datetime(2026, 2, 14, 11, 0, 0)
    
    calendar_manager.split_event(
        orig_event, booked_start, booked_end, 
        "Room A", "REF", "user"
    )
    
    # Verify New Event Created (the booked part)
    mock_google_client.add_event.assert_called_once()
    
    # Verify Old Event Updated (the remaining part)
    mock_google_client.service.events().update.assert_called_once()
    _, kwargs = mock_google_client.service.events().update.call_args
    
    # Should start at 11:00 now
    assert kwargs['body']['start']['dateTime'] == '2026-02-14T11:00:00'
