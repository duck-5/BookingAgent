import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from core.enums import BookingResult, CalendarStatus
from core.entities import BookingRequest
from services.agent_manager import AgentManager
from services.calendar_manager import CalendarManager
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

            # --- 1. DELETE ---
            # Skip deletions if we are in a critical booking window to save time/bandwidth
            if not is_booking_window and not manual_run:
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
        try:
            delete_requests = self.calendar_manager.scan_for_deletions()
            if delete_requests:
                state.op_update("Scanner", "status", f"Processing {len(delete_requests)} deletions...")
                logger.info(f"[SCANNER] Found {len(delete_requests)} delete requests.")
                
                for req in delete_requests:
                    if self._stop_event.is_set(): break
                    item_id = state.op_add_item("Scanner", f"Delete {req.ref_num}", "loading")
                    success, reason = self.booking_manager.cancel_booking(req)
                    
                    if success:
                         logger.info(f"[SCANNER] Successfully deleted {req.ref_num}")
                         state.op_update_item("Scanner", item_id, "success", "Deleted")
                         self.calendar_manager.update_event_status(
                             req.event_id, CalendarStatus.DELETED, req.original_event
                         )
                    else:
                         logger.error(f"[SCANNER] Failed to delete {req.ref_num}: {reason}")
                         state.op_update_item("Scanner", item_id, "error", f"Failed: {reason}")
                         self.calendar_manager.update_event_status(
                             req.event_id, CalendarStatus.FAILURE, req.original_event, reason=f"Delete Failed: {reason}"
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
            all_requests = self.calendar_manager.scan_for_bookings()
            
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
                 processed_ids = set()
                 
                 for i, req in enumerate(actionable_requests):
                     if req.event_id in processed_ids: continue
                     
                     next_req = None
                     if i + 1 < len(actionable_requests):
                         candidate = actionable_requests[i+1]
                         if abs((candidate.start_time - req.end_time).total_seconds()) < 60:
                             next_req = candidate
                     
                     current_batch = [req]
                     if next_req: 
                         current_batch.append(next_req)
                         processed_ids.add(next_req.event_id)
                     
                     processed_ids.add(req.event_id)
                     
                     self._process_booking_batch(current_batch)
                                      
                 state.op_end("Booker", "Idle (Finished Batch)")
            else:
                 # Ensure Booker is also Idle if nothing to do (clears previous state)
                 state.op_start("Booker", "Idle")
                 state.op_end("Booker", "Idle")
                
        except Exception as e:
            logger.error(f"[SCANNER] Scan loop error: {e}")
            state.op_end("Scanner", "Error")
            state.op_end("Booker", "Error")

    def _process_booking_batch(self, batch):
        """
        ... logic handles actual booking ...
        uses state.op_add_item("Booker", ...)
        """
        # I need to update the internal calls in this method too!
        # Since I can't overwrite the whole file easily, and this method is large.
        # I will rely on `multi_replace` or just rewrite the `_process_booking_batch` call site logic here 
        # but I can't see `_process_booking_batch` body in this replacement chunk.
        # I must ensure `_process_booking_batch` uses "Booker". 
        pass 

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
            
        dt_orig_end = datetime.fromisoformat(orig_end_str)
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

    def _handle_consecutive_booking(self, req: BookingRequest, past_booking: tuple) -> bool:
        """
        Handles consecutive booking logic.
        1. Attempt Update
        2. Attempt New Booking (Same Room) -> gracefully falls back exactly as requested
        Returns True if handled, False if we should fallback purely to normal unstructured flow.
        """
        evt, ref, user, room = past_booking
        
        room_id = next((k for k, v in config.ALL_ROOMS.items() if v == room), None)
        if room_id is None:
            return False
            
        orig_start_str = evt['start'].get('dateTime') or evt['start'].get('date')
        if not orig_start_str: return False
        
        dt_orig_start = datetime.fromisoformat(orig_start_str)
        if dt_orig_start.tzinfo is None: dt_orig_start = dt_orig_start.replace(tzinfo=timezone.utc)
        else: dt_orig_start = dt_orig_start.astimezone(timezone.utc)
        start_utc = dt_orig_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        total_duration = (req.end_time - dt_orig_start).total_seconds() / 3600
        if total_duration > config.MAX_SINGLE_BOOKING_DURATION_HOURS + 0.05:
             logger.info(f"[BOOKER] Extending {ref} to {req.end_time.strftime('%H:%M')} exceeds {config.MAX_SINGLE_BOOKING_DURATION_HOURS}h limit. Force skipping to new booking fallback.")
             agent = None # Bypass extend logic
        else:
             agent = self.agent_manager.get_agent(user)
             
        if agent:
             item_id = state.op_add_item("Booker", f"Extending {ref}...", "loading", agent=user)
             success, reason = agent.update_booking(ref, room_id, start_utc, req.utc_end)
             if success:
                 self._check_and_set_cooldown(req)
                 state.op_update_item("Booker", item_id, "success", f"Extended ({req.end_time.strftime('%H:%M')})")
                 
                 # Check if split needed
                 if self._check_and_split_event(req, room, ref, user):
                     return True

                 self.calendar_manager.update_event_status(
                     req.event_id, CalendarStatus.SUCCESS, req.original_event, 
                     room_name=room, ref_num=ref, user=user
                 )
                 return True
             else:
                 state.op_update_item("Booker", item_id, "warning", f"Extend failed. Trying new booking in {room}...", details=f"Extend failed: {reason}")
        
        # 2. Attempt New Booking (Same Room)
        # This implicitly covers "if that fails just book something" because attempt_booking handles iterating other rooms if the preferred one is taken
        item_id = state.op_add_item("Booker", f"Booking {req.summary} in {room}...", "loading")
        self._booking_in_progress.clear()
        try:
             res, details = self.booking_manager.attempt_booking(req, stop_event=self._stop_event, preferred_room_id=room_id)
             self._check_and_set_cooldown(req)
        finally:
             self._booking_in_progress.set()

        if res == BookingResult.SUCCESS:
             agent_email, room_name, ref_num = details
             state.op_update_item("Booker", item_id, "success", f"{room_name} ({ref_num})", agent=agent_email)
             
             if self._check_and_split_event(req, room_name, ref_num, agent_email):
                  return True

             self.calendar_manager.update_event_status(
                  req.event_id, CalendarStatus.SUCCESS, req.original_event, 
                  room_name=room_name, ref_num=ref_num, user=agent_email
             )
             return True
             
        else:
             error_msg = details if details else "Unknown Failure"
             state.op_update_item("Booker", item_id, "error", f"Failed", details=str(error_msg))
             self.calendar_manager.update_event_status(req.event_id, CalendarStatus.FAILURE, req.original_event, reason=str(error_msg))
             return True # Handled (even as a failure)


    def _process_booking_batch(self, batch):
        """
        Process a batch of 1 or 2 requests. 
        If 2, try to book combined. If failed, book first then extend.
        """
        if not batch: return
        
        # --- SPLITTING LOGIC ---
        # Check against 7-day rule (approx). 
        # Actually, the 7-day rule is enforced by the scanner (it calculates `opening_time`).
        # If `req.end_time` > `opening_time + 1h`, it's a multi-hour request that is only partially open.
        # Wait, the `opening_time` is when the *entry* becomes bookable. 
        # Usually, a 2h slot 10-12 becomes bookable at T (for 10-11) and T+1 (for 11-12).
        # So if we are at T, 10-11 is open, 11-12 is not.
        # The scanner logic uses `opening_time = dt_start - 7 days + 1 hour`. 
        # So if `now >= opening_time`, the start is valid.
        
        # We need to process each request in the batch to see if it needs splitting.
        # But wait, batching logic grouped adjacent requests. 
        # A single request object (from Google Calendar) might be 2 hours long.
        # If so, it needs splitting.
        
        active_batch = []
        
        now = datetime.now(timezone.utc)
        
        for req in batch:
             # Check if multi-hour single event
             duration = (req.end_time - req.start_time).total_seconds() / 3600
             if duration > 1.05: # Allow small margin
                 # Calculate how many hours are currently open
                 # opening_time is when the FIRST hour opens
                 req_opening_utc = req.opening_time.replace(tzinfo=timezone.utc) if req.opening_time.tzinfo is None else req.opening_time.astimezone(timezone.utc)
                 delta_hours = (now - req_opening_utc).total_seconds() / 3600
                 hours_open = max(1, int(delta_hours) + 1)
                 
                 # We can book at most 3 hours atomically, and no more than what's open
                 target_duration = min(duration, hours_open, float(config.MAX_SINGLE_BOOKING_DURATION_HOURS))
                 
                 if target_duration < duration - 0.05:
                     logger.info(f"[BOOKER] Slicing multi-hour request {req.summary} from {duration}h to {target_duration}h")
                     # Split!
                     sub_end = req.start_time + timedelta(hours=target_duration)
                     sub_utc_end = sub_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                     
                     sub_req = BookingRequest(
                         event_id=req.event_id,
                         summary=req.summary,
                         start_time=req.start_time,
                         end_time=sub_end,
                         utc_start=req.utc_start,
                         utc_end=sub_utc_end,
                         original_event=req.original_event,
                         opening_time=req.opening_time
                     )
                     
                     # We only process the sub_req. The remainder is handled by the calendar update (split).
                     active_batch.append(sub_req)
                     continue
            
             active_batch.append(req)

        if not active_batch: return
        batch = active_batch # Update batch with split items
        
        # UI Update for batch
        for req in batch:
             self.calendar_manager.update_event_status(req.event_id, CalendarStatus.PROCESSING, req.original_event)

        primary_req = batch[0]
        
        if len(batch) == 2:
            req1, req2 = batch[0], batch[1]
            
            total_dur = (req2.end_time - req1.start_time).total_seconds() / 3600
            if total_dur > config.MAX_SINGLE_BOOKING_DURATION_HOURS + 0.05:
                logger.info(f"[BOOKER] Skipping combined booking for {req1.summary}, total duration {total_dur}h exceeds {config.MAX_SINGLE_BOOKING_DURATION_HOURS}h limit.")
                # Fallthrough to process req1 normally as a single request
                batch = [req1]
                primary_req = batch[0]
            else:
                logger.info(f"[BOOKER] Attempting CONSECUTIVE booking for {req1.summary} + {req2.summary}")
                
                # 1. Try Combined
                # Create a synthetic request
            combined_req = BookingRequest(
                event_id=f"{req1.event_id}_{req2.event_id}",
                summary=f"Combined {req1.summary}",
                start_time=req1.start_time,
                end_time=req2.end_time,
                utc_start=req1.utc_start,
                utc_end=req2.utc_end,
                original_event=req1.original_event, # Use first as base
                opening_time=req1.opening_time
            )
            
            slot_str = f"{req1.start_time.strftime('%H:%M')}-{req2.end_time.strftime('%H:%M')}"
            base_text = f"Booking: '{req1.summary}' at {slot_str}"
            item_id = state.op_add_item("Booker", base_text, "loading")
            
            def progress_cb(agent_email):
                state.op_update_item("Booker", item_id, "loading", f"{base_text} (Trying {agent_email})")
            
            self._booking_in_progress.clear()
            try:
                # We need to pass stop_event
                res, details = self.booking_manager.attempt_booking(combined_req, stop_event=self._stop_event, progress_callback=progress_cb)
                self._check_and_set_cooldown(combined_req)
            finally:
                 self._booking_in_progress.set()

            if res == BookingResult.SUCCESS:
                agent_email, room_name, ref_num = details
                state.op_update_item("Booker", item_id, "success", f"Secured! ({room_name})", agent=agent_email)
                
                # Update BOTH events
                self.calendar_manager.update_event_status(req1.event_id, CalendarStatus.SUCCESS, req1.original_event, room_name=room_name, ref_num=ref_num, user=agent_email)
                self.calendar_manager.update_event_status(req2.event_id, CalendarStatus.SUCCESS, req2.original_event, room_name=room_name, ref_num=ref_num, user=agent_email)
                return

            else:
                state.op_update_item("Booker", item_id, "warning", f"Combined Failed. Trying single...")
                # Fallback to Single + Extend
                batch = [req1]
                primary_req = batch[0]
        
        # 2. Process Primary (Normal or First of pair)
        # Look-Back Logic: check if we can EXTEND a previous booking instead of creating new
        # Only if we are dealing with a single request (not a combined pair attempt)
        if len(batch) == 1:
             past_booking = self.calendar_manager.find_successful_booking(primary_req.start_time)
             if past_booking:
                 logger.info(f"[BOOKER] Found consecutive past booking. Delegating to consecutive handler.")
                 if self._handle_consecutive_booking(primary_req, past_booking):
                     return # Handled
        
        
        slot_str = f"{primary_req.start_time.strftime('%H:%M')}-{primary_req.end_time.strftime('%H:%M')}"
        base_text = f"Booking: '{primary_req.summary}' at {slot_str}"
        item_id = state.op_add_item("Booker", base_text, "loading")
        state.update_status("next_action", f"Booking {primary_req.summary}")
        
        def progress_cb(agent_email):
            state.op_update_item("Booker", item_id, "loading", f"{base_text} (Trying {agent_email})")
        
        self._booking_in_progress.clear()
        try:
             res, details = self.booking_manager.attempt_booking(primary_req, stop_event=self._stop_event, progress_callback=progress_cb)
             # Track activity regardless of result to be safe? 
             # No, only if we suspect server state changed. 
             # But let's set it on any attempt to be safe against race conditions.
             self._check_and_set_cooldown(primary_req)
        finally:
             self._booking_in_progress.set()
             
        # --- Graceful Degradation Strategy ---
        duration_h = (primary_req.end_time - primary_req.start_time).total_seconds() / 3600
        current_req = primary_req
        
        while res != BookingResult.SUCCESS and duration_h >= 1.95:
             # Slice 1 hour off the end and retry
             duration_h -= 1
             logger.info(f"[BOOKER] Booking failed. Degrading to {int(duration_h)}h...")
             state.op_update_item("Booker", item_id, "warning", f"Failed. Retrying for {int(duration_h)}h...")
             
             new_end = current_req.start_time + timedelta(hours=duration_h)
             new_utc_end = new_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
             
             degraded_req = BookingRequest(
                 event_id=current_req.event_id, summary=current_req.summary, start_time=current_req.start_time, end_time=new_end,
                 utc_start=current_req.utc_start, utc_end=new_utc_end, original_event=current_req.original_event, opening_time=current_req.opening_time
             )
             res, details = self.booking_manager.attempt_booking(degraded_req, stop_event=self._stop_event, progress_callback=progress_cb)
             self._check_and_set_cooldown(degraded_req)
             current_req = degraded_req
             
        primary_req = current_req # Update primary_req for the split detection logic below
             
        if res != BookingResult.SUCCESS:
             # Use the detailed error message from booking_manager
             error_msg = details if details else "Unknown Failure"
             # Since Booker cycles through agents, we don't have a specific agent if all failed
             state.op_update_item("Booker", item_id, "error", f"Failed", details=str(error_msg))
             self.calendar_manager.update_event_status(primary_req.event_id, CalendarStatus.FAILURE, primary_req.original_event, reason=str(error_msg))
             # If valid pair, fail second too
             if len(batch) > 1:
                 # Mark second as failed too to avoid orphan attempt
                 self.calendar_manager.update_event_status(batch[1].event_id, CalendarStatus.FAILURE, batch[1].original_event, reason="Previous slot failed")
             return

        # Success on Primary
        agent_email, room_name, ref_num = details
        # Update item with agent so UI groups it properly
        state.op_update_item("Booker", item_id, "success", f"{room_name} ({ref_num})", agent=agent_email)
        
        # Split the event if the booked portion is shorter than the original
        
        if self._check_and_split_event(primary_req, room_name, ref_num, agent_email):
             return # Done, split handled the updates

        # Normal Update
        self.calendar_manager.update_event_status(
             primary_req.event_id, CalendarStatus.SUCCESS, primary_req.original_event, 
             room_name=room_name, ref_num=ref_num, user=agent_email
        )
        
        # 3. Extension (If Pair)
        if len(batch) > 1:
            req2 = batch[1]
            item_id_ext = state.op_add_item("Booker", f"Extending to {req2.summary}...", "loading")
            
            # Get Agent
            agent = self.agent_manager.get_agent(agent_email)
            if agent:
                # Force update. `room_name` was used in `primary_req`.
                room_id = next((k for k, v in config.ALL_ROOMS.items() if v == room_name), None)
                success, reason = agent.update_booking(ref_num, room_id, primary_req.utc_start, req2.utc_end)
                if success:
                    self._check_and_set_cooldown(req2)
                    state.op_update_item("Booker", item_id_ext, "success", "Extended!", agent=agent_email)
                    self.calendar_manager.update_event_status(req2.event_id, CalendarStatus.SUCCESS, req2.original_event, room_name=room_name, ref_num=ref_num, user=agent_email)
                else:
                    state.op_update_item("Booker", item_id_ext, "error", f"Extend Failed", details=reason, agent=agent_email)
                    self.calendar_manager.update_event_status(req2.event_id, CalendarStatus.FAILURE, req2.original_event, reason=f"Extend Failed: {reason}")
            else:
                 state.op_update_item("Booker", item_id_ext, "error", "Agent not found for extend", agent=agent_email)
