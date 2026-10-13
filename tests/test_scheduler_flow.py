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
        self.mock_config.BOOKING_TIMEOUT_SECONDS = 120
        self.mock_config.BOOKING_RETRY_INTERVAL_SECONDS = 0.5
        
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

    def test_multi_ref_deletion_cancels_all_refs(self):
        """Test that deleting a merged event with multiple references cancels each reference."""
        req = DeletionRequest(
            event_id="evt_multi", summary="DELETE [P] Booking Ref: REF1 Ref: REF2",
            ref_num="REF1", ref_nums=["REF1", "REF2"],
            original_event={}, owner_email="test@tau.ac.il"
        )
        self.scheduler.calendar_manager.scan_for_deletions.return_value = [req]
        self.scheduler.booking_manager.cancel_booking.return_value = (True, "Deleted")

        self.scheduler._process_deletions()

        self.assertEqual(self.scheduler.booking_manager.cancel_booking.call_count, 2)
        called_refs = [c[0][0].ref_num for c in self.scheduler.booking_manager.cancel_booking.call_args_list]
        self.assertEqual(called_refs, ["REF1", "REF2"])
        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt_multi", CalendarStatus.DELETED, {}
        )

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
        
        # Since extend succeeded, the event is merged into the preceding event on GCal
        self.scheduler.calendar_manager.merge_adjacent_events.assert_called_once_with(
            orig_event, orig_event, "REF123", "Room 108", "agent@tau.ac.il"
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
        
        # Same room and user under 3h merges into previous booking
        self.scheduler.calendar_manager.merge_adjacent_events.assert_called_once_with(
            orig_event, orig_event, "REF456", "Room 108", "agent@tau.ac.il"
        )

    def test_consecutive_booking_merging_under_3_hours(self):
        """Test that consecutive bookings with same room and user under 3 hours are merged."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        req = BookingRequest(
            event_id="evt_h2", summary="Hour 2 Booking", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, 
            opening_time=now - timedelta(days=7)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        
        # Preceding 1-hour booking in Room 108 by User A
        prev_orig = {
            'start': {'dateTime': (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            'end': {'dateTime': start_dt}
        }
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            prev_orig, "REF100", "agent@tau.ac.il", "Room 108"
        )
        
        # Attempt booking succeeds with same room and same user
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("agent@tau.ac.il", "Room 108", "REF200")
        )
        self.mock_config.ALL_ROOMS = {125: "Room 108"}

        self.scheduler._scan_and_book_cycle()

        # Should attempt booking with preferred_room_id 125 and preferred_user_email agent@tau.ac.il
        args, kwargs = self.scheduler.booking_manager.attempt_booking.call_args
        self.assertEqual(kwargs.get('preferred_room_id'), 125)
        self.assertEqual(kwargs.get('preferred_user_email'), "agent@tau.ac.il")

        # Since 1h + 1h = 2h <= 3h, merge_adjacent_events should be called
        self.scheduler.calendar_manager.merge_adjacent_events.assert_called_once_with(
            prev_orig, orig_event, "REF200", "Room 108", "agent@tau.ac.il"
        )

    def test_consecutive_booking_exceeding_3_hours_stays_separate(self):
        """Test that consecutive bookings reaching >3 hours total do NOT merge and stay separate."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        req = BookingRequest(
            event_id="evt_h4", summary="Hour 4 Booking", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, 
            opening_time=now - timedelta(days=7)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        
        # Preceding 3-hour merged booking (10:00-13:00) in Room 108 by User A
        prev_orig = {
            'start': {'dateTime': (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            'end': {'dateTime': start_dt}
        }
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            prev_orig, "REF100", "agent@tau.ac.il", "Room 108"
        )
        
        # Attempt booking succeeds with same room and same user
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("agent@tau.ac.il", "Room 108", "REF400")
        )
        self.mock_config.ALL_ROOMS = {125: "Room 108"}

        self.scheduler._scan_and_book_cycle()

        # Preferred room should still be Room 108
        args, kwargs = self.scheduler.booking_manager.attempt_booking.call_args
        self.assertEqual(kwargs.get('preferred_room_id'), 125)

        # Merge should NOT be called because 3h + 1h = 4h > 3h!
        self.scheduler.calendar_manager.merge_adjacent_events.assert_not_called()

        # Should be updated to SUCCESS as an independent event
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "evt_h4", CalendarStatus.SUCCESS, orig_event,
            room_name="Room 108", ref_num="REF400", user="agent@tau.ac.il"
        )

    def test_independent_slots_process_independently(self):
        """Test that multiple slots process independently without combined degradation loops."""
        now = datetime.now(timezone.utc)
        req1 = BookingRequest(
            event_id="slot1", summary="Slot 1", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start="U1", utc_end="U2", original_event={}, opening_time=now - timedelta(days=1)
        )
        req2 = BookingRequest(
            event_id="slot2", summary="Slot 2", 
            start_time=now + timedelta(hours=1), end_time=now + timedelta(hours=2),
            utc_start="U2", utc_end="U3", original_event={}, opening_time=now - timedelta(days=1)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req1, req2]
        self.scheduler.calendar_manager.find_successful_booking.return_value = None
        
        # Slot 1 fails, Slot 2 succeeds
        self.scheduler.booking_manager.attempt_booking.side_effect = [
            (BookingResult.ROOM_TAKEN, "All Rooms Taken"),
            (BookingResult.SUCCESS, ("a@tau.ac.il", "Room 101", "REF_S2"))
        ]

        self.scheduler._scan_and_book_cycle()

        # attempt_booking called twice (once for each independent slot)
        self.assertEqual(self.scheduler.booking_manager.attempt_booking.call_count, 2)
        
        # Slot 1 marked as FAILURE
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "slot1", CalendarStatus.FAILURE, {}, reason="All Rooms Taken"
        )
        # Slot 2 marked as SUCCESS
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "slot2", CalendarStatus.SUCCESS, {}, room_name="Room 101", ref_num="REF_S2", user="a@tau.ac.il"
        )

    def test_booking_fails_with_user_permission_restriction_marks_calendar_failure(self):
        """When booking returns user limit/permission error, scheduler marks calendar event as FAILURE with exact reason."""
        now = datetime.now(timezone.utc)
        req = BookingRequest(
            event_id="slot_perm_fail", summary="Booking Study", 
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start="U1", utc_end="U2", original_event={}, opening_time=now - timedelta(days=1)
        )
        self.scheduler.calendar_manager.scan_for_bookings.return_value = [req]
        self.scheduler.calendar_manager.find_successful_booking.return_value = None
        
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.USER_LIMIT, "User Limit: You are not allowed to change this reservation."
        )

        self.scheduler._scan_and_book_cycle()

        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "slot_perm_fail", CalendarStatus.PROCESSING, {}
        )
        self.scheduler.calendar_manager.update_event_status.assert_any_call(
            "slot_perm_fail", CalendarStatus.FAILURE, {}, reason="User Limit: You are not allowed to change this reservation."
        )

if __name__ == '__main__':
    unittest.main()

