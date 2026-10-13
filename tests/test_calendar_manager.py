
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

def test_split_multi_hour_event_upfront(calendar_manager, mock_google_client):
    """Test that split_multi_hour_event splits a 3H event into 3 1H BookingRequest objects."""
    event = {
        'id': 'evt_3h',
        'summary': 'Booking: 3 Hours',
        'description': 'Notes',
        'start': {'dateTime': '2026-02-14T10:00:00Z', 'timeZone': 'Asia/Jerusalem'},
        'end': {'dateTime': '2026-02-14T13:00:00Z', 'timeZone': 'Asia/Jerusalem'}
    }
    dt_start = datetime(2026, 2, 14, 10, 0, 0, tzinfo=timezone.utc)
    dt_end = datetime(2026, 2, 14, 13, 0, 0, tzinfo=timezone.utc)
    
    # Mock add_event to return dicts with new IDs
    mock_google_client.add_event.side_effect = [
        {'id': 'evt_3h_part2'},
        {'id': 'evt_3h_part3'}
    ]
    
    chunks = calendar_manager.split_multi_hour_event(event, dt_start, dt_end)
    
    assert len(chunks) == 3
    assert chunks[0].event_id == 'evt_3h'
    assert chunks[0].start_time == dt_start
    assert chunks[0].end_time == dt_start + timedelta(hours=1)
    
    assert chunks[1].event_id == 'evt_3h_part2'
    assert chunks[1].start_time == dt_start + timedelta(hours=1)
    assert chunks[1].end_time == dt_start + timedelta(hours=2)
    
    assert chunks[2].event_id == 'evt_3h_part3'
    assert chunks[2].start_time == dt_start + timedelta(hours=2)
    assert chunks[2].end_time == dt_end
    
    # Verify update called for the first chunk
    mock_google_client.service.events().update.assert_called_once()
    # Verify add_event called twice for parts 2 and 3
    assert mock_google_client.add_event.call_count == 2

def test_find_successful_booking_accumulated_duration(calendar_manager, mock_google_client):
    """Test that find_successful_booking correctly calculates accumulated duration for merged events."""
    # Mock a 3-hour merged event
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'merged_prev',
            'summary': '[P] Booking: Room 108',
            'description': 'Booked for User: agent@tau.ac.il\nRef: REF100\nRef: REF200',
            'location': 'Room 108',
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T13:00:00Z'}
        }]
    }
    
    target_start = datetime(2026, 2, 14, 13, 0, 0, tzinfo=timezone.utc)
    preceding = calendar_manager.find_successful_booking(target_start)
    
    assert preceding is not None
    assert preceding.room == 'Room 108'
    assert preceding.user == 'agent@tau.ac.il'
    assert preceding.duration_hours == 3.0
    
    # Backwards compatibility unpacking check
    ev, ref, user, room = preceding
    assert room == 'Room 108'
    assert user == 'agent@tau.ac.il'

def test_merge_adjacent_events(calendar_manager, mock_google_client):
    """Test that merge_adjacent_events extends prev_event and deletes current_event."""
    prev_evt = {
        'id': 'evt_prev',
        'summary': '[P] Booking: Room 108',
        'description': 'User: agent@tau.ac.il\nRef: REF1',
        'start': {'dateTime': '2026-02-14T10:00:00Z'},
        'end': {'dateTime': '2026-02-14T11:00:00Z'}
    }
    curr_evt = {
        'id': 'evt_curr',
        'summary': '[P] Booking: Room 108',
        'start': {'dateTime': '2026-02-14T11:00:00Z'},
        'end': {'dateTime': '2026-02-14T12:00:00Z'}
    }
    
    mock_google_client.service.events().get.return_value.execute.return_value = prev_evt
    
    success = calendar_manager.merge_adjacent_events(
        prev_evt, curr_evt, "REF2", "Room 108", "agent@tau.ac.il"
    )
    
    assert success is True
    # Verify update on prev_evt extends to 12:00:00
    mock_google_client.service.events().update.assert_called_once()
    _, kwargs = mock_google_client.service.events().update.call_args
    assert kwargs['body']['end']['dateTime'] == '2026-02-14T12:00:00Z'
    assert 'Ref: REF2' in kwargs['body']['description']
    
    # Verify delete called on curr_evt
    mock_google_client.service.events().delete.assert_called_once_with(
        calendarId="cal123", eventId="evt_curr"
    )

def test_split_multi_hour_skips_when_add_event_fails(calendar_manager, mock_google_client):
    """Test that split_multi_hour_event skips subsequent chunks when add_event returns None."""
    event = {
        'id': 'evt_2h',
        'summary': 'Booking: 2 Hour Block',
        'start': {'dateTime': '2026-02-20T10:00:00Z'},
        'end': {'dateTime': '2026-02-20T12:00:00Z'}
    }
    dt_start = datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc)
    dt_end = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    
    # client.add_event fails and returns None
    mock_google_client.add_event.return_value = None
    
    chunks = calendar_manager.split_multi_hour_event(event, dt_start, dt_end)
    
    # Only the first updated event should be in chunks, no phantom chunk created
    assert len(chunks) == 1
    assert chunks[0].event_id == 'evt_2h'

def test_extract_ref_and_user_multiple_refs(calendar_manager):
    """Test extracting multiple references from description."""
    desc = "Booked for User: user1@tau.ac.il\nRoom: Room 108\nRef: REF101\nRef: REF102\nRef: REF103"
    extracted = calendar_manager._extract_ref_and_user(desc)
    assert extracted.ref_num == "REF101"
    assert extracted.user == "user1@tau.ac.il"
    assert extracted.ref_nums == ["REF101", "REF102", "REF103"]
    # Tuple unpacking check
    primary_ref, user = extracted
    assert primary_ref == "REF101"
    assert user == "user1@tau.ac.il"

def test_find_successful_booking_ignores_failed_and_deleted_events(calendar_manager, mock_google_client):
    """Test that find_successful_booking ignores events with FAILURE, DELETED or PROCESSING status."""
    target_start = datetime(2026, 2, 14, 11, 0, 0, tzinfo=timezone.utc)
    
    # 1. Event with CalendarStatus.FAILURE
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_failed',
            'summary': '[P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'colorId': CalendarStatus.FAILURE,
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 2. Event with CalendarStatus.DELETED
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_deleted',
            'summary': '[P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'colorId': CalendarStatus.DELETED,
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 3. Event with [DELETED] in summary
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_deleted_title',
            'summary': '[DELETED] [P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 4. Event with CalendarStatus.PROCESSING
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_processing',
            'summary': '[P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'colorId': CalendarStatus.PROCESSING,
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 5. Event with [FAILED] in summary
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_failed_title',
            'summary': '[FAILED] [P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 6. Event with [D] in summary
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_d_title',
            'summary': '[D] [P] Booking: Room 108',
            'description': 'User: agent@tau.ac.il\nRef: REF1\nRoom: Room 108',
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None

    # 7. Event with description=None (verifying null safety)
    mock_google_client.service.events().list.return_value.execute.return_value = {
        'items': [{
            'id': 'evt_null_desc',
            'summary': '[P] Booking: Room 108',
            'description': None,
            'start': {'dateTime': '2026-02-14T10:00:00Z'},
            'end': {'dateTime': '2026-02-14T11:00:00Z'}
        }]
    }
    assert calendar_manager.find_successful_booking(target_start) is None
