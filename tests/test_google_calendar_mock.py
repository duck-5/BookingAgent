import pytest
from unittest.mock import MagicMock, patch
import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.google_calendar import GoogleCalendarClient, CalendarSync
import config

@pytest.fixture
def mock_calendar_service():
    with patch('utils.google_calendar.build') as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        yield mock_service

@pytest.fixture
def mock_creds():
    with patch('utils.google_calendar.Credentials') as mock_creds_cls:
        mock_creds_instance = MagicMock()
        mock_creds_instance.valid = True
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds_instance
        yield mock_creds_instance

class TestGoogleCalendarClient:
    def test_authenticate_success(self, mock_creds, mock_calendar_service):
        with patch('os.path.exists', return_value=True):
            client = GoogleCalendarClient()
            result = client.authenticate()
            
            assert result is True
            assert client.service is not None

    def test_authenticate_failure_no_creds(self):
        with patch('os.path.exists', return_value=False):
            client = GoogleCalendarClient()
            with pytest.raises(FileNotFoundError):
                client.authenticate()

    def test_authenticate_failure_build_error(self, mock_creds):
        with patch('os.path.exists', return_value=True), \
             patch('utils.google_calendar.build', side_effect=Exception("Build Error")):
            
            client = GoogleCalendarClient()
            result = client.authenticate()
            
            assert result is False
            assert client.service is None

    def test_get_or_create_calendar_existing(self, mock_calendar_service, mock_creds):
        with patch('os.path.exists', return_value=True):
            client = GoogleCalendarClient()
            client.authenticate()
            
            # Mock list response
            mock_calendar_service.calendarList().list.return_value.execute.return_value = {
                'items': [{'summary': 'MyCal', 'id': 'cal123'}]
            }
            
            cal_id = client.get_or_create_calendar('MyCal')
            assert cal_id == 'cal123'
            mock_calendar_service.calendars().insert.assert_not_called()

    def test_get_or_create_calendar_new(self, mock_calendar_service, mock_creds):
        with patch('os.path.exists', return_value=True):
            client = GoogleCalendarClient()
            client.authenticate()
            
            # Mock list response (empty/not found)
            mock_calendar_service.calendarList().list.return_value.execute.return_value = {
                'items': [{'summary': 'OtherCal', 'id': 'cal456'}]
            }
            
            # Mock insert response
            mock_calendar_service.calendars().insert.return_value.execute.return_value = {
                'id': 'new_cal_id'
            }
            
            cal_id = client.get_or_create_calendar('MyCal')
            assert cal_id == 'new_cal_id'
            mock_calendar_service.calendars().insert.assert_called_once()

    def test_add_event(self, mock_calendar_service, mock_creds):
         with patch('os.path.exists', return_value=True):
            client = GoogleCalendarClient()
            client.authenticate()
            
            client.add_event(
                summary="Test Event",
                start_time="2026-01-01T10:00:00",
                end_time="2026-01-01T11:00:00",
                calendar_id="primary"
            )
            
            mock_calendar_service.events().insert.assert_called_once()
            args, kwargs = mock_calendar_service.events().insert.call_args
            assert kwargs['calendarId'] == 'primary'
            assert kwargs['body']['summary'] == 'Test Event'


@pytest.fixture
def mock_calendar_sync_deps():
    with patch('utils.google_calendar.GoogleCalendarClient') as mock_gc_cls, \
         patch('booking_agent.BookingAgent') as mock_agent_cls, \
         patch('builtins.open', new_callable=MagicMock) as mock_open:
             
        mock_gc = mock_gc_cls.return_value
        mock_gc.authenticate.return_value = True
        mock_gc.get_or_create_calendar.return_value = "mock_cal_id"
        
        # Mock json load for users
        import json
        with patch('json.load', return_value=[{'email': 'test@example.com'}]):
             yield {
                 'gc': mock_gc,
                 'agent_cls': mock_agent_cls,
                 'open': mock_open
             }

class TestCalendarSync:
    def test_sync_initialization(self, mock_calendar_sync_deps):
        with patch('os.path.exists', return_value=True):
            syncer = CalendarSync()
            assert syncer.calendar_id == "mock_cal_id"

    def test_sync_all_users_basic(self, mock_calendar_sync_deps):
        deps = mock_calendar_sync_deps
        mock_gc = deps['gc']
        mock_agent = deps['agent_cls'].return_value
        
        # Mock Agent Login
        mock_agent.login.return_value = True
        mock_agent.BASE_URL = "http://test"
        mock_agent.session = MagicMock()
        
        # Mock Server Response (my-calendar.php)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 'REF123',
            'title': 'Room 101 Study',
            'start': '2026-02-14 10:00',
            'end': '2026-02-14 12:00',
            'className': 'mine'
        }]
        mock_agent.session.get.return_value = mock_response
        
        # Mock Google Calendar Events (Empty to trigger add)
        mock_gc.service.events().list.return_value.execute.return_value = {'items': []}
        
        with patch('os.path.exists', return_value=True):
            syncer = CalendarSync()
            syncer.sync_all_users()
            
            # Verify Add Event was called
            # We expect 1 call because we returned 1 event from server and 0 from Google
            mock_gc.add_event.assert_called_once()
            _, kwargs = mock_gc.add_event.call_args
            assert "[S] Room 101" in kwargs['summary']
            assert "Room: Room 101" in kwargs['description']

    def test_sync_all_users_duplicate_check(self, mock_calendar_sync_deps):
        deps = mock_calendar_sync_deps
        mock_gc = deps['gc']
        mock_agent = deps['agent_cls'].return_value
        
        mock_agent.login.return_value = True
        mock_agent.BASE_URL = "http://test"
        mock_agent.session = MagicMock()
        
        # Mock Server Response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{
            'id': 'REF123',
            'title': 'Room 101 Study',
            'start': '2026-02-14 10:00',
            'end': '2026-02-14 12:00',
            'className': 'mine'
        }]
        mock_agent.session.get.return_value = mock_response
        
        # Mock Existing Google Event (Formatted as Synced)
        mock_gc.service.events().list.return_value.execute.return_value = {
            'items': [{
                'summary': '[S] Room 101',
                'start': {'dateTime': '2026-02-14T10:00:00'},
                'description': 'User: test@example.com\nRoom: Room 101\nRef: REF123'
            }]
        }
        
        with patch('os.path.exists', return_value=True):
            syncer = CalendarSync()
            syncer.sync_all_users()
            
            # Verify Add Event was NOT called (Duplicate)
            mock_gc.add_event.assert_not_called()
