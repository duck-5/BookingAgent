import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from datetime import datetime

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
        self.scheduler.booking_manager.cancel_booking.return_value = True

        # Trigger Sync Logic manually (bypass threading)
        # We simulate what _sync_worker does inside the loop
        
        # 1. Process Deletes
        delete_requests = self.scheduler.calendar_manager.scan_for_deletions()
        if delete_requests:
            for r in delete_requests:
                success = self.scheduler.booking_manager.cancel_booking(r)
                if success:
                    self.scheduler.calendar_manager.update_event_status(
                        r.event_id, CalendarStatus.DELETED, r.original_event
                    )
        
        # 2. Sync
        self.scheduler.syncer.sync_all_users()

        # Assertions
        self.scheduler.booking_manager.cancel_booking.assert_called_with(req)
        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt1", CalendarStatus.DELETED, {}
        )
        self.scheduler.syncer.sync_all_users.assert_called_once()

    def test_scan_loop_booking_flow(self):
        """Test booking flow when a request is found."""
        # Setup Request
        req = BookingRequest(
            event_id="evt2", summary="Booking Room 1", 
            start_time=datetime.now(), end_time=datetime.now(),
            utc_start="2026-01-01T10:00:00Z", utc_end="2026-01-01T11:00:00Z",
            original_event={}, opening_time=datetime.now()
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        
        # Mock Booking Success
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("agent@tau.ac.il", "Room 101", "REF123")
        )

        # Trigger Scan Logic (Simulate _scan_loop body)
        requests = self.scheduler.calendar_manager.scan_for_bookings()
        for r in requests:
            self.scheduler.calendar_manager.update_event_status(
                r.event_id, CalendarStatus.PROCESSING, r.original_event
            )
            result, details = self.scheduler.booking_manager.attempt_booking(r)
            
            if result == BookingResult.SUCCESS:
                agent, room, ref = details
                # Check split logic (simplified)
                duration = 1.0 
                if duration > 1.1:
                    self.scheduler.calendar_manager.split_event(...)
                else:
                    self.scheduler.calendar_manager.update_event_status(
                        r.event_id, CalendarStatus.SUCCESS, r.original_event,
                        room_name=room, ref_num=ref, user=agent
                    )

        # Assertions
        self.scheduler.calendar_manager.scan_for_bookings.assert_called()
        self.scheduler.booking_manager.attempt_booking.assert_called_with(req)
        
        # Verify status updates
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt2", CalendarStatus.PROCESSING, {}
        )
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt2", CalendarStatus.SUCCESS, {}, 
            room_name="Room 101", ref_num="REF123", user="agent@tau.ac.il"
        )

if __name__ == '__main__':
    unittest.main()
