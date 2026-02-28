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
        self.assertEqual(agent1.book_room.call_count, 1) # Only tried Room 101
        self.assertEqual(agent2.book_room.call_count, 1) # Only tried Room 101

if __name__ == '__main__':
    unittest.main()
