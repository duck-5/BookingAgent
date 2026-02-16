import time
import logging
import threading
from datetime import datetime, timedelta
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
            for a in self.agent_manager.agents:
                agent_status_list.append({
                    "email": a.email,
                    "status": "Ready" if a.is_logged_in else "Not Logged In",
                    "last_active": "Now"
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

    def _sync_worker(self):
        while not self._stop_event.is_set():
            triggered = self._sync_wake_event.wait(config.SYNC_INTERVAL_SECONDS)
            if triggered: self._sync_wake_event.clear()
            
            self._booking_in_progress.wait()
            
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
                        item_id = state.op_add_item("DELETE", f"Delete {req.ref_num}", "loading")
                        success = self.booking_manager.cancel_booking(req)
                        
                        if success:
                             logger.info(f"[SCHEDULER] Successfully deleted {req.ref_num}")
                             state.op_update_item("DELETE", item_id, "success", "Deleted")
                             new_summary = req.summary.replace(config.DELETE_KEYWORD, '').strip()
                             self.calendar_manager.update_event_status(
                                 req.event_id, CalendarStatus.DELETED, req.original_event
                             )
                        else:
                             logger.error(f"[SCHEDULER] Failed to delete {req.ref_num}")
                             state.op_update_item("DELETE", item_id, "error", "Failed")
                             self.calendar_manager.update_event_status(
                                 req.event_id, CalendarStatus.FAILURE, req.original_event, reason="Delete Failed"
                             )
                else:
                    state.op_end("DELETE", "Idle (No requests)")
            except Exception as e:
                logger.error(f"[SCHEDULER] Delete Error: {e}")
                state.op_end("DELETE", "Error")

            # --- PROCESS SYNC ---
            if self.syncer:
                state.op_start("SYNC", "Syncing Calendar...")
                try:
                    state.op_add_item("SYNC", "Syncing all users...", "loading")
                    self.syncer.sync_all_users()
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
            try:
                # Wait for interval
                next_wake = datetime.now() + timedelta(seconds=config.CALENDAR_POLL_INTERVAL_SECONDS)
                state.set_timer(next_wake, "Next Scan")
                state.update_status("next_action", "Sleep")
                
                self._scan_wake_event.wait(config.CALENDAR_POLL_INTERVAL_SECONDS)
                self._scan_wake_event.clear()

                state.update_status("last_scan", "Scanning...")
                state.set_timer(None, "Scanning...") # Clear timer while scanning
                
                # Scan for bookings
                requests = self.calendar_manager.scan_for_bookings()
                
                if not requests:
                    state.update_status("last_scan", datetime.now().strftime("%H:%M:%S"))
                    continue

                # Process Requests
                state.op_start("CREATE", f"Processing {len(requests)} Bookings...")
                
                for req in requests:
                    logger.info(f"[SCHEDULER] Processing request: {req.summary}")
                    item_id = state.op_add_item("CREATE", f"Booking {req.summary}", "loading")
                    state.update_status("next_action", f"Booking {req.summary}")
                    
                    # Mark as Processing
                    self.calendar_manager.update_event_status(
                        req.event_id, CalendarStatus.PROCESSING, req.original_event
                    )
                    
                    # Attempt Booking
                    self._booking_in_progress.clear()
                    try:
                        result, details = self.booking_manager.attempt_booking(req)
                    finally:
                        self._booking_in_progress.set()
                    
                    # Handle Result
                    if result == BookingResult.SUCCESS:
                        agent_email, room_name, ref_num = details
                        logger.info(f"[SCHEDULER] Booking Successful! {room_name} by {agent_email}")
                        state.op_update_item("CREATE", item_id, "success", f"{room_name} ({agent_email})")
                        
                        # Handle Split/Merge (Placeholder logic from original file)
                        # ... (Simplified for this rewrite as original split logic was complex/todo)
                        
                        self.calendar_manager.update_event_status(
                            req.event_id, CalendarStatus.SUCCESS, req.original_event,
                            room_name=room_name, ref_num=ref_num, user=agent_email
                        )
                        
                    elif result in [BookingResult.ROOM_TAKEN, BookingResult.USER_LIMIT, BookingResult.CLOSED]:
                         logger.warning(f"[SCHEDULER] Booking Failed: {result}")
                         state.op_update_item("CREATE", item_id, "error", f"Failed: {result}")
                         self.calendar_manager.update_event_status(
                             req.event_id, CalendarStatus.FAILURE, req.original_event, reason=str(result)
                         )
                    
                    elif result == BookingResult.ERROR:
                         state.op_update_item("CREATE", item_id, "error", "System Error")
                         logger.error(f"[SCHEDULER] System Error during booking")
                
                state.op_end("CREATE", "Idle (Finished Batch)")
                    
            except Exception as e:
                logger.error(f"[SCHEDULER] Scan loop error: {e}")
                state.op_end("CREATE", "Error")
                time.sleep(5)