import pytest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scheduler as scheduler_module
import config
from booking_agent import BookingAgent

@pytest.fixture
def scheduler_instance():
    # Mock __init__ to verify logic without loading files
    with patch('os.path.exists', return_value=True), \
         patch('builtins.open', new_callable=MagicMock):
        sched = scheduler_module.Scheduler()
        sched.history = []
        return sched

@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.email = "test@example.com"
    return agent

@pytest.fixture
def setup_data(scheduler_instance, mock_agent):
    scheduler_instance.agents = [mock_agent]
    scheduler_instance.last_successful_user = None
    
    room_id = 125
    room_name = config.ALL_ROOMS[room_id]
    
    slot_start = datetime(2026, 2, 11, 10, 0, 0)
    slot = {
        'key': slot_start.strftime("%Y-%m-%d %H:%M"),
        'start': slot_start,
        'utc_start': "2026-02-11T08:00:00.000Z",
        'utc_end': "2026-02-11T09:00:00.000Z"
    }
    
    prev_utc_start = "2026-02-11T07:00:00.000Z"
    prev_utc_end = "2026-02-11T08:00:00.000Z"
    
    return {
        'scheduler': scheduler_instance,
        'agent': mock_agent,
        'room_id': room_id,
        'room_name': room_name,
        'slot': slot,
        'prev_utc_start': prev_utc_start,
        'prev_utc_end': prev_utc_end
    }

def test_extend_booking_success(setup_data):
    sched = setup_data['scheduler']
    agent = setup_data['agent']
    room_name = setup_data['room_name']
    prev_utc_start = setup_data['prev_utc_start']
    prev_utc_end = setup_data['prev_utc_end']
    slot = setup_data['slot']

    # Setup History
    sched.history = [{
        "user": "test@example.com",
        "room": room_name,
        "ref_num": "REF123",
        "start_utc": prev_utc_start,
        "end_utc": prev_utc_end,
        "status": "SUCCESS"
    }]

    # Mock success
    agent.extend_booking.return_value = ("SUCCESS", "REF123")
    
    # Mock _save_history
    sched._save_history = MagicMock()

    result = sched.attempt_booking(slot)

    assert result is True
    agent.extend_booking.assert_called_with(
        setup_data['room_id'], 
        "REF123", 
        prev_utc_start, 
        slot['utc_end']
    )
    agent.book_room.assert_not_called()
    
    sched._save_history.assert_called()
    call_args = sched._save_history.call_args[0][0]
    assert call_args['ref_num'] == "REF123"
    assert call_args['start_utc'] == prev_utc_start
    assert call_args['end_utc'] == slot['utc_end']

def test_extend_booking_fail_fallback_create(setup_data):
    sched = setup_data['scheduler']
    agent = setup_data['agent']
    room_name = setup_data['room_name']
    prev_utc_start = setup_data['prev_utc_start']
    prev_utc_end = setup_data['prev_utc_end']
    slot = setup_data['slot']

    # Setup History
    sched.history = [{
        "user": "test@example.com",
        "room": room_name,
        "ref_num": "REF123",
        "start_utc": prev_utc_start,
        "end_utc": prev_utc_end,
        "status": "SUCCESS"
    }]

    # Mock fail then success
    agent.extend_booking.return_value = ("USER_LIMIT", None)
    agent.book_room.return_value = ("SUCCESS", "REF456")
    
    sched._save_history = MagicMock()

    result = sched.attempt_booking(slot)

    assert result is True
    agent.extend_booking.assert_called()
    agent.book_room.assert_called_with(
        setup_data['room_id'],
        slot['utc_start'],
        slot['utc_end']
    )
    
    call_args = sched._save_history.call_args[0][0]
    assert call_args['ref_num'] == "REF456"
    assert call_args['start_utc'] == slot['utc_start']

def test_no_consecutive_booking_creates_new(setup_data):
    sched = setup_data['scheduler']
    agent = setup_data['agent']
    slot = setup_data['slot']

    sched.history = []
    
    agent.book_room.return_value = ("SUCCESS", "REF789")
    sched._save_history = MagicMock()

    result = sched.attempt_booking(slot)

    assert result is True
    agent.extend_booking.assert_not_called()
    agent.book_room.assert_called()
    
    call_args = sched._save_history.call_args[0][0]
    assert call_args['ref_num'] == "REF789"

def test_consecutive_check_respects_user_and_room(setup_data):
    sched = setup_data['scheduler']
    agent = setup_data['agent']
    room_name = setup_data['room_name']
    prev_utc_end = setup_data['prev_utc_end']
    slot = setup_data['slot']

    sched.history = [
        { # Different user
            "user": "other@example.com",
            "room": room_name,
            "ref_num": "REF111",
            "end_utc": prev_utc_end,
            "status": "SUCCESS",
            "ref_num": "REF111"
        },
        { # Different room
            "user": "test@example.com",
            "room": "Some Other Room",
            "ref_num": "REF222",
            "end_utc": prev_utc_end,
            "status": "SUCCESS",
             "ref_num": "REF222"
        }
    ]
    
    agent.book_room.return_value = ("SUCCESS", "REF999")
    sched._save_history = MagicMock()

    sched.attempt_booking(slot)

    agent.extend_booking.assert_not_called()
    agent.book_room.assert_called()
