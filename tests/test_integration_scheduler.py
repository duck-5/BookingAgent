import pytest
import os
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config
from clients.tau_client import TauClient
from core.entities import UserCredentials, BookingRequest
from core.enums import BookingResult
from services.agent_manager import AgentManager
from services.booking_manager import BookingManager
from tests.test_integration import real_agent_manager, _find_open_slot

# Ensure we're running with the --integration flag
pytestmark = pytest.mark.integration

logger = logging.getLogger(__name__)

def test_live_multi_hour_split(real_agent_manager):
    """
    Test the graceful degradation / splitting logic on the live server.
    We ask for a 4-hour booking (which is illegal, max is 3).
    The system should degrade to 3 hours, book it, and return SUCCESS.
    We then delete the 3-hour booking.
    """
    agent = real_agent_manager.get_rotational_agents()[0]
    
    # Find a 1-hour slot that is open. We will pretend the user asked for 4 hours starting here.
    # We only really need the first hour to be open to prove the system ATTEMPTS the 3 hour and succeeds 
    # (or degrades further if hour 2/3 are taken). 
    # But ideally we find a 3-hour open block. Since finding guaranteed 3h blocks is hard in a live test,
    # we will just trust the degradation loop: if 3h fails, it will try 2h, then 1h.
    # As long as it doesn't crash and returns SUCCESS (for *some* duration) and we can delete it, the logic works.
    
    slot = _find_open_slot(agent)
    if not slot:
        pytest.skip("Could not find any available test slots on the live server.")
        
    room_id, start_time, _ = slot
    
    # Create a request for 4 hours
    end_time_4h = start_time + timedelta(hours=4)
    start_utc_str = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_utc_str = end_time_4h.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    # Mock original event Google Calendar data
    mock_orig_event = {
        'id': 'test_integration_event_123',
        'summary': 'Booking: Test Split',
        'start': {'dateTime': start_utc_str},
        'end': {'dateTime': end_utc_str}
    }
    
    req = BookingRequest(
        event_id='test_integration_event_123',
        summary='Booking: Test Split',
        start_time=start_time,
        end_time=end_time_4h,
        utc_start=start_utc_str,
        utc_end=end_utc_str,
        original_event=mock_orig_event,
        opening_time=start_time - timedelta(days=7) + timedelta(hours=1)
    )
    
    booking_manager = BookingManager(real_agent_manager)
    stop_event = threading.Event()
    
    logger.info(f"Test attempting 4-hour split booking starting at {start_utc_str}")
    
    # We pass preferred_room_id to force it to try the room we know has at least 1 hour open
    res, details = booking_manager.attempt_booking(req, stop_event=stop_event, preferred_room_id=room_id)
    
    # It might degrade down to 1 hour if hours 2,3,4 are taken. But it should return SUCCESS.
    if res in [BookingResult.ROOM_TAKEN, BookingResult.USER_LIMIT]:
        pytest.skip(f"Could not complete integration test due to live constraints: {res}")
    elif res == BookingResult.ERROR and "Timeout" in str(details):
        pytest.skip("BookingManager hit timeout due to constant rate limits.")
    elif res == BookingResult.CLOSED:
        pytest.skip("Server marked this slot as CLOSED and unavailable for booking.")
        
    assert res == BookingResult.SUCCESS, f"Split booking failed: {res}, {details}"
    
    agent_email, booked_room, ref_num = details
    assert ref_num, "No reference number returned from successful split booking"
    
    # Teardown
    logger.info(f"Test attempting to delete split booking {ref_num}")
    del_res, del_msg = agent.delete_booking(ref_num)
    assert del_res is True, f"Failed to clean up split booking: {del_msg}"
