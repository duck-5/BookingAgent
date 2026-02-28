import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime, timedelta, timezone

# Setup path
sys.path.insert(0, os.getcwd())

from scheduler import Scheduler
from core.enums import BookingResult, CalendarStatus
from core.entities import BookingRequest, DeletionRequest

class TestSchedulerFlow(unittest.TestCase):
    
    def setUp(self):
        # Patch config to avoid file loading issues
        self.config_patcher = patch('scheduler.config')
        self.mock_config = self.config_patcher.start()
        self.mock_config.FINAL_WAKE_UP_SECONDS_BEFORE_OPENING = 5
        self.mock_config.MAX_SINGLE_BOOKING_DURATION_HOURS = 3.0
        self.mock_config.CALENDAR_POLL_INTERVAL_SECONDS = 300
        
        # Patch External Clients
        self.gc_patcher = patch('scheduler.GoogleCalendarClient')
        self.gc_mock = self.gc_patcher.start()
        
        self.am_patcher = patch('scheduler.AgentManager')
        self.am_mock = self.am_patcher.start()
        
        # Patch Dashboard state to avoid threading issues or UI logic
        self.state_patcher = patch('scheduler.state')
        self.mock_state = self.state_patcher.start()

        # Instantiate Scheduler
        self.scheduler = Scheduler()
        
        # Mock internal managers which are initialized in __init__
        self.scheduler.calendar_manager = MagicMock()
        self.scheduler.booking_manager = MagicMock()
        self.scheduler.syncer = MagicMock()

    def tearDown(self):
        self.config_patcher.stop()
        self.gc_patcher.stop()
        self.am_patcher.stop()
        self.state_patcher.stop()

    def test_sync_cycle_processing(self):
        """Test that sync cycle processes deletes and runs syncer."""
        # Setup Mocks
        req = DeletionRequest(
            event_id="evt1", summary="DELETE Ref: 123", ref_num="123", 
            original_event={}, owner_email="test@tau.ac.il"
        )
        self.scheduler.calendar_manager.scan_for_deletions.return_value = [req]
        self.scheduler.booking_manager.cancel_booking.return_value = (True, "Deleted")

        # 1. Process Deletes
        self.scheduler._process_deletions()
        
        # Verify Deletion Logic
        self.scheduler.calendar_manager.scan_for_deletions.assert_called_once()
        self.scheduler.booking_manager.cancel_booking.assert_called_with(req)
        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt1", CalendarStatus.DELETED, {}
        )
        
        # 2. Sync
        self.scheduler._perform_sync()
        
        # Verify Sync Logic
        self.scheduler.syncer.sync_all_users.assert_called_once()

    def test_scan_loop_booking_flow(self):
        """Test booking flow when a request is found."""
        # Setup Request
        # Note: _scan_and_book_cycle filters out requests not yet open.
        # We set opening_time to now-1s to ensure it's actionable.
        orig_event = {'end': {'dateTime': datetime.now(timezone.utc).isoformat()}}
        req = BookingRequest(
            event_id="evt2", summary="Booking Room 1", 
            start_time=datetime.now(), end_time=datetime.now(),
            utc_start="2026-01-01T10:00:00Z", utc_end="2026-01-01T11:00:00Z",
            original_event=orig_event, 
            opening_time=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        self.scheduler.calendar_manager.find_successful_booking.return_value = None
        
        # Mock Booking Success
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("agent@tau.ac.il", "Room 101", "REF123")
        )

        # Trigger Scan Logic
        self.scheduler._scan_and_book_cycle()

        # Assertions
        self.scheduler.calendar_manager.scan_for_bookings.assert_called()
        args, kwargs = self.scheduler.booking_manager.attempt_booking.call_args
        self.assertEqual(args[0], req)
        self.assertEqual(kwargs.get('stop_event'), self.scheduler._stop_event)
        
        # Verify status updates
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt2", CalendarStatus.PROCESSING, orig_event
        )
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt2", CalendarStatus.SUCCESS, orig_event, 
            room_name="Room 101", ref_num="REF123", user="agent@tau.ac.il"
        )

    def test_extend_booking_flow(self):
        """Test extending an existing booking."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'start': {'dateTime': start_dt}, 'end': {'dateTime': start_dt}}
        req = BookingRequest(
            event_id="evt3", summary="Extend Room 1", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start="2026-01-01T11:00:00Z", utc_end="2026-01-01T12:00:00Z",
            original_event=orig_event, 
            opening_time=now - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        
        # Mock finding a successful past booking
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            orig_event, "REF123", "agent@tau.ac.il", "Room 108"
        )
        
        # Mock Agent Manager returning an agent
        mock_agent = MagicMock()
        mock_agent.is_logged_in = True
        mock_agent.update_booking.return_value = (True, "Success")
        self.scheduler.agent_manager.get_agent.return_value = mock_agent

        # Config mock for room ID lookup
        self.mock_config.ALL_ROOMS = {125: "Room 108"}
        self.scheduler._scan_and_book_cycle()

        # Assertions
        mock_agent.update_booking.assert_called_once_with("REF123", 125, start_dt, "2026-01-01T12:00:00Z")
        
        # Since the _scan_and_book_cycle checks if req_end_utc < dt_orig_end for split, we bypass that here
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt3", CalendarStatus.SUCCESS, orig_event, 
            room_name="Room 108", ref_num="REF123", user="agent@tau.ac.il"
        )

    def test_consecutive_booking_fallback(self):
        """Test fallback when extend fails but room booking succeeds."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'start': {'dateTime': start_dt}, 'end': {'dateTime': start_dt}}
        req = BookingRequest(
            event_id="evt4", summary="Booking Room 1", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start="2026-01-01T11:00:00Z", utc_end="2026-01-01T12:00:00Z",
            original_event=orig_event, 
            opening_time=now - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        
        # Mock finding a successful past booking
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            orig_event, "REF123", "agent@tau.ac.il", "Room 108"
        )
        
        # Agent extend FAILS
        mock_agent = MagicMock()
        mock_agent.is_logged_in = True
        mock_agent.update_booking.return_value = (False, "Too late")
        self.scheduler.agent_manager.get_agent.return_value = mock_agent
        
        # Attempt booking SUCCEEDS for same room
        # We need to spy on attempt_booking
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("agent@tau.ac.il", "Room 108", "REF456")
        )

        self.mock_config.ALL_ROOMS = {125: "Room 108"}
        self.scheduler._scan_and_book_cycle()

        # Should have called agent.update_booking (it failed)
        mock_agent.update_booking.assert_called_once()
        
        # Should have called attempt_booking with preferred_room_id = 125
        args, kwargs = self.scheduler.booking_manager.attempt_booking.call_args
        self.assertEqual(args[0], req)
        self.assertEqual(kwargs.get('preferred_room_id'), 125)
        
        # Should mark success with NEW ref num (REF456)
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt4", CalendarStatus.SUCCESS, orig_event, 
            room_name="Room 108", ref_num="REF456", user="agent@tau.ac.il"
        )

if __name__ == '__main__':
    unittest.main()

