import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from core.enums import BookingResult, CalendarStatus
from core.entities import BookingRequest, DeletionRequest
from services.agent_manager import AgentManager
from services.calendar_manager import CalendarManager, parse_iso_datetime
from services.booking_manager import BookingManager
from clients.google_calendar import GoogleCalendarClient, CalendarSync
from dashboard.state import state

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        # Initialize Services
        self.agent_manager = AgentManager(config.CREDENTIALS_FILE)
        self.calendar_client = GoogleCalendarClient(
            credentials_file=config.GOOGLE_CALENDAR_CREDENTIALS,
            token_file=config.GOOGLE_CALENDAR_TOKEN
        )
        self.calendar_manager = CalendarManager(self.calendar_client)
        self.booking_manager = BookingManager(self.agent_manager)
        
        # Sync Setup
        self._booking_in_progress = threading.Event()
        self._booking_in_progress.set()
        
        self.next_booking_opening: Optional[datetime] = None
        
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()

        # Cooldown Tracker
        self.last_booking_time: Optional[datetime] = None
        self._warmed_up_window: bool = False

        # Dashboard Hooks
        state.register_action("run_now", self.trigger_run_now)

        try:
            self.syncer = CalendarSync()
        except Exception as e:
            logger.error(f"Failed to init CalendarSync: {e}")
            self.syncer = None
            
        # Update Dashboard Agents
        agent_status_list = []
        if hasattr(self.agent_manager, 'agents'):
            self._update_dashboard_agents()

    def _update_dashboard_agents(self):
        """Helper to push current agent status to dashboard."""
        agent_status_list = []
        if hasattr(self.agent_manager, 'agents'):
            for a in self.agent_manager.agents:
                status = "Ready" if a.is_logged_in else "Failed"
                agent_status_list.append({
                    "email": a.email,
                    "status": status,
                    "last_active": datetime.now().strftime("%H:%M")
                })
        state.update_agents(agent_status_list)

    def trigger_run_now(self):
        logger.info("[SCHEDULER] Manual Run Requested")
        self._wake_event.set()

    def run(self):
        logger.info("[SCHEDULER] Starting background threads...")
        state.update_status("scheduler", "Running")
        
        # Initial Sync & Scan (Blocking)
        if self.syncer:
            logger.info("[SCHEDULER] Performing initial Sync...")
            self._perform_sync()

        logger.info("[SYSTEM] Performing initial Scan...")
        self._run_loop()

    def stop(self):
        """Signal all threads to stop and wake them up."""
        logger.info("[SYSTEM] Stopping...")
        self._stop_event.set()
        self._wake_event.set()

    def _run_loop(self):
        logger.info("[SYSTEM] Starting Main Loop...")
        state.update_status("scheduler", "Running")
        
        manual_run = False

        while not self._stop_event.is_set():
            # Refresh Agent Status Periodically (every loop)
            self._update_dashboard_agents()
            
            now_utc = datetime.now(timezone.utc)
            
            # Decide if we are in a "Booking Window"
            is_booking_window = False
            time_to_opening = None
            
            if self.next_booking_opening:
                time_to_opening = (self.next_booking_opening - now_utc).total_seconds()
                # Window is from -LOGIN_WAKE_UP to +LOGIN_WAKE_UP (giving it time to process)
                if -config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING <= time_to_opening <= config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                    is_booking_window = True
                    logger.info(f"[SYSTEM] In Booking Window ({int(time_to_opening)}s to open).")

                    # Warm up agent logins in pre-opening window (T-60s)
                    if config.FINAL_WAKE_UP_SECONDS_BEFORE_OPENING < time_to_opening <= config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                        if not self._warmed_up_window:
                            logger.info("[SYSTEM] Pre-opening window reached (T-60s). Refreshing agent logins...")
                            self.agent_manager.refresh_agent_logins()
                            self._warmed_up_window = True
            else:
                self._warmed_up_window = False

            if not is_booking_window:
                self._warmed_up_window = False

            # --- 1. DELETE ---
            # Skip deletions if we are in a critical booking window to save time/bandwidth
            if not is_booking_window:
                 self._process_deletions()

                 
            if self._stop_event.is_set(): return
            
            # --- 2. BOOK ---
            # Always run the book/scan cycle. It will identify upcoming openings and attempt bookings.
            self._scan_and_book_cycle()
            if self._stop_event.is_set(): return

            # --- 3. CHECK COOLDOWN (Skipping Sync) ---
            skip_sync = is_booking_window

            if not manual_run and not skip_sync and self.last_booking_time:
                since_booking = (datetime.now(timezone.utc) - self.last_booking_time).total_seconds()
                # Use LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING as a reasonable cooldown duration
                if since_booking < config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                     logger.info(f"[SYSTEM] Post-Booking Cooldown ({int(since_booking)}s ago). Skipping Sync.")
                     skip_sync = True

            # --- 4. SYNC ---
            if manual_run or not skip_sync:
                 if manual_run:
                     logger.info("[SYSTEM] Manual run forced sync, bypassing cooldown.")
                 self._perform_sync()
            else:
                 state.update_status("last_sync", "Paused")
            
            if self._stop_event.is_set(): return
            
            manual_run = False # Reset flag for next loop

            # --- 5. CALCULATE SLEEP TIME ---
            try:
                # Recalculate time_to_opening in case it changed during the cycle
                now_utc = datetime.now(timezone.utc)
                if self.next_booking_opening:
                    time_to_opening = (self.next_booking_opening - now_utc).total_seconds()
                else:
                    time_to_opening = None

                base_interval = config.CALENDAR_POLL_INTERVAL_SECONDS
                sleep_duration = base_interval

                if time_to_opening is not None:
                     if time_to_opening < -config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                          # It's past the window (maybe failed or too long), just normal sleep
                          sleep_duration = base_interval
                     elif time_to_opening <= config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                          # We are INSIDE the critical window. 
                          # If it's very close, sleep very little. If it's a bit further, sleep a bit.
                          # We use FINAL_WAKE_UP_SECONDS_BEFORE_OPENING for these short rapid polls.
                          sleep_duration = min(base_interval, max(5, config.FINAL_WAKE_UP_SECONDS_BEFORE_OPENING))
                     elif time_to_opening <= base_interval + config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING:
                          # It opens before the NEXT normal cycle would finish.
                          # We MUST wake up exactly at T - LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING
                          target_sleep = time_to_opening - config.LOGIN_WAKE_UP_SECONDS_BEFORE_OPENING
                          # Ensure we don't accidentally sleep negative or zero if there's a slight timing drift
                          sleep_duration = max(5, int(target_sleep))
                          logger.info(f"[SYSTEM] Adjusting sleep timer to wake up for upcoming booking in {sleep_duration}s.")
                          
                next_wake = datetime.now() + timedelta(seconds=sleep_duration)
                
                if sleep_duration < base_interval:
                     state.set_timer("scan", next_wake, "Next Cycle (Fast)")
                else:
                     state.set_timer("scan", next_wake, "Next Cycle")
                     
                state.update_status("next_action", "Sleep")
                
                # Wait for wake event or timeout
                if self._wake_event.wait(sleep_duration):
                    self._wake_event.clear()
                    logger.info("[SYSTEM] Woken up by manual trigger")
                    manual_run = True
                
                if self._stop_event.is_set():
                    return
                    
            except Exception as e:
                logger.error(f"[SYSTEM] Error in wait: {e}")

    def _process_deletions(self):
        """Step 1: Check for DELETE requests on GCal and execute them."""
        # Map to 'Scanner' entity as we are scanning for instructions
        state.op_start("Scanner", "Scanning for deletions...")
        logger.info("[SCANNER] Checking for deletion requests...")
        try:
            delete_requests = self.calendar_manager.scan_for_deletions()
            if delete_requests:
                state.op_update("Scanner", "status", f"Processing {len(delete_requests)} deletions...")
                logger.info(f"[SCANNER] Found {len(delete_requests)} delete requests.")
                
                for req in delete_requests:
                    if self._stop_event.is_set(): break
                    refs_to_cancel = getattr(req, 'ref_nums', None) or ([req.ref_num] if req.ref_num else [])
                    all_success = True
                    reasons = []
                    for ref in refs_to_cancel:
                        item_id = state.op_add_item("Scanner", f"Delete {ref}", "loading")
                        single_req = DeletionRequest(
                            event_id=req.event_id,
                            summary=req.summary,
                            ref_num=ref,
                            ref_nums=[ref],
                            original_event=req.original_event,
                            owner_email=req.owner_email
                        )
                        success, reason = self.booking_manager.cancel_booking(single_req)
                        if success:
                            logger.info(f"[SCANNER] Successfully deleted {ref}")
                            state.op_update_item("Scanner", item_id, "success", "Deleted")
                        else:
                            logger.error(f"[SCANNER] Failed to delete {ref}: {reason}")
                            state.op_update_item("Scanner", item_id, "error", f"Failed: {reason}")
                            all_success = False
                            reasons.append(f"{ref}: {reason}")
                    
                    if all_success:
                        self.calendar_manager.update_event_status(
                            req.event_id, CalendarStatus.DELETED, req.original_event
                        )
                    else:
                        self.calendar_manager.update_event_status(
                            req.event_id, CalendarStatus.FAILURE, req.original_event, reason=f"Delete Failed: {', '.join(reasons)}"
                        )
                state.op_end("Scanner", "Idle (Deletions processed)")
            else:
                state.op_end("Scanner", "Idle")
        except Exception as e:
            logger.error(f"[SCANNER] Delete Error: {e}")
            state.op_end("Scanner", "Error")

    def _perform_sync(self):
        """Step 2: Sync bookings from University Server to GCal."""
        if not self.syncer: return
        
        state.op_start("Syncer", "Syncing Calendar...")
        try:
            # Pre-populate dashboard
            user_map = {} 
            for u in self.syncer.users:
                # Add a main status item for this user
                item_id = state.op_add_item("Syncer", "Fetching...", "loading", agent=u.email)
                user_map[u.email] = item_id

            def progress_cb(email, status, msg):
                 if status == "details":
                     # Add a separate sublist item for each finding
                     state.op_add_item("Syncer", msg, "success", agent=email)
                 elif email in user_map:
                     # Update the main status item
                     state.op_update_item("Syncer", user_map[email], status, msg)

            self.syncer.sync_all_users(progress_callback=progress_cb)
            state.op_end("Syncer", "Idle (Synced)")
            logger.info("[SYNCER] === SYNC CYCLE COMPLETE ===")
            state.update_status("last_sync", datetime.now().strftime("%H:%M:%S"))
        except Exception as e:
            logger.error(f"[SYNCER] Sync failed: {e}")
            state.op_end("Syncer", "Error")

    def _scan_and_book_cycle(self):
        """Step 3 & 4: Scan GCal for 'Booking' events and process them."""
        try:
            state.update_status("last_scan", "Scanning...")
            
            # --- PHASE 1: SCAN ---
            state.op_start("Scanner", "Scanning for bookings...")
            all_requests = self.calendar_manager.scan_for_bookings(split_multi_hour=True)
            
            # Filter for actionable requests vs pending
            now = datetime.now(timezone.utc)
            actionable_requests = []
            next_booking_time = None
            
            for req in all_requests:
                opening = req.opening_time.replace(tzinfo=timezone.utc) if req.opening_time.tzinfo is None else req.opening_time
                if opening > now + timedelta(seconds=config.FINAL_WAKE_UP_SECONDS_BEFORE_OPENING):
                    if not next_booking_time or opening < next_booking_time:
                        next_booking_time = opening
                else:
                    actionable_requests.append(req)
            
            if next_booking_time:
                self.next_booking_opening = next_booking_time 
                state.set_timer("booking", next_booking_time, "Slot Open")
            else:
                self.next_booking_opening = None
                state.set_timer("booking", None, "No Pending Slots")

            state.op_end("Scanner", f"Found {len(actionable_requests)} actionable")

            # --- PHASE 2: BOOK ---
            if actionable_requests:
                 state.op_start("Booker", f"Processing {len(actionable_requests)} Bookings...")
                 actionable_requests.sort(key=lambda x: x.start_time)
                 
                 for req in actionable_requests:
                     self._process_single_booking(req)
                                       
                 state.op_end("Booker", "Idle (Finished Batch)")
            else:
                 # Ensure Booker is also Idle if nothing to do (clears previous state)
                 state.op_start("Booker", "Idle")
                 state.op_end("Booker", "Idle")
                
        except Exception as e:
            logger.error(f"[SCANNER] Scan loop error: {e}")
            state.op_end("Scanner", "Error")
            state.op_end("Booker", "Error")


    def _check_and_set_cooldown(self, req: BookingRequest = None):
        # We always want to cooldown after any booking attempt to prevent GCal sync race conditions
        self.last_booking_time = datetime.now(timezone.utc)

    def _check_and_split_event(self, req: BookingRequest, room_name: str, ref_num: str, user_email: str) -> bool:
        """
        Checks if the booked portion is smaller than the original event, and splits if necessary.
        Returns True if a split occurred, False otherwise.
        """
        orig_end_str = req.original_event['end'].get('dateTime') or req.original_event['end'].get('date')
        if not orig_end_str:
            return False
            
        dt_orig_end = parse_iso_datetime(orig_end_str)
        if dt_orig_end.tzinfo is None: dt_orig_end = dt_orig_end.replace(tzinfo=timezone.utc)
        else: dt_orig_end = dt_orig_end.astimezone(timezone.utc)
        
        req_end_utc = req.end_time.astimezone(timezone.utc) if req.end_time.tzinfo else req.end_time.replace(tzinfo=timezone.utc)
        
        if req_end_utc < dt_orig_end:
            logger.info(f"[BOOKER] Splitting event: Booked {req.start_time}-{req.end_time}")
            self.calendar_manager.split_event(
                req.original_event, 
                booked_start=req.start_time,
                booked_end=req.end_time,
                room_name=room_name,
                ref_num=ref_num,
                user=user_email
            )
            return True
        return False

    def _process_single_booking(self, req: BookingRequest):
        """
        Processes a single 1-hour booking request:
        1. Checks for preceding bookings ending at req.start_time.
        2. If previous booking exists:
           - Prioritizes same room (preferred_room_id) and same person (preferred_user_email).
           - If combined duration <= MAX_SINGLE_BOOKING_DURATION_HOURS, attempts extending via update_booking.
        3. If extend not applicable or fails:
           - Executes attempt_booking with preferred room and user.
        4. On success:
           - Merges in Google Calendar if same person and same room and duration <= MAX_SINGLE_BOOKING_DURATION_HOURS.
           - Otherwise updates event to SUCCESS.
        5. On failure:
           - Updates event to FAILURE.
        """
        self.calendar_manager.update_event_status(req.event_id, CalendarStatus.PROCESSING, req.original_event)

        slot_str = f"{req.start_time.strftime('%H:%M')}-{req.end_time.strftime('%H:%M')}"
        base_text = f"Booking: '{req.summary}' at {slot_str}"
        item_id = state.op_add_item("Booker", base_text, "loading")
        state.update_status("next_action", f"Booking {req.summary}")

        def progress_cb(agent_email):
            state.op_update_item("Booker", item_id, "loading", f"{base_text} (Trying {agent_email})")

        slot_duration = max(1.0, (req.end_time - req.start_time).total_seconds() / 3600.0)
        max_hours = config.MAX_SINGLE_BOOKING_DURATION_HOURS if isinstance(getattr(config, 'MAX_SINGLE_BOOKING_DURATION_HOURS', None), (int, float)) else 3.0

        # 1. Check for preceding booking
        past_booking = self.calendar_manager.find_successful_booking(req.start_time)
        preferred_room_id = None
        preferred_user = None
        accumulated_hours = 0.0
        prev_event = None
        prev_ref = None

        if past_booking:
            prev_event, prev_ref, prev_user, prev_room = past_booking[0], past_booking[1], past_booking[2], past_booking[3]
            preferred_room_id = next((k for k, v in config.ALL_ROOMS.items() if v == prev_room), None)
            preferred_user = prev_user
            accumulated_hours = getattr(past_booking, 'duration_hours', None)
            if accumulated_hours is None and prev_event:
                try:
                    p_start = parse_iso_datetime(prev_event['start'].get('dateTime') or prev_event['start'].get('date'))
                    p_end = parse_iso_datetime(prev_event['end'].get('dateTime') or prev_event['end'].get('date'))
                    accumulated_hours = max(1.0, (p_end - p_start).total_seconds() / 3600.0)
                except Exception:
                    accumulated_hours = 1.0

            # Try extend via update_booking if within max duration limit
            if (accumulated_hours + slot_duration) <= max_hours + 0.05:
                agent = self.agent_manager.get_agent(prev_user)
                if agent and preferred_room_id and prev_event:
                    orig_start_str = prev_event['start'].get('dateTime') or prev_event['start'].get('date')
                    if orig_start_str:
                        dt_orig_start = parse_iso_datetime(orig_start_str)
                        if dt_orig_start.tzinfo is None:
                            dt_orig_start = dt_orig_start.replace(tzinfo=timezone.utc)
                        else:
                            dt_orig_start = dt_orig_start.astimezone(timezone.utc)
                        start_utc = dt_orig_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

                        state.op_update_item("Booker", item_id, "loading", f"Extending {prev_ref}...", agent=prev_user)
                        
                        extend_timeout = config.BOOKING_TIMEOUT_SECONDS if isinstance(getattr(config, 'BOOKING_TIMEOUT_SECONDS', None), (int, float)) else 120
                        extend_retry_interval = config.BOOKING_RETRY_INTERVAL_SECONDS if isinstance(getattr(config, 'BOOKING_RETRY_INTERVAL_SECONDS', None), (int, float)) else 0.5
                        extend_start_time = time.time()
                        extend_success = False
                        extend_last_reason = None
                        while not self._stop_event.is_set():
                            if time.time() - extend_start_time > extend_timeout:
                                extend_last_reason = "Extend Timeout"
                                break
                            update_res = agent.update_booking(prev_ref, preferred_room_id, start_utc, req.utc_end)
                            if isinstance(update_res, tuple) and len(update_res) == 2 and update_res[0] is True:
                                self._check_and_set_cooldown(req)
                                state.op_update_item("Booker", item_id, "success", f"Extended ({req.end_time.strftime('%H:%M')})", agent=prev_user)
                                # Merge in calendar (deletes current event)
                                merged = self.calendar_manager.merge_adjacent_events(
                                    prev_event, req.original_event, prev_ref, prev_room, prev_user
                                )
                                if not merged:
                                    logger.warning(f"[BOOKER] Merge after extend failed for {req.event_id}; keeping as single SUCCESS event.")
                                    self.calendar_manager.update_event_status(
                                        req.event_id, CalendarStatus.SUCCESS, req.original_event,
                                        room_name=prev_room, ref_num=prev_ref, user=prev_user
                                    )
                                extend_success = True
                                return
                            
                            extend_last_reason = update_res[1] if isinstance(update_res, tuple) and len(update_res) > 1 else str(update_res)
                            if "too early" in str(extend_last_reason).lower():
                                logger.info(f"[BOOKER] Extend returned Too Early for {prev_ref}, retrying...")
                                time.sleep(extend_retry_interval)
                                continue
                            else:
                                break
                        
                        if not extend_success:
                            state.op_update_item("Booker", item_id, "warning", f"Extend failed. Trying new booking in {prev_room}...", details=f"Extend failed: {extend_last_reason}")

        # 2. Attempt New Booking (with preferred room and user)
        self._booking_in_progress.clear()
        try:
            res, details = self.booking_manager.attempt_booking(
                req,
                stop_event=self._stop_event,
                preferred_room_id=preferred_room_id,
                preferred_user_email=preferred_user,
                progress_callback=progress_cb
            )
            self._check_and_set_cooldown(req)
        finally:
            self._booking_in_progress.set()

        if res == BookingResult.SUCCESS:
            agent_email, room_name, ref_num = details
            state.op_update_item("Booker", item_id, "success", f"{room_name} ({ref_num})", agent=agent_email)

            # Check if eligible to merge
            can_merge = False
            if past_booking and prev_event:
                prev_room = past_booking[3]
                prev_user = past_booking[2]
                slot_duration = max(1.0, (req.end_time - req.start_time).total_seconds() / 3600.0)
                if (room_name == prev_room and
                    agent_email == prev_user and
                    (accumulated_hours + slot_duration) <= max_hours + 0.05):
                    can_merge = True

            if can_merge:
                logger.info(f"[BOOKER] Merging booking for {req.summary} into previous booking {prev_ref}")
                merged = self.calendar_manager.merge_adjacent_events(
                    prev_event, req.original_event, ref_num, room_name, agent_email
                )
                if not merged:
                    logger.warning(f"[BOOKER] Merge failed for {req.event_id}; keeping as distinct SUCCESS event.")
                    self.calendar_manager.update_event_status(
                        req.event_id, CalendarStatus.SUCCESS, req.original_event,
                        room_name=room_name, ref_num=ref_num, user=agent_email
                    )
            else:
                self.calendar_manager.update_event_status(
                    req.event_id, CalendarStatus.SUCCESS, req.original_event,
                    room_name=room_name, ref_num=ref_num, user=agent_email
                )
        else:
            error_msg = details if details else "Unknown Failure"
            state.op_update_item("Booker", item_id, "error", "Failed", details=str(error_msg))
            self.calendar_manager.update_event_status(
                req.event_id, CalendarStatus.FAILURE, req.original_event, reason=str(error_msg)
            )

    def _process_booking_batch(self, batch):
        """Processes a batch of requests by delegating each to _process_single_booking."""
        for req in batch:
            self._process_single_booking(req)

    def _handle_consecutive_booking(self, req: BookingRequest, past_booking: tuple) -> bool:
        """Legacy helper delegating to _process_single_booking."""
        self._process_single_booking(req)
        return True
