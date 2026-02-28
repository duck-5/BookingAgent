
import pytest
from unittest.mock import MagicMock, patch
import sys
import os
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.tau_client import TauClient
from core.enums import BookingResult
from core.entities import UserCredentials

@pytest.fixture
def mock_session():
    with patch('clients.tau_client.requests.Session') as mock_cls:
        session = mock_cls.return_value
        session.cookies = {} 
        yield session

@pytest.fixture
def client(mock_session):
    creds = UserCredentials(
        email='test@example.com',
        password='password123',
        owner_id='12345'
    )
    return TauClient(creds)

def test_login_success(client, mock_session):
    # Setup post side_effect to populate cookies AFTER clear() is called
    def post_side_effect(*args, **kwargs):
        mock_session.cookies = {'login_token': 'abc'}
        return MagicMock()
    
    mock_session.post.side_effect = post_side_effect
    
    result = client.login()
    
    assert result is True
    assert client.is_logged_in is True
    mock_session.post.assert_called_once()

def test_login_failure(client, mock_session):
    mock_session.cookies = {}
    
    result = client.login()
    
    assert result is False
    assert client.is_logged_in is False

def test_book_room_success(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.content = b'{"success": true}'
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {"referenceNumber": "123456"}
        }
        mock_session.post.return_value = mock_response

        # Note: book_room takes resource_id(int), start_utc(str), end_utc(str)
        status, ref = client.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == BookingResult.SUCCESS
        assert ref == "123456"

def test_book_room_failure_generic(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.content = b'{"success": false}'
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": False}
        mock_session.post.return_value = mock_response

        status, ref = client.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == BookingResult.ERROR
        assert ref is not None
        assert "Server Error" in ref

def test_book_room_room_taken(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.content = b'{"success": false}'
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "data": {"errors": ["conflicting booking"]}
        }
        mock_session.post.return_value = mock_response

        status, ref = client.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == BookingResult.ROOM_TAKEN

def test_delete_booking_success(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "" # Empty response usually means success for delete API? Or json
        mock_response.json.return_value = {"data": {"success": True}}
        mock_session.post.return_value = mock_response

        result, msg = client.delete_booking("REF123")
        
        assert result is True
        assert msg == "Success"

def test_delete_booking_failure(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False, 
            "data": {"errors": ["Invalid Ref"]}
        }
        mock_session.post.return_value = mock_response

        # Tuple unpacking
        result, msg = client.delete_booking("REF123")
        
        assert result is False
        assert "Invalid Ref" in msg

def test_update_booking_success(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.content = b'{"success": true}'
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {"referenceNumber": "123456"}
        }
        mock_session.post.return_value = mock_response

        status, msg = client.update_booking("123456", 123, "2026-01-01T10:00:00Z", "2026-01-01T12:00:00Z")
        
        assert status is True
        assert msg == "123456"

def test_update_booking_failure(client, mock_session):
    client.is_logged_in = True
    
    with patch.object(client, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        mock_response.content = b'{"success": false}'
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": False,
            "data": {"errors": ["Invalid end time"]}
        }
        mock_session.post.return_value = mock_response

        status, msg = client.update_booking("123456", 123, "2026-01-01T10:00:00Z", "2026-01-01T12:00:00Z")
        
        assert status is False
        assert msg is not None
        assert "Invalid end time" in msg

