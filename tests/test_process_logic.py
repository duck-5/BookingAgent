import pytest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler import Scheduler
import config

@pytest.fixture
def mock_scheduler_deps():
    with patch('scheduler.GoogleCalendarClient') as mock_gc_cls, \
         patch('scheduler.CalendarSync') as mock_sync_cls, \
         patch('scheduler.BookingAgent') as mock_agent_cls, \
         patch('builtins.open', new_callable=MagicMock), \
         patch('os.path.exists', return_value=True):
             
        mock_gc = mock_gc_cls.return_value
        mock_gc.get_or_create_calendar.return_value = "cal123"
        
        mock_sync = mock_sync_cls.return_value
        
        # Mock load json
        with patch('json.load', return_value=[{'email': 'test@example.com'}]):
            scheduler = Scheduler()
            
        yield {
            'scheduler': scheduler,
            'gc': mock_gc,
            'agent_cls': mock_agent_cls
        }

class TestSchedulerProcessLogic:
    
    def test_process_delete_requests_success(self, mock_scheduler_deps):
        scheduler = mock_scheduler_deps['scheduler']
        mock_gc = mock_scheduler_deps['gc']
        
        # Setup Mock Agent
        mock_agent = MagicMock()
        mock_agent.email = "test@example.com"
        mock_agent.delete_booking.return_value = True
        scheduler.agents = [mock_agent]
        
        # Mock Calendar Events with DELETE request
        mock_gc.service.events().list.return_value.execute.return_value = {
            'items': [{
                'id': 'evt1',
                'summary': 'DELETE [P] Booking: Room - Study',
                'description': 'User: test@example.com\nRef: REF123',
                'start': {'dateTime': '2026-02-14T10:00:00'},
                'end': {'dateTime': '2026-02-14T11:00:00'}
            }]
        }
        
        scheduler._process_delete_requests()
        
        # Verify Agent Delete called
        mock_agent.delete_booking.assert_called_with("REF123")
        
        # Verify Calendar Update (marked as deleted)
        mock_gc.service.events().update.assert_called_once()
        _, kwargs = mock_gc.service.events().update.call_args
        assert kwargs['calendarId'] == scheduler.calendar_id
        assert kwargs['eventId'] == 'evt1'
        assert kwargs['body']['colorId'] == config.CalendarStatus.DELETED
        assert '[DELETED]' in kwargs['body']['summary']

    def test_process_delete_requests_no_ref(self, mock_scheduler_deps):
        scheduler = mock_scheduler_deps['scheduler']
        mock_gc = mock_scheduler_deps['gc']
        
        mock_gc.service.events().list.return_value.execute.return_value = {
            'items': [{
                'id': 'evt1',
                'summary': 'DELETE [P] Booking',
                'description': 'No ref here', # Missing Ref
                'start': {'dateTime': '2026-02-14T10:00:00'}
            }]
        }
        
        scheduler._process_delete_requests()
        
        # Update should mark as FAILURE (red)
        mock_gc.service.events().update.assert_called_once()
        _, kwargs = mock_gc.service.events().update.call_args
        assert kwargs['body']['colorId'] == config.CalendarStatus.FAILURE
        assert "No reference number found" in kwargs['body']['description']

    def test_process_delete_requests_agent_fail(self, mock_scheduler_deps):
        scheduler = mock_scheduler_deps['scheduler']
        mock_gc = mock_scheduler_deps['gc']
        
        mock_agent = MagicMock()
        mock_agent.email = "test@example.com"
        mock_agent.delete_booking.return_value = False # Fail
        scheduler.agents = [mock_agent]
        
        mock_gc.service.events().list.return_value.execute.return_value = {
            'items': [{
                'id': 'evt2',
                'summary': 'DELETE [P] Booking',
                'description': 'User: test@example.com\nRef: REF999'
            }]
        }
        
        scheduler._process_delete_requests()
        
        # Update should mark as FAILURE
        mock_gc.service.events().update.assert_called_once()
        _, kwargs = mock_gc.service.events().update.call_args
        assert kwargs['body']['colorId'] == config.CalendarStatus.FAILURE
        assert "DELETE failed" in kwargs['body']['description']

    def test_fetch_calendar_bookings_parsing(self, mock_scheduler_deps):
        scheduler = mock_scheduler_deps['scheduler']
        mock_gc = mock_scheduler_deps['gc']
        
        # Mock Calendar List
        mock_gc.service.events().list.return_value.execute.return_value = {
            'items': [{
                'id': 'evt_book',
                'summary': 'Booking: My Study Session',
                'start': {'dateTime': '2026-02-20T10:00:00+02:00'}, # Local
                'end': {'dateTime': '2026-02-20T12:00:00+02:00'}
            }]
        }
        
        candidates = scheduler._fetch_calendar_bookings()
        
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand['key'] == 'Booking: My Study Session'
        # Verify opening time calc: Start - 7 days + 1 hour
        # 20th - 7d = 13th. + 1h = 11:00.
        # But wait, logic is: dt_start - 7 days + 1 hour.
        # 10:00 - 7 days = 13th 10:00. + 1 hour = 11:00.
        expected_open_day = 13
        assert cand['opening_time'].day == expected_open_day
        assert cand['opening_time'].hour == 11

    def test_split_event_logic(self, mock_scheduler_deps):
        """Test user splitting logic without real API calls."""
        scheduler = mock_scheduler_deps['scheduler']
        mock_gc = mock_scheduler_deps['gc']
        
        # Original Event 2 hours
        orig_event = {
            'id': 'orig1',
            'summary': 'Booking: Long Study',
            'description': 'Desc',
            'start': {'dateTime': '2026-02-14T10:00:00'},
            'end': {'dateTime': '2026-02-14T12:00:00'}
        }
        
        # Booked for 1st hour
        booked_start = datetime(2026, 2, 14, 10, 0, 0)
        booked_end = datetime(2026, 2, 14, 11, 0, 0)
        
        mock_gc.service.events().insert.return_value.execute.return_value = {'id': 'new_split'}
        
        scheduler._split_event_after_booking(
            original_event=orig_event,
            booked_start=booked_start,
            booked_end=booked_end,
            room_name="Room 1",
            ref_num="REF1",
            user="u1"
        )
        
        # Verify Insert (Booked portion)
        # Should create a new event for the booked part
        mock_gc.service.events().insert.assert_called_once()
        
        # Verify Update (Remaining portion)
        # Should update original event to start at 11:00
        mock_gc.service.events().update.assert_called_once()
        _, kwargs = mock_gc.service.events().update.call_args
        assert kwargs['eventId'] == 'orig1'
        assert kwargs['body']['start']['dateTime'] == '2026-02-14T11:00:00'
