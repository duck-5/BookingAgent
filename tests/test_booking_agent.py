import pytest
from unittest.mock import MagicMock, patch
from booking_agent import BookingAgent

@pytest.fixture
def mock_session():
    with patch('booking_agent.requests.Session') as mock_cls:
        session = mock_cls.return_value
        session.cookies = {} # Default to empty
        yield session

@pytest.fixture
def agent(mock_session):
    user_data = {
        'email': 'test@example.com',
        'password': 'password123',
        'owner_id': '12345'
    }
    return BookingAgent(user_data)

def test_login_success(agent, mock_session):
    # Simulate successful login cookie
    mock_session.cookies = {'login_token': 'abc'}
    
    result = agent.login()
    
    assert result is True
    assert agent.is_logged_in is True
    mock_session.post.assert_called_once()

def test_login_failure(agent, mock_session):
    # Simulate empty cookies (login failed)
    mock_session.cookies = {}
    
    result = agent.login()
    
    assert result is False
    assert agent.is_logged_in is False

def test_get_csrf_token_success(agent, mock_session):
    # Mock the response text containing the token
    mock_response = MagicMock()
    mock_response.text = '<html>name="CSRF_TOKEN" value="test_token"</html>'
    mock_session.get.return_value = mock_response
    
    token = agent.get_csrf_token()
    
    assert token == "test_token"

def test_get_csrf_token_failure(agent, mock_session):
    # Mock response without token
    mock_response = MagicMock()
    mock_response.text = '<html>No token here</html>'
    mock_session.get.return_value = mock_response
    
    token = agent.get_csrf_token()
    
    assert token is None

def test_book_room_success(agent, mock_session):
    agent.is_logged_in = True
    
    # Mock get_csrf_token on the agent instance
    with patch.object(agent, 'get_csrf_token', return_value="fake_token"):
            # Mock successful booking response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {"referenceNumber": "123456"}
        }
        mock_session.post.return_value = mock_response

        status, ref = agent.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == "SUCCESS"
        assert ref == "123456"

def test_book_room_failure(agent, mock_session):
    agent.is_logged_in = True
    
    with patch.object(agent, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        # Generic error
        mock_response.json.return_value = {"success": False}
        mock_session.post.return_value = mock_response

        status, ref = agent.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == "ERROR"
        assert ref is None

def test_book_room_room_taken(agent, mock_session):
    agent.is_logged_in = True
    
    with patch.object(agent, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        # Simulation of "Room Taken" error response structure
        mock_response.json.return_value = {
            "success": False,
            "data": {"errors": ["conflicting"]}
        }
        mock_session.post.return_value = mock_response

        status, ref = agent.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == "ROOM_TAKEN"
        assert ref is None

def test_book_room_user_limit(agent, mock_session):
    agent.is_logged_in = True
    
    with patch.object(agent, 'get_csrf_token', return_value="fake_token"):
        mock_response = MagicMock()
        # Simulation of "User Limit" error
        mock_response.json.return_value = {
                "success": False,
                "data": {"errors": ["limited"]}
        }
        mock_session.post.return_value = mock_response

        status, ref = agent.book_room(123, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        
        assert status == "USER_LIMIT"
        assert ref is None

