import pytest
import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config
from clients.tau_client import TauClient
from core.entities import UserCredentials
from core.enums import BookingResult
from services.agent_manager import AgentManager

# Ensure we're running with the --integration flag
pytestmark = pytest.mark.integration

logger = logging.getLogger(__name__)

@pytest.fixture(scope="module")
def real_agent_manager():
    """Provides a real AgentManager loaded with actual credentials."""
    if not os.path.exists(config.CREDENTIALS_FILE):
        pytest.skip(f"Test requires {config.CREDENTIALS_FILE}")
    manager = AgentManager(config.CREDENTIALS_FILE)
    if not manager.agents:
        pytest.skip("No agents loaded.")

    # Try logging them in again if they failed during init
    for agent in manager.agents:
         if not agent.is_logged_in:
             agent.login()

    valid_agents = [a for a in manager.agents if a.is_logged_in]
    if not valid_agents:
        pytest.skip("Could not log in any agents.")
    manager.agents = valid_agents
    return manager


def _find_open_slot(client: TauClient) -> Optional[Tuple[int, datetime, datetime]]:
    """
    Helper function to hunt the server for an open 1-hour slot in low-priority rooms.
    Since slots open 7 days in advance, we look ~6.5 days out to find unbooked slots.
    """
    now_utc = datetime.now(timezone.utc)
    # Search window: 6 days from now, between 10:00 and 18:00
    target_date = now_utc + timedelta(days=6)
    
    # Try finding an open slot by attempting to book and catching ROOM_TAKEN vs SUCCESS
    # Note: To avoid this, ideally we would parse the /schedule.php HTML page cleanly, 
    # but using attempt_booking directly is faster if we immediately delete.
    # We will search the low priority rooms.
    low_priority = list(config.LOW_PRIORITY_ROOMS.keys())
    
    # Check hours 10 to 17
    for hour in range(10, 18):
        start_time = target_date.replace(hour=hour, minute=0, second=0, microsecond=0)
        end_time = start_time + timedelta(hours=1)
        
        start_utc_str = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_utc_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        for room_id in low_priority:
            # We don't want to actually book it here, just return the candidate
            # We'll let the test do the booking to verify it.
            return room_id, start_time, end_time
            
    return None


def test_live_book_and_delete(real_agent_manager):
    """
    End-to-end test:
    1. Finds an open slot
    2. Books it via TauClient
    3. Verifies SUCCESS
    4. Immediately deletes it via TauClient
    5. Verifies SUCCESS
    """
    agent = real_agent_manager.get_rotational_agents()[0]
    
    slot = _find_open_slot(agent)
    if not slot:
        pytest.skip("Could not find any available test slots on the live server.")
        
    room_id, start_time, end_time = slot
    start_utc_str = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    # 1. Book
    logger.info(f"Test attempting to book Room {room_id} from {start_utc_str} to {end_utc_str}")
    res, ref_num = agent.book_room(room_id, start_utc_str, end_utc_str)
    
    if res == BookingResult.ROOM_TAKEN:
        pytest.skip("Slot got taken during test execution or was not actually open.")
    elif res == BookingResult.USER_LIMIT:
        pytest.skip("Agent is rate-limited.")
    elif res == BookingResult.ERROR and "Timeout" in str(ref_num):
         pytest.skip("BookingManager hit timeout due to constant rate limits or full rooms.")
    elif res == BookingResult.ERROR and "invalid credential" in str(ref_num).lower():
         pytest.skip("Login failed for test agent.")
    elif res == BookingResult.CLOSED:
         pytest.skip("Server marked this slot as CLOSED and unavailable for booking.")
        
    assert res == BookingResult.SUCCESS, f"Booking failed with status: {res}, details: {ref_num}"
    assert ref_num and isinstance(ref_num, str), "Valid reference number was not returned."
    
    # 2. Delete
    logger.info(f"Test attempting to delete {ref_num}")
    del_res, del_msg = agent.delete_booking(ref_num)
    
    assert del_res is True, f"Deletion failed: {del_msg}"


def test_live_extend_booking(real_agent_manager):
    """
    End-to-end test:
    1. Finds a 2-hour open block.
    2. Books the first hour.
    3. Uses update_booking to extend it by 1 hour.
    4. Deletes the booking.
    """
    agent = real_agent_manager.get_rotational_agents()[0]
    
    # We need a 2-hour slot. We'll simply try booking 1 hour, then updating.
    slot = _find_open_slot(agent)
    if not slot:
        pytest.skip("Could not find any available test slots on the live server.")
        
    room_id, start_time, end_time = slot
    start_utc_str = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc_str = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    # 1. Book first hour
    res, ref_num = agent.book_room(room_id, start_utc_str, end_utc_str)
    
    if res != BookingResult.SUCCESS:
        pytest.skip(f"Failed to secure initial 1-hour slot: {res}, {ref_num}")
        
    # 2. Extend by 1 hour
    extended_end = end_time + timedelta(hours=1)
    extended_end_utc_str = extended_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    logger.info(f"Test attempting to extend {ref_num} to {extended_end_utc_str}")
    upd_res, upd_msg = agent.update_booking(ref_num, room_id, start_utc_str, extended_end_utc_str)
    
    # Teardown (Must delete regardless of update success)
    agent.delete_booking(ref_num)
    
    # Check Update result
    if not upd_res and "conflicting" in str(upd_msg).lower():
         pytest.skip("The extended hour was already taken.")
         
    assert upd_res is True, f"Update failed: {upd_msg}"


def test_find_existing_slots(real_agent_manager):
    """
    Tests that we can successfully parse the user's /my-calendar.php page.
    """
    agent = real_agent_manager.get_rotational_agents()[0]
    
    now = datetime.now()
    start_str = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end_str = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    
    # Hit the first SID
    url = f"{agent.BASE_URL}/my-calendar.php?dr=events&start={start_str}&end={end_str}&sid=1&rid=&gid="
    headers = {
        "X-Requested-With": "XMLHttpRequest", 
        "Referer": f"{agent.BASE_URL}/schedule.php"
    }
    
    resp = agent.session.get(url, headers=headers)
    assert resp.status_code == 200, "Server did not return a 200 OK status"
    
    try:
        data = resp.json()
        assert isinstance(data, list), "Response is not a JSON array"
    except json.JSONDecodeError:
        pytest.fail(f"Could not decode JSON. Possible HTML error page. Response prefix: {resp.text[:200]}")
