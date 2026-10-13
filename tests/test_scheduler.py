import unittest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta, timezone
import time

from scheduler import Scheduler
from core.enums import BookingResult, CalendarStatus
from core.entities import BookingRequest, DeletionRequest

class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.config_patcher = patch('scheduler.config')
        self.mock_config = self.config_patcher.start()
        self.mock_config.FINAL_WAKE_UP_SECONDS_BEFORE_OPENING = 5
        self.mock_config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING = 60
        self.mock_config.MAX_SINGLE_BOOKING_DURATION_HOURS = 3.0
        self.mock_config.CALENDAR_POLL_INTERVAL_SECONDS = 300
        self.mock_config.BOOKING_TIMEOUT_SECONDS = 120
        self.mock_config.BOOKING_RETRY_INTERVAL_SECONDS = 0.01
        self.mock_config.ALL_ROOMS = {125: "Room 108", 126: "Room 109"}

        self.gc_patcher = patch('scheduler.GoogleCalendarClient')
        self.gc_mock = self.gc_patcher.start()

        self.am_patcher = patch('scheduler.AgentManager')
        self.am_mock = self.am_patcher.start()

        self.state_patcher = patch('scheduler.state')
        self.mock_state = self.state_patcher.start()

        self.scheduler = Scheduler()
        self.scheduler.calendar_manager = MagicMock()
        self.scheduler.booking_manager = MagicMock()
        self.scheduler.syncer = MagicMock()

    def tearDown(self):
        self.config_patcher.stop()
        self.gc_patcher.stop()
        self.am_patcher.stop()
        self.state_patcher.stop()

    def test_extend_retries_on_too_early_then_succeeds(self):
        """Extend loop retries on Too Early and succeeds once the window opens."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'id': 'evt1', 'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        prev_event = {'id': 'evt_prev', 'start': {'dateTime': (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}, 'end': {'dateTime': start_dt}}
        
        req = BookingRequest(
            event_id="evt1", summary="Booking",
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, opening_time=now - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            prev_event, "REF100", "user@tau.ac.il", "Room 108"
        )
        self.scheduler.calendar_manager.merge_adjacent_events.return_value = True

        mock_agent = MagicMock()
        mock_agent.update_booking.side_effect = [
            (False, "Too early to book"),
            (True, "Extended successfully")
        ]
        self.scheduler.agent_manager.get_agent.return_value = mock_agent

        self.scheduler._process_single_booking(req)

        self.assertEqual(mock_agent.update_booking.call_count, 2)
        self.scheduler.calendar_manager.merge_adjacent_events.assert_called_once_with(
            prev_event, orig_event, "REF100", "Room 108", "user@tau.ac.il"
        )

    def test_extend_merge_failure_falls_back_to_single_event_success(self):
        """If extend succeeds on the university server but merge_adjacent_events fails, event is marked SUCCESS (not yellow)."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'id': 'evt1', 'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        prev_event = {'id': 'evt_prev', 'start': {'dateTime': (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}, 'end': {'dateTime': start_dt}}
        
        req = BookingRequest(
            event_id="evt1", summary="Booking",
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, opening_time=now - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            prev_event, "REF100", "user@tau.ac.il", "Room 108"
        )
        # Merge fails on Google Calendar
        self.scheduler.calendar_manager.merge_adjacent_events.return_value = False

        mock_agent = MagicMock()
        mock_agent.update_booking.return_value = (True, "Success")
        self.scheduler.agent_manager.get_agent.return_value = mock_agent

        self.scheduler._process_single_booking(req)

        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt1", CalendarStatus.SUCCESS, orig_event,
            room_name="Room 108", ref_num="REF100", user="user@tau.ac.il"
        )

    def test_new_booking_merge_failure_falls_back_to_single_event_success(self):
        """If new booking succeeds and is eligible to merge, but merge_adjacent_events fails, marks single event SUCCESS."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'id': 'evt2', 'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        prev_event = {'id': 'evt_prev', 'start': {'dateTime': (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}, 'end': {'dateTime': start_dt}}

        req = BookingRequest(
            event_id="evt2", summary="Booking",
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, opening_time=now - timedelta(seconds=1)
        )
        self.scheduler.calendar_manager.find_successful_booking.return_value = (
            prev_event, "REF100", "user@tau.ac.il", "Room 108"
        )
        # Extend is not possible or fails
        mock_agent = MagicMock()
        mock_agent.update_booking.return_value = (False, "Capacity")
        self.scheduler.agent_manager.get_agent.return_value = mock_agent

        # New booking succeeds with same user and room
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("user@tau.ac.il", "Room 108", "REF200")
        )
        # Merge fails on Google Calendar API
        self.scheduler.calendar_manager.merge_adjacent_events.return_value = False

        self.scheduler._process_single_booking(req)

        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt2", CalendarStatus.SUCCESS, orig_event,
            room_name="Room 108", ref_num="REF200", user="user@tau.ac.il"
        )

    def test_boundary_hour_4_stays_independent_event(self):
        """If accumulated hours is 3.0, a 4th hour cannot extend or merge, and stays an independent SUCCESS event."""
        now = datetime.now(timezone.utc)
        start_dt = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        orig_event = {'id': 'evt_h4', 'start': {'dateTime': start_dt}, 'end': {'dateTime': (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")}}
        prev_event = {
            'id': 'evt_3h_merged',
            'start': {'dateTime': (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")},
            'end': {'dateTime': start_dt}
        }
        req = BookingRequest(
            event_id="evt_h4", summary="Hour 4",
            start_time=now, end_time=now + timedelta(hours=1),
            utc_start=start_dt, utc_end=(now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=orig_event, opening_time=now - timedelta(seconds=1)
        )
        # Preceding booking already 3.0 hours
        from services.calendar_manager import PrecedingBooking
        self.scheduler.calendar_manager.find_successful_booking.return_value = PrecedingBooking(
            prev_event, "REF3H", "user@tau.ac.il", "Room 108", duration_hours=3.0
        )

        mock_agent = MagicMock()
        self.scheduler.agent_manager.get_agent.return_value = mock_agent

        # Booking succeeds with same room and user
        self.scheduler.booking_manager.attempt_booking.return_value = (
            BookingResult.SUCCESS, ("user@tau.ac.il", "Room 108", "REF4H")
        )

        self.scheduler._process_single_booking(req)

        # Extend must NOT be attempted (3h + 1h = 4h > 3h)
        mock_agent.update_booking.assert_not_called()
        # Merge must NOT be attempted
        self.scheduler.calendar_manager.merge_adjacent_events.assert_not_called()
        # Kept as independent SUCCESS event
        self.scheduler.calendar_manager.update_event_status.assert_called_with(
            "evt_h4", CalendarStatus.SUCCESS, orig_event,
            room_name="Room 108", ref_num="REF4H", user="user@tau.ac.il"
        )

    def test_multi_ref_deletion_partial_failure_sets_status_failure(self):
        """When deleting multi-ref event, if one ref fails, the calendar event is marked FAILURE with reasons."""
        req = DeletionRequest(
            event_id="evt_del", summary="DELETE [P] Booking Ref: REF1 Ref: REF2",
            ref_num="REF1", ref_nums=["REF1", "REF2"],
            original_event={}, owner_email="user@tau.ac.il"
        )
        self.scheduler.calendar_manager.scan_for_deletions.return_value = [req]
        self.scheduler.booking_manager.cancel_booking.side_effect = [
            (True, "Deleted"),
            (False, "Permission Denied")
        ]

        self.scheduler._process_deletions()

        self.scheduler.calendar_manager.update_event_status.assert_called_once_with(
            "evt_del", CalendarStatus.FAILURE, {}, reason="Delete Failed: REF2: Permission Denied"
        )

if __name__ == '__main__':
    unittest.main()
