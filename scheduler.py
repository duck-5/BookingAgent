import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from core.enums import BookingResult, CalendarStatus
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
        
        self._sync_wake_event = threading.Event()
        self._scan_wake_event = threading.Event()
        self._stop_event = threading.Event()

        # Dashboard Hooks
        state.register_action("sync", self.trigger_manual_sync)
        state.register_action("scan", self.trigger_manual_scan)

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
                # If we have a 'last_active' timestamp in agent, use it. 
                # For now just 'Now' or maybe track login time?
                agent_status_list.append({
                    "email": a.email,
                    "status": status,
                    "last_active": datetime.now().strftime("%H:%M")
                })
        state.update_agents(agent_status_list)

    def trigger_manual_sync(self):
        logger.info("[SCHEDULER] Manual Sync Requested")
        self._sync_wake_event.set()

    def trigger_manual_scan(self):
        logger.info("[SCHEDULER] Manual Scan Requested")
        self._scan_wake_event.set()

    def run(self):
        logger.info("[SCHEDULER] Starting background threads...")
        state.update_status("scheduler", "Running")
        
        sync_thread = threading.Thread(target=self._sync_worker, daemon=True)
        sync_thread.start()
        
        self._scan_loop()

    def stop(self):
        """Signal all threads to stop and wake them up."""
        logger.info("[SCHEDULER] Stopping...")
        self._stop_event.set()
        self._scan_wake_event.set()
        self._sync_wake_event.set()
        self._booking_in_progress.set()

    def _sync_worker(self):
        while not self._stop_event.is_set():
            # Set timer for next sync
            next_sync = datetime.now() + timedelta(seconds=config.SYNC_INTERVAL_SECONDS)
            state.set_timer("sync", next_sync, "Next Sync")
            
            triggered = self._sync_wake_event.wait(config.SYNC_INTERVAL_SECONDS)
            if self._stop_event.is_set():
                return
            if triggered: self._sync_wake_event.clear()
            
            # Wait if booking is in progress, but check stop event
            while not self._booking_in_progress.wait(1):
                if self._stop_event.is_set():
                    return
            
            state.update_status("last_sync", "In Progress...")
            logger.info("[SCHEDULER] === STARTING SYNC CYCLE ===")
            
            # --- PROCESS DELETES ---
            state.op_start("DELETE", "Scanning for deletions...")
            try:
                delete_requests = self.calendar_manager.scan_for_deletions()
                if delete_requests:
                    state.op_update("DELETE", "status", f"Processing {len(delete_requests)} requests...")
                    logger.info(f"[SCHEDULER] Found {len(delete_requests)} delete requests.")
                    
                    for req in delete_requests:
                        if self._stop_event.is_set(): break
                        item_id = state.op_add_item("DELETE", f"Delete {req.ref_num}", "loading")
                        success, reason = self.booking_manager.cancel_booking(req)
                        
                        if success:
                             logger.info(f"[SCHEDULER] Successfully deleted {req.ref_num}")
                             state.op_update_item("DELETE", item_id, "success", "Deleted")
                             new_summary = req.summary.replace(config.DELETE_KEYWORD, '').strip()
                             self.calendar_manager.update_event_status(
                                 req.event_id, CalendarStatus.DELETED, req.original_event
                             )
                        else:
                             logger.error(f"[SCHEDULER] Failed to delete {req.ref_num}: {reason}")
                             state.op_update_item("DELETE", item_id, "error", f"Failed: {reason}")
                             self.calendar_manager.update_event_status(
                                 req.event_id, CalendarStatus.FAILURE, req.original_event, reason=f"Delete Failed: {reason}"
                             )
                else:
                    state.op_end("DELETE", "Idle (No requests)")
            except Exception as e:
                logger.error(f"[SCHEDULER] Delete Error: {e}")
                state.op_end("DELETE", "Error")

            if self._stop_event.is_set(): return

            # --- PROCESS SYNC ---
            if self.syncer:
                state.op_start("SYNC", "Syncing Calendar...")
                try:
                    # Pre-populate dashboard with all users
                    user_map = {} # email -> item_id
                    for u in self.syncer.users:
                        item_id = state.op_add_item("SYNC", u.email, "pending")
                        user_map[u.email] = item_id

                    def progress_cb(email, status, msg):
                         if email in user_map:
                             state.op_update_item("SYNC", user_map[email], status, msg)

                    self.syncer.sync_all_users(progress_callback=progress_cb)
                    state.op_end("SYNC", "Idle (Synced)")
                    logger.info("[SCHEDULER] === SYNC CYCLE COMPLETE ===")
                except Exception as e:
                    logger.error(f"Sync failed: {e}")
                    state.op_end("SYNC", "Error")
            
            state.update_status("last_sync", datetime.now().strftime("%H:%M:%S"))

    def _scan_loop(self):
        logger.info("[SCHEDULER] Starting Main Scan Loop...")
        state.update_status("scheduler", "Running")

        while not self._stop_event.is_set():
            # Refresh Agent Status Periodically (every loop)
            self._update_dashboard_agents()
            
            try:
                # Wait for interval
                next_wake = datetime.now() + timedelta(seconds=config.CALENDAR_POLL_INTERVAL_SECONDS)
                state.set_timer("scan", next_wake, "Next Scan")
                state.update_status("next_action", "Sleep")
                
                self._scan_wake_event.wait(config.CALENDAR_POLL_INTERVAL_SECONDS)
                if self._stop_event.is_set():
                    logger.info("[SCHEDULER] Stop signal received. Exiting scan loop.")
                    return
                self._scan_wake_event.clear()

                state.update_status("last_scan", "Scanning...")
                state.set_timer("scan", None, "Scanning...") # Clear timer while scanning
                
                # Scan for bookings
                all_requests = self.calendar_manager.scan_for_bookings()
                
                # Filter for actionable requests vs pending
                now = datetime.now(timezone.utc)
                actionable_requests = []
                next_booking_time = None
                
                for req in all_requests:
                    # Parse opening time (it's already a datetime object from scan_for_bookings)
                    # We need to compare strict UTC
                    opening = req.opening_time.replace(tzinfo=timezone.utc) if req.opening_time.tzinfo is None else req.opening_time
                    
                    # If opening time is in the future (plus a small buffer, e.g. 5 seconds), it's pending
                    if opening > now + timedelta(seconds=5):
                        if not next_booking_time or opening < next_booking_time:
                            next_booking_time = opening
                    else:
                        actionable_requests.append(req)
                
                # Update Next Booking Timer
                if next_booking_time:
                    state.set_timer("booking", next_booking_time, "Slot Open")
                else:
                    state.set_timer("booking", None, "No Pending Slots")

                if actionable_requests:
                     # Process Requests
                     state.op_start("CREATE", f"Processing {len(actionable_requests)} Bookings...")
                     
                     # Add pending items to UI first
                     for req in actionable_requests:
                         state.op_add_item("CREATE", f"Booking {req.summary}", "pending")
                     
                     for req in actionable_requests:
                         logger.info(f"[SCHEDULER] Processing request: {req.summary}")
                         
                         # Find the item we just added (or add new if somehow missed)
                         # Simple way: just add a new "loading" item or update existing?
                         # Let's add a new specific interaction item for clarity or update the pending one.
                         # Since we didn't store IDs above, let's just add new for now or improve state management later.
                         # For now: Add matching "Processing" item.
                         
                         item_id = state.op_add_item("CREATE", f"Processing {req.summary}...", "loading")
                         state.update_status("next_action", f"Booking {req.summary}")
                         
                         # Mark as Processing
                         self.calendar_manager.update_event_status(
                             req.event_id, CalendarStatus.PROCESSING, req.original_event
                         )
                         
                         # Attempt Booking
                         self._booking_in_progress.clear()
                         try:
                             result, details = self.booking_manager.attempt_booking(req, stop_event=self._stop_event)
                         finally:
                             self._booking_in_progress.set()
                         
                         # Handle Result
                         if result == BookingResult.SUCCESS:
                             agent_email, room_name, ref_num = details
                             logger.info(f"[SCHEDULER] Booking Successful! {room_name} by {agent_email}")
                             state.op_update_item("CREATE", item_id, "success", f"{room_name} ({agent_email})")
                             
                             self.calendar_manager.update_event_status(
                                 req.event_id, CalendarStatus.SUCCESS, req.original_event,
                                 room_name=room_name, ref_num=ref_num, user=agent_email
                             )
                             
                         elif result in [BookingResult.ROOM_TAKEN, BookingResult.USER_LIMIT, BookingResult.CLOSED, BookingResult.TOO_EARLY]:
                              logger.warning(f"[SCHEDULER] Booking Failed: {result}")
                              state.op_update_item("CREATE", item_id, "error", f"❌ Failed: {details if details else result}")
                              self.calendar_manager.update_event_status(
                                  req.event_id, CalendarStatus.FAILURE, req.original_event, reason=str(details if details else result)
                              )
                              
                              # If too early, maybe we should have caught it? 
                              # But if API says too early, double check opening time.
                         
                         elif result == BookingResult.ERROR:
                              state.op_update_item("CREATE", item_id, "error", f"❌ System Error: {details}")
                              logger.error(f"[SCHEDULER] System Error: {details}")
                     
                     state.op_end("CREATE", "Idle (Finished Batch)")
                
                else:
                    if not next_booking_time:
                         state.op_start("CREATE", "Scanning...")
                         state.op_add_item("CREATE", "No bookings found to process", "success")
                         time.sleep(2) 
                         state.op_end("CREATE", "Idle")
                    
            except Exception as e:
                logger.error(f"[SCHEDULER] Scan loop error: {e}")
                state.op_end("CREATE", "Error")
                time.sleep(5)
