import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.getcwd())

from services.booking_manager import BookingManager
from core.enums import BookingResult
from core.entities import BookingRequest

class TestBookingManager(unittest.TestCase):
    
    def setUp(self):
        # Patch config
        self.config_patcher = patch('services.booking_manager.config')
        self.mock_config = self.config_patcher.start()
        
        # Rooms
        self.mock_config.HIGH_PRIORITY_ROOMS = {101: "Room A", 102: "Room B"}
        self.mock_config.LOW_PRIORITY_ROOMS = {201: "Room C"}
        self.mock_config.ALL_ROOMS = {101: "Room A", 102: "Room B", 201: "Room C"}
        self.mock_config.BOOKING_TIMEOUT_SECONDS = 5
        self.mock_config.BOOKING_RETRY_INTERVAL_SECONDS = 0.01

        # Mocks
        self.agent_manager_mock = MagicMock()
        self.bm = BookingManager(self.agent_manager_mock)
        
        # Request
        self.req = BookingRequest(
            event_id="evt1",
            summary="Test",
            start_time=datetime.now(timezone.utc),
            end_time=datetime.now(timezone.utc),
            utc_start="U1",
            utc_end="U2",
            original_event={},
            opening_time=datetime.now(timezone.utc)
        )

    def tearDown(self):
        self.config_patcher.stop()

    def test_agent_limit_moves_to_next_agent_same_room(self):
        """If an agent hits USER_LIMIT, it should try the next agent on the SAME root prioritizing order."""
        agent1 = MagicMock()
        agent1.email = "agent1@tau.ac.il"
        agent2 = MagicMock()
        agent2.email = "agent2@tau.ac.il"
        
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1, agent2]
        
        # Agent 1 fails with USER_LIMIT on 101
        # Agent 2 succeeds on 101
        
        # We need to configure side_effect for Bookings.
        # agent.book_room(rid, start, end)
        
        def agent1_book(rid, start, end):
            if rid == 101:
                return BookingResult.USER_LIMIT, "Quota Hit"
            return BookingResult.ERROR, "Should not reach here"

        def agent2_book(rid, start, end):
            if rid == 101:
                return BookingResult.SUCCESS, "REF123"
            return BookingResult.ERROR, "Should not reach here"
            
        agent1.book_room.side_effect = agent1_book
        agent2.book_room.side_effect = agent2_book
        
        res, details = self.bm.attempt_booking(self.req)
        
        self.assertEqual(res, BookingResult.SUCCESS)
        self.assertEqual(details[0], "agent2@tau.ac.il")
        self.assertEqual(details[1], "Room A") # Still Room 101
        
        # Checks
        agent1.book_room.assert_called_once_with(101, "U1", "U2")
        agent2.book_room.assert_called_once_with(101, "U1", "U2")

    def test_room_taken_moves_to_next_room_from_first_agent(self):
        """If a room is taken, it should try the next room starting from the FIRST available agent."""
        agent1 = MagicMock()
        agent1.email = "a@tau.ac.il"
        agent2 = MagicMock()
        agent2.email = "b@tau.ac.il"
        
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1, agent2]
        
        def a1_book(rid, start, end):
            if rid == 101: return BookingResult.ROOM_TAKEN, "Taken"
            if rid == 102: return BookingResult.SUCCESS, "REF102"
            return BookingResult.ERROR, "Unreachable"

        def a2_book(rid, start, end):
             return BookingResult.ERROR, "Unreachable"
             
        agent1.book_room.side_effect = a1_book
        agent2.book_room.side_effect = a2_book
        
        res, details = self.bm.attempt_booking(self.req)
        
        self.assertEqual(res, BookingResult.SUCCESS)
        self.assertEqual(details[0], "a@tau.ac.il")
        self.assertEqual(details[1], "Room B") # Next Room
        
        # Agent 1 was called twice (for 101 and 102). Agent 2 was never called.
        self.assertEqual(agent1.book_room.call_count, 2)
        agent2.book_room.assert_not_called()

    def test_all_agents_exhausted(self):
        """If all agents hit USER_LIMIT without success, it should fail immediately."""
        agent1 = MagicMock()
        agent1.email = "a1@tau.ac.il"
        agent2 = MagicMock()
        agent2.email = "a2@tau.ac.il"
        
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1, agent2]
        
        agent1.book_room.return_value = (BookingResult.USER_LIMIT, "Limit")
        agent2.book_room.return_value = (BookingResult.USER_LIMIT, "Limit")
        
        res, details = self.bm.attempt_booking(self.req)
        
        self.assertEqual(res, BookingResult.USER_LIMIT)
        # Should not have kept rotating through rooms since all agents are exhausted
    def test_too_early_retries_and_succeeds(self):
        """If server returns TOO_EARLY, it should sleep and retry the same agent/room until SUCCESS."""
        agent1 = MagicMock()
        agent1.email = "a1@tau.ac.il"
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1]
        
        # Returns TOO_EARLY twice, then SUCCESS
        agent1.book_room.side_effect = [
            (BookingResult.TOO_EARLY, "Too early"),
            (BookingResult.TOO_EARLY, "Too early"),
            (BookingResult.SUCCESS, "REF999")
        ]
        
        res, details = self.bm.attempt_booking(self.req)
        
        self.assertEqual(res, BookingResult.SUCCESS)
        self.assertEqual(details, ("a1@tau.ac.il", "Room A", "REF999"))
        self.assertEqual(agent1.book_room.call_count, 3)

    def test_too_early_stops_on_stop_event(self):
        """If stop_event is signaled during TOO_EARLY retries, it should abort cleanly."""
        import threading
        stop_evt = threading.Event()
        
        agent1 = MagicMock()
        agent1.email = "a1@tau.ac.il"
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1]
        
        def book_side_effect(rid, s, e):
            stop_evt.set() # Signal stop on first call
            return (BookingResult.TOO_EARLY, "Too early")
            
        agent1.book_room.side_effect = book_side_effect
        
        res, details = self.bm.attempt_booking(self.req, stop_event=stop_evt)
        self.assertEqual(res, BookingResult.ERROR)
        self.assertEqual(details, "Systems Stopping")

    def test_too_long_moves_to_next_agent_same_room(self):
        """If an agent hits TOO_LONG (3-hour limit), it should exhaust agent and try next agent on same room."""
        agent1 = MagicMock()
        agent1.email = "agent1@tau.ac.il"
        agent2 = MagicMock()
        agent2.email = "agent2@tau.ac.il"
        
        self.agent_manager_mock.get_rotational_agents.return_value = [agent1, agent2]
        
        agent1.book_room.return_value = (BookingResult.TOO_LONG, "3-Hour Limit Exceeded")
        agent2.book_room.return_value = (BookingResult.SUCCESS, "REF_TL_OK")
        
        res, details = self.bm.attempt_booking(self.req)
        
        self.assertEqual(res, BookingResult.SUCCESS)
        self.assertEqual(details[0], "agent2@tau.ac.il")
        self.assertEqual(details[1], "Room A")
        self.assertEqual(details[2], "REF_TL_OK")

    def test_preferred_user_not_logged_in_is_prepended(self):
        """If preferred user is not logged in (absent from rotational agents), get_agent should still prepend them."""
        logged_in_agent = MagicMock()
        logged_in_agent.email = "logged_in@tau.ac.il"
        self.agent_manager_mock.get_rotational_agents.return_value = [logged_in_agent]
        
        pref_agent = MagicMock()
        pref_agent.email = "preferred@tau.ac.il"
        self.agent_manager_mock.get_agent.return_value = pref_agent
        
        pref_agent.book_room.return_value = (BookingResult.SUCCESS, "REF_PREF")
        
        res, details = self.bm.attempt_booking(self.req, preferred_user_email="preferred@tau.ac.il")
        
        self.assertEqual(res, BookingResult.SUCCESS)
        self.assertEqual(details[0], "preferred@tau.ac.il")
        self.agent_manager_mock.get_agent.assert_called_with("preferred@tau.ac.il")

    def test_cancel_booking_owner_direct_match(self):
        """cancel_booking tries the owner agent first if owner_email is provided."""
        from core.entities import DeletionRequest
        owner_agent = MagicMock()
        owner_agent.email = "owner@tau.ac.il"
        owner_agent.delete_booking.return_value = (True, "Deleted")
        self.agent_manager_mock.get_agent.return_value = owner_agent

        del_req = DeletionRequest(
            event_id="e1", summary="DELETE", ref_num="REF99",
            original_event={}, owner_email="owner@tau.ac.il"
        )
        success, reason = self.bm.cancel_booking(del_req)

        self.assertTrue(success)
        self.assertEqual(reason, "Deleted")
        self.agent_manager_mock.get_agent.assert_called_once_with("owner@tau.ac.il")
        owner_agent.delete_booking.assert_called_once_with("REF99")

    def test_cancel_booking_fallback_when_no_owner(self):
        """cancel_booking falls back to trying all agents when owner_email is None or unknown."""
        from core.entities import DeletionRequest
        self.agent_manager_mock.get_agent.return_value = None
        agent1 = MagicMock()
        agent1.email = "a1@tau.ac.il"
        agent1.delete_booking.return_value = (False, "Not found")
        agent2 = MagicMock()
        agent2.email = "a2@tau.ac.il"
        agent2.delete_booking.return_value = (True, "Deleted")
        self.agent_manager_mock.get_all_agents.return_value = [agent1, agent2]

        del_req = DeletionRequest(
            event_id="e2", summary="DELETE", ref_num="REF88",
            original_event={}, owner_email=None
        )
        success, reason = self.bm.cancel_booking(del_req)

        self.assertTrue(success)
        self.assertEqual(reason, "Deleted")
        agent1.delete_booking.assert_called_once_with("REF88")
        agent2.delete_booking.assert_called_once_with("REF88")

    def test_attempt_booking_unconfigured_preferred_room_fallback(self):
        """attempt_booking handles unconfigured room IDs gracefully without KeyError."""
        agent = MagicMock()
        agent.email = "agent@tau.ac.il"
        agent.book_room.return_value = (BookingResult.SUCCESS, "REF999")
        self.agent_manager_mock.get_rotational_agents.return_value = [agent]
        self.agent_manager_mock.get_agent.return_value = agent

        # preferred_room_id 999 is not in ALL_ROOMS
        res, details = self.bm.attempt_booking(self.req, preferred_room_id=999)

        self.assertEqual(res, BookingResult.SUCCESS)
        agent_email, room_name, ref_num = details
        self.assertEqual(agent_email, "agent@tau.ac.il")
        self.assertEqual(room_name, "Room 999")
        self.assertEqual(ref_num, "REF999")

if __name__ == '__main__':
    unittest.main()
