import json
import logging
import os
import time
import threading
import concurrent.futures
from datetime import datetime, timedelta, timezone
import config
from booking_agent import BookingAgent, BookingResult
from utils.google_calendar import GoogleCalendarClient, CalendarSync
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self.users_data: List[Dict[str, str]] = self._load_json(config.CREDENTIALS_FILE, [])
        self.agents: List[BookingAgent] = [] 
        self.last_successful_user: Optional[str] = None
        self.calendar_client = GoogleCalendarClient(
            credentials_file=config.GOOGLE_CALENDAR_CREDENTIALS,
            token_file=config.GOOGLE_CALENDAR_TOKEN
        )
        self.calendar_id = self.calendar_client.get_or_create_calendar(config.CALENDAR_NAME) 
        
        # Sync setup
        self._booking_in_progress = threading.Event()
        self._booking_in_progress.set()  # Set = NOT booking (sync can run)
        
        try:
            self.syncer = CalendarSync()
        except Exception as e:
            logger.error(f"Failed to init CalendarSync: {e}")
            self.syncer = None 

    def _sync_worker(self):
        """Background sync thread. Runs every SYNC_INTERVAL_SECONDS, pauses during booking attempts."""
        while True:
            time.sleep(config.SYNC_INTERVAL_SECONDS)
            # Wait until no booking is in progress
            self._booking_in_progress.wait()
            
            logger.info("[SCHEDULER] === STARTING SYNC CYCLE ===")
            
            # 1. Process User Requests (Deletes) First
            logger.debug("[SCHEDULER] Processing delete requests...")
            try:
                self._process_delete_requests()
            except Exception as e:
                logger.error(f"[SCHEDULER] Delete processing failed: {e}")
            
            # 2. Sync Server State to Calendar
            if self.syncer:
                logger.debug("[SCHEDULER] Running periodic calendar sync...")
                try:
                    self.syncer.sync_all_users()
                    logger.info("[SCHEDULER] === SYNC CYCLE COMPLETE ===")
                except Exception as e:
                    logger.error(f"[SCHEDULER] Periodic sync failed: {e}")

    def _process_delete_requests(self):
        """
        Scans calendar for events with DELETE in title and processes deletion requests.
        Marks events as [DELETED] with red color after successful deletion.
        """
        try:
            now = datetime.utcnow()
            end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
            tmin = now.isoformat() + 'Z'
            tmax = end.isoformat() + 'Z'
            
            events_result = self.calendar_client.service.events().list(
                calendarId=self.calendar_id,
                timeMin=tmin,
                timeMax=tmax,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            delete_count = 0
            skip_count = 0
            
            for event in events_result.get('items', []):
                summary = event.get('summary', '')
                
                # Check if event contains DELETE keyword (case-insensitive)
                if config.DELETE_KEYWORD.upper() not in summary.upper():
                    continue
                
                # Only process events that have been processed/synced ([P] or [S] prefix)
                # Check if [P] or [S] exists anywhere in the title (user may add DELETE before it)
                if '[P]' not in summary and '[S]' not in summary:
                    logger.warning(f"Skipping delete request for unprocessed event: {summary}")
                    skip_count += 1
                    continue
                
                # Skip already deleted events
                if '[D]' in summary:
                    continue
                
                # Extract reference number from description
                description = event.get('description', '')
                ref_num = None
                
                # Look for "Ref: XXXXXXXX" pattern in description
                import re
                ref_match = re.search(r'Ref:\s*([A-Z0-9]+)', description)
                if ref_match:
                    ref_num = ref_match.group(1)
                else:
                    logger.error(f"No reference number found in event: {summary}")
                    # Mark event with error
                    self._update_calendar_event(
                        event['id'], 
                        'FAILURE', 
                        event, 
                        reason="DELETE failed - No reference number found"
                    )
                    skip_count += 1
                    continue
                
                # Extract booking owner email from description (format: "Booked for User: email@domain" or "User: email@domain")
                owner_email = None
                owner_match = re.search(r'(?:Booked for )?User:\s*([^\s\n]+)', description)
                if owner_match:
                    owner_email = owner_match.group(1).strip()
                    logger.debug(f"Found booking owner: {owner_email}")
                
                # Find the agent for this booking owner (case-insensitive match)
                owner_agent = None
                if owner_email:
                    # Try exact match first
                    owner_agent = next((a for a in self.agents if a.email.lower() == owner_email.lower()), None)
                    
                    if not owner_agent:
                        # Log available agents for debugging
                        agent_emails = [a.email for a in self.agents]
                        logger.warning(f"Booking owner '{owner_email}' not found in agent list: {agent_emails}")
                
                # Try to delete the booking using the owner's agent, or fall back to trying all
                deleted = False
                agents_to_try = [owner_agent] if owner_agent else self.agents
                
                for agent in agents_to_try:
                    # Force fresh login for delete operations to ensure valid session
                    logger.debug(f"Attempting delete with agent {agent.email}, forcing fresh login...")
                    agent.is_logged_in = False  # Reset flag to force re-login
                    agent.login()
                    
                    if agent.delete_booking(ref_num):
                        deleted = True
                        logger.info(f"[SCHEDULER] [DELETE] Successfully deleted booking {ref_num} from server by {agent.email}")
                        
                        # Remove DELETE keyword from title to avoid re-processing
                        new_summary = summary.replace(config.DELETE_KEYWORD, '').strip()

                        # Mark event as [DELETED] with red color
                        new_summary = new_summary.replace('[P]', '[DELETED]').replace('[S]', '[DELETED]')
                        
                        # Clean up extra spaces
                        new_summary = ' '.join(new_summary.split())
                        
                        updated_event = {**event}
                        updated_event['summary'] = new_summary
                        updated_event['colorId'] = config.CalendarStatus.DELETED
                        updated_desc = f"{description}\n\nDeleted on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        updated_event['description'] = updated_desc
                        
                        self.calendar_client.service.events().update(
                            calendarId=self.calendar_id,
                            eventId=event['id'],
                            body=updated_event
                        ).execute()
                        
                        delete_count += 1
                        break  # Successfully deleted, no need to try other agents
                
                if not deleted:
                    logger.error(f"[SCHEDULER] [DELETE] Failed to delete booking {ref_num}")
                    self._update_calendar_event(
                        event['id'], 
                        'FAILURE', 
                        event, 
                        reason="DELETE failed - Permission denied or server error"
                    )
                    skip_count += 1
            
            if delete_count > 0 or skip_count > 0:
                logger.info(f"[SCHEDULER] [DELETE] Processed {delete_count} deletions, skipped {skip_count}")
                
        except Exception as e:
            logger.error(f"[SCHEDULER] [DELETE] Error processing delete requests: {e}")

    def _load_json(self, filepath: str, default: Any) -> Any:
        if not os.path.exists(filepath): return default
        try:
            with open(filepath, 'r') as f: return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load JSON from {filepath}: {e}")
            return default

    def initialize_agents(self):
        if not self.users_data: return
        logger.info(f"[SCHEDULER] --- Initializing {len(self.users_data)} Agents ---")
        temp_agents = [BookingAgent(u) for u in self.users_data]
        self.agents = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_agent = {executor.submit(a.login): a for a in temp_agents}
            for future in concurrent.futures.as_completed(future_to_agent):
                agent = future_to_agent[future]
                if future.result(): self.agents.append(agent)
        
        logger.info(f"[SCHEDULER] Agents Ready: {len(self.agents)}")

    def _update_calendar_event(self, event_id: str, status: str, original_event: Dict[str, Any], **kwargs):
        """
        Updates the calendar event with the new status (Color, Title, Description).
        """
        try:
            # Base updates
            updates = {}
            new_summary = original_event.get('summary', '')
            # Ensure title starts with [P] if not already
            if not new_summary.startswith("[P]"):
                new_summary = f"[P] {new_summary}"

            current_desc = original_event.get('description', '')

            if status == 'PROCESSING':
                updates['colorId'] = config.CalendarStatus.PROCESSING
            
            elif status == 'SUCCESS':
                updates['colorId'] = config.CalendarStatus.SUCCESS
                room_name = kwargs.get('room_name', 'Unknown')
                ref_num = kwargs.get('ref_num', 'N/A')
                booked_user = kwargs.get('user', 'Unknown')
                
                # Append Room to Title if not present
                if f"- {room_name}" not in new_summary:
                    new_summary = f"{new_summary} - {room_name}"
                
                updates['location'] = room_name
                updates['description'] = f"{current_desc}\n\nBooked for User: {booked_user}\nRoom: {room_name}\nRef: {ref_num}"
            
            elif status == 'FAILURE':
                updates['colorId'] = config.CalendarStatus.FAILURE
                reason = kwargs.get('reason', 'Unknown Error')
                updates['description'] = f"{current_desc}\n\nError: {reason}"

            updates['summary'] = new_summary
            
            # Merge updates into event body
            body = {**original_event, **updates}
            
            self.calendar_client.service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=body
            ).execute()
            logger.info(f"Updated Event {event_id} status to {status}")
            
        except Exception as e:
            logger.error(f"Failed to update calendar event {event_id}: {e}")

    def _delete_event(self, event_id: str):
        """Delete a calendar event by ID."""
        try:
            self.calendar_client.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id
            ).execute()
            logger.info(f"Deleted event {event_id}")
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}")

    def _get_event_duration_hours(self, event: Dict[str, Any]) -> int:
        """Calculate event duration in hours."""
        start_str = event['start'].get('dateTime') or event['start'].get('date')
        end_str = event['end'].get('dateTime') or event['end'].get('date')
        if not start_str or not end_str:
            return 1
        
        try:
            dt_start = datetime.fromisoformat(start_str)
            dt_end = datetime.fromisoformat(end_str)
            duration = (dt_end - dt_start).total_seconds() / 3600
            return int(duration)
        except Exception as e:
            logger.error(f"Failed to calculate duration: {e}")
            return 1

    def _create_event_for_slot(self, summary: str, start_time: datetime, end_time: datetime, 
                               color_id: str = None, description: str = "", location: str = "") -> Optional[str]:
        """Create a new calendar event for a time slot."""
        try:
            event_body = {
                'summary': summary,
                'start': {'dateTime': start_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
                'end': {'dateTime': end_time.isoformat(), 'timeZone': 'Asia/Jerusalem'},
                'description': description,
                'location': location
            }
            if color_id:
                event_body['colorId'] = color_id
            
            created_event = self.calendar_client.service.events().insert(
                calendarId=self.calendar_id,
                body=event_body
            ).execute()
            logger.info(f"Created event: {summary}")
            return created_event['id']
        except Exception as e:
            logger.error(f"Failed to create event: {e}")
            return None

    def _split_event_after_booking(self, original_event: Dict[str, Any], booked_start: datetime, 
                                   booked_end: datetime, room_name: str, ref_num: str, user: str):
        """Split a multi-hour event when only part is booked."""
        try:
            # Parse original event times
            orig_start_str = original_event['start'].get('dateTime')
            orig_end_str = original_event['end'].get('dateTime')
            orig_start = datetime.fromisoformat(orig_start_str)
            orig_end = datetime.fromisoformat(orig_end_str)
            
            # Create booked portion event
            booked_summary = f"[P] Booking: {original_event.get('summary', '').replace('Booking: ', '')} - {room_name}"
            booked_desc = f"{original_event.get('description', '')}\n\nBooked for User: {user}\nRoom: {room_name}\nRef: {ref_num}"
            self._create_event_for_slot(
                summary=booked_summary,
                start_time=booked_start,
                end_time=booked_end,
                color_id=config.CalendarStatus.SUCCESS,
                description=booked_desc,
                location=room_name
            )
            
            # Update original event to exclude booked time
            if booked_end < orig_end:
                # Remaining time after booking
                updated_event = {**original_event}
                updated_event['start'] = {'dateTime': booked_end.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                self.calendar_client.service.events().update(
                    calendarId=self.calendar_id,
                    eventId=original_event['id'],
                    body=updated_event
                ).execute()
                logger.info(f"Split event: booked {booked_start} to {booked_end}, remaining {booked_end} to {orig_end}")
            elif booked_start > orig_start:
                # Remaining time before booking
                updated_event = {**original_event}
                updated_event['end'] = {'dateTime': booked_start.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                self.calendar_client.service.events().update(
                    calendarId=self.calendar_id,
                    eventId=original_event['id'],
                    body=updated_event
                ).execute()
                logger.info(f"Split event: remaining {orig_start} to {booked_start}, booked {booked_start} to {booked_end}")
            else:
                # Fully booked - delete original
                self._delete_event(original_event['id'])
                logger.info(f"Event fully booked, deleted original")
                
        except Exception as e:
            logger.error(f"Failed to split event: {e}")

    def _merge_consecutive_bookings(self, event_id: str, room_name: str, user: str, 
                                    start_time: datetime, end_time: datetime):
        """Merge consecutive successful bookings on same room/user."""
        try:
            # Fetch all processed events in a reasonable range
            # Ensure we search using proper UTC ISO format 'Z'
            # Convert start/end to UTC first to avoid +02:00Z double suffix
            search_start_dt = (start_time - timedelta(hours=12)).astimezone(timezone.utc)
            search_end_dt = (end_time + timedelta(hours=12)).astimezone(timezone.utc)
            
            search_start = search_start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            search_end = search_end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            events_result = self.calendar_client.service.events().list(
                calendarId=self.calendar_id,
                timeMin=search_start,
                timeMax=search_end,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            # Find adjacent bookings with same room/user
            adjacent_events = []
            for event in events_result.get('items', []):
                summary = event.get('summary', '')
                if not summary.startswith('[P] Booking:'): continue
                if room_name not in summary: continue
                
                desc = event.get('description', '')
                if f"User: {user}" not in desc or f"Room: {room_name}" not in desc: continue
                
                evt_start_str = event['start'].get('dateTime')
                evt_end_str = event['end'].get('dateTime')
                if not evt_start_str or not evt_end_str: continue
                
                evt_start = datetime.fromisoformat(evt_start_str)
                evt_end = datetime.fromisoformat(evt_end_str)
                
                # Check if adjacent (within 1 minute tolerance)
                if abs((evt_end - start_time).total_seconds()) < 60 or abs((end_time - evt_start).total_seconds()) < 60:
                    adjacent_events.append({
                        'id': event['id'],
                        'start': evt_start,
                        'end': evt_end,
                        'event': event
                    })
            
            if adjacent_events:
                # Merge all adjacent events
                all_starts = [e['start'] for e in adjacent_events] + [start_time]
                all_ends = [e['end'] for e in adjacent_events] + [end_time]
                merged_start = min(all_starts)
                merged_end = max(all_ends)
                
                # Delete old events
                for adj in adjacent_events:
                    self._delete_event(adj['id'])
                
                # Get current event to extract metadata
                current_event = self.calendar_client.service.events().get(
                    calendarId=self.calendar_id,
                    eventId=event_id
                ).execute()
                
                # Update current event to span merged duration
                current_event['start'] = {'dateTime': merged_start.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                current_event['end'] = {'dateTime': merged_end.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                
                self.calendar_client.service.events().update(
                    calendarId=self.calendar_id,
                    eventId=event_id,
                    body=current_event
                ).execute()
                
                logger.info(f"Merged {len(adjacent_events) + 1} consecutive bookings: {merged_start} to {merged_end}")
                
        except Exception as e:
            logger.error(f"Failed to merge consecutive bookings: {e}")

    def _fetch_calendar_bookings(self) -> List[Dict[str, Any]]:
        """
        Scans the calendar for 'Booking' events in the next CALENDAR_SCAN_DAYS.
        """
        now = datetime.utcnow()
        end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
        tmin = now.isoformat() + 'Z'
        tmax = end.isoformat() + 'Z'
        
        candidates = []
        try:
            events_result = self.calendar_client.service.events().list(
                calendarId=self.calendar_id,
                timeMin=tmin,
                timeMax=tmax,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            raw_items = events_result.get('items', [])
            
            for event in raw_items:
                summary = event.get('summary', '')
                
                # Filter Logic
                if not summary.startswith("Booking"): continue
                if summary.startswith("[P]"): continue # already processed
                if summary.startswith("[S]"): continue # server-synced, ignore
                
                # Parse Start Time
                start_str = event['start'].get('dateTime') or event['start'].get('date')
                if not start_str: continue
                
                # Handle ISO format with timezone
                # Google returns: "2026-02-14T10:00:00+02:00"
                # We need a datetime object for math
                # Simple parsing (ignoring offset for rough 'opening' calc for now, or using properly)
                # Ideally use dateutil, but let's try standard lib logic or simple cut
                # NOTE: The system expects UTC strings for 'utc_start' and local datetime for 'start'
                # Let's trust the string provided by Google for the 'utc' part (requires converting to UTC properly)
                
                # For simplicity in this POC environment without dateutil:
                # We'll treat the time as the Target Slot Time.
                # If offset is present, python 3.7+ fromisoformat handles it.
                try:
                    dt_start = datetime.fromisoformat(start_str)
                except ValueError:
                    # Fallback for older python or Z format if mixed
                    dt_start = datetime.strptime(start_str.replace('Z', '+00:00'), "%Y-%m-%dT%H:%M:%S%z")
                
                # Convert to naive local/server time for 'opening_time' calc logic if needed, 
                # OR just work with aware datetimes. 
                # The existing logic: opening_time = slot_start - 7 days + 1 hour.
                opening_time = dt_start - timedelta(days=7) + timedelta(hours=1)
                
                # UTC Start/End strings for the BookingAgent API
                # The API expects "2026-02-14T10:00:00.000Z" (UTC).
                # Google might give "+02:00". We need to convert.
                dt_utc_start = dt_start.astimezone(timezone.utc) # Convert to UTC
                
                # End time
                end_str = event['end'].get('dateTime') or event['end'].get('date')
                dt_end = datetime.fromisoformat(end_str)
                dt_utc_end = dt_end.astimezone(timezone.utc)
                
                candidates.append({
                    "start": dt_start,
                    "opening_time": opening_time,
                    "key": summary, # Display name
                    # Fix: .astimezone() defaulted to local, causing offset logic bugs.
                    # We strictly want the UTC digits for the server.
                    "utc_start": dt_utc_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "utc_end": dt_utc_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    "event_id": event['id'],
                    "original_event": event
                })

        except Exception as e:
            logger.error(f"Error fetching calendar events: {e}")
            
        if candidates:
            logger.info(f"[SCHEDULER] Found {len(candidates)} valid 'Booking' candidates.")
            for c in candidates:
                logger.debug(f"[SCHEDULER]    > Candidate: '{c['key']}' @ {c['start']}")
        else:
            logger.debug("[SCHEDULER] No valid 'Booking' candidates found.")
            
        return candidates

    def get_next_target_slot(self) -> Tuple[Optional[Dict[str, Any]], float]:
        """
        Finds the single most urgent slot to book from the Calendar.
        """
        now = datetime.now().astimezone() # Make sure now is aware if we compare with aware
        
        candidates = self._fetch_calendar_bookings()
        
        # Sort by opening time
        candidates.sort(key=lambda x: x['opening_time'])
        
        # Filter out those that are ALREADY PASSED?
        # Actually, if we missed the opening window but it's still 'Processing' (Yellow) or Unprocessed,
        # we should probably try to book it immediately (Late Snipe).
        # But if it's PAST the START time of the event, it's too late.
        
        valid_candidates = []
        for cand in candidates:
            if cand['start'] > now:
                valid_candidates.append(cand)
            else:
                # Event already started/past. Mark as Failed?
                logger.warning(f"Event {cand['key']} is in the past. Marking failed.")
                self._update_calendar_event(cand['event_id'], 'FAILURE', cand['original_event'], reason="Event in the past")
        
        if not valid_candidates: return None, 0

        target = valid_candidates[0]
        
        # We need to compare aware vs aware.
        # target['opening_time'] is likely aware (fromisoformat).
        # now (datetime.now()) might be naive.
        # Safety:
        op_time = target['opening_time']
        if op_time.tzinfo is None:
            op_time = op_time.replace(tzinfo=now.tzinfo) # Assume local if naive
            
        seconds_until_open = (op_time - now).total_seconds()
        
        # If seconds_until_open is negative, it means it's ALREADY OPEN. 
        # We should return 0 (or negative) to indicate "Go Now".
        
        return target, seconds_until_open

    def attempt_booking(self, slot: Dict[str, Any]) -> bool:
        # Signal that a booking is in progress (pause sync)
        self._booking_in_progress.clear()
        try:
            return self._attempt_booking_inner(slot)
        finally:
            # Signal that booking is done (sync can resume)
            self._booking_in_progress.set()

    def _attempt_booking_inner(self, slot: Dict[str, Any]) -> bool:
        exhausted_emails = set()
        
        # 0. MARK AS PROCESSING (Yellow)
        self._update_calendar_event(slot['event_id'], 'PROCESSING', slot['original_event'])

        # 1. ORGANIZE ROOMS (High Priority First)
        room_batches = [
            list(config.HIGH_PRIORITY_ROOMS.keys()), # Batch 1
            list(config.LOW_PRIORITY_ROOMS.keys())   # Batch 2
        ]
        
        # 2. ORGANIZE USERS
        active_agents = list(self.agents) # shallow copy
        if self.last_successful_user:
            priority_agent = next((a for a in active_agents if a.email == self.last_successful_user), None)
            if priority_agent:
                active_agents.remove(priority_agent)
                active_agents.insert(0, priority_agent)

        # --- PRE-FETCH AGENT BOOKINGS (For Smart Extension) ---
        agent_server_bookings = {}
        target_day = slot['start']
        logger.info(f"Pre-fetching user bookings for {target_day.date()}...")
        
        def fetch_for_agent(agent):
            try:
                # Add logging to see progress
                logger.debug(f"Fetching for {agent.email}...")
                return agent.email, agent.get_user_bookings(target_day, target_day)
            except Exception as e:
                logger.error(f"Failed to fetch bookings for {agent.email}: {e}")
                return agent.email, []

        # Strict global timeout for pre-fetching: 20 seconds total.
        try:
             with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                future_to_agent = {executor.submit(fetch_for_agent, a): a for a in active_agents}
                # Even with 8 threads, if everyone hangs, we stop.
                # We simply iterate with a timeout. If it times out, we exit the loop.
                # The 'with' block will then try to exit. executor.shutdown(wait=True) is default.
                # WE MUST MANUALLY SHUTDOWN with wait=False if we want to kill it?
                # Actually, ThreadPoolExecutor doesn't support 'kill'.
                # But we can at least proceed with what we have if we break out.
                # WAIT: 'with' block WILL block until all threads are done.
                # We need to NOT use 'with' if we want to proceed while they are stuck.
                # OR we accept that we can't kill them, but we want to proceed.
                # If we use 'shutdown(wait=False)', the threads continue but we don't wait.
                
                # CORRECT PATTERN for "proceed anyway":
                pass 
                
        except Exception:
            pass

        # RE-WRITE to avoid 'with' blocking:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        future_to_agent = {executor.submit(fetch_for_agent, a): a for a in active_agents}
        
        try:
             for future in concurrent.futures.as_completed(future_to_agent, timeout=20):
                try:
                    email, bookings = future.result()
                    agent_server_bookings[email] = bookings
                except Exception as e:
                    logger.error(f"Worker for unknown agent failed: {e}")
        except concurrent.futures.TimeoutError:
             logger.error("Pre-fetching bookings timed out! Moving on with partial data.")
        
        # Non-blocking shutdown to let stragglers die off in background
        executor.shutdown(wait=False)

        # --- RETRY LOOP (For Sniping) ---
        # --- RETRY LOOP (For Sniping) ---
        start_time = time.time()
        exhausted_emails = set()
        failed_rooms = set()  # Track rooms that are definitely taken/unavailable for this slot
        
        while True:
            # --- TIMEOUT / GIVE UP LOGIC ---
            if time.time() - start_time > 120:
                logger.info(f"[SCHEDULER] [BOOKING] [TIMEOUT] Could not book {slot['key']} within 2 mins.")
                self._update_calendar_event(slot['event_id'], 'FAILURE', slot['original_event'], reason="Timeout - Could not secure room")
                return False

            # Check if all rooms are failed
            total_rooms_count = sum(len(rooms) for rooms in room_batches)
            if len(failed_rooms) >= total_rooms_count:
                 logger.warning(f"[SCHEDULER] [BOOKING] [FAILED] All {total_rooms_count} rooms are unavailable for {slot['key']}.")
                 self._update_calendar_event(slot['event_id'], 'FAILURE', slot['original_event'], reason="All Rooms Taken")
                 return False

            for batch_name, rooms in zip(["HIGH", "LOW"], room_batches):
                for rid in rooms:
                    if rid in failed_rooms: continue
                    
                    rname = config.ALL_ROOMS[rid]
                    
                    for agent in active_agents:
                        if agent.email in exhausted_emails: continue

                        # Note: We removed the complex "Extend" logic for this POC/Refactor to simplify.
                        # The user asked to "Connect this feature... verify...". 
                        # If strict equivalence to old logic is needed, we'd add 'extend' check here.
                        # For now, we will stick to basic booking to ensure the Calendar Integration works.
                        # (Simplification per "Replace existing... logic")
                        
                        # CREATE NEW
                        res, ref_num = agent.book_room(rid, slot['utc_start'], slot['utc_end'])

                        if res == BookingResult.SUCCESS:
                            logger.info(f"[SCHEDULER] [BOOKING] [SUCCESS] {slot['key']} | {rname} | {agent.email} | Ref: {ref_num}")
                            self.last_successful_user = agent.email
                            
                            # --- GOOGLE CALENDAR SYNC (SUCCESS) ---
                            # Calculate slot times for splitting/merging
                            # Parse UTC string (Z) back to aware datetime
                            slot_start_dt = datetime.fromisoformat(slot['utc_start'].replace('Z', '+00:00'))
                            slot_end_dt = datetime.fromisoformat(slot['utc_end'].replace('Z', '+00:00'))
                            
                            # Check if this is a multi-hour event that needs splitting
                            original_duration = self._get_event_duration_hours(slot['original_event'])
                            
                            if original_duration > 1:
                                # Split event: booked portion + remaining
                                logger.info(f"Multi-hour event detected ({original_duration}h). Splitting...")
                                self._split_event_after_booking(
                                    original_event=slot['original_event'],
                                    booked_start=slot_start_dt,
                                    booked_end=slot_end_dt,
                                    room_name=rname,
                                    ref_num=ref_num,
                                    user=agent.email
                                )
                                
                                # Try to merge with adjacent bookings
                                # Note: The newly created event from split doesn't have an ID yet,
                                # so we need to fetch it or use the original event logic differently
                                # For simplicity, we'll search for the new event and merge
                                time.sleep(0.5)  # Brief pause for event creation to propagate
                                
                                # Fetch the newly created booked event
                                now_iso = datetime.utcnow().isoformat() + 'Z'
                                search_end_iso = (datetime.utcnow() + timedelta(days=1)).isoformat() + 'Z'
                                recent_events = self.calendar_client.service.events().list(
                                    calendarId=self.calendar_id,
                                    timeMin=now_iso,
                                    timeMax=search_end_iso,
                                    singleEvents=True,
                                    orderBy='startTime'
                                ).execute()
                                
                                # Find the event we just created
                                for evt in recent_events.get('items', []):
                                    evt_start_str = evt['start'].get('dateTime')
                                    if evt_start_str:
                                        evt_start = datetime.fromisoformat(evt_start_str)
                                        if abs((evt_start - slot_start_dt).total_seconds()) < 60:
                                            # This is our newly created event
                                            self._merge_consecutive_bookings(
                                                event_id=evt['id'],
                                                room_name=rname,
                                                user=agent.email,
                                                start_time=slot_start_dt,
                                                end_time=slot_end_dt
                                            )
                                            break
                            else:
                                # Single-hour event - mark as processed and try to merge
                                new_event_id = None
                                
                                # Update the original event to mark as processed
                                self._update_calendar_event(
                                    slot['event_id'], 
                                    'SUCCESS', 
                                    slot['original_event'],
                                    room_name=rname,
                                    ref_num=ref_num,
                                    user=agent.email
                                )
                                
                                # Try to merge with adjacent bookings
                                self._merge_consecutive_bookings(
                                    event_id=slot['event_id'],
                                    room_name=rname,
                                    user=agent.email,
                                    start_time=slot_start_dt,
                                    end_time=slot_end_dt
                                )
                            
                            return True
                        
                        elif res == BookingResult.USER_LIMIT:
                            logger.info(f"[SCHEDULER] [BOOKING] [USER LIMIT] {agent.email} exhausted.")
                            exhausted_emails.add(agent.email)
                        
                        elif res == BookingResult.ROOM_TAKEN:
                            logger.info(f"[SCHEDULER] [BOOKING] [ROOM TAKEN] {rname} is unavailable.")
                            failed_rooms.add(rid)
                            break # Agent valid, Room dead. Next Room.
                        
                        elif res == BookingResult.CLOSED:
                            logger.warning(f"[SCHEDULER] [BOOKING] [CLOSED] Library closed or invalid booking time for {rname}.")
                            failed_rooms.add(rid) 
                            # If closed, it's likely closed for ALL agents on this room.
                            break

                        elif res == BookingResult.TOO_EARLY:
                            logger.warning(f"[SCHEDULER] [BOOKING] [TOO EARLY] Window not open yet for {slot['key']}. Retrying...")
                            start_time = time.time() # Reset timeout if we are just early? 
                            # Actually, if we are early, we should just retry loop. 
                            # But we need to avoid infinite loop if it NEVER opens (e.g. calculation wrong).
                            # The global timeout handles that.
                            break
                        
                        elif res == BookingResult.ERROR:
                             logger.warning(f"[SCHEDULER] [BOOKING] [ERROR] Agent {agent.email} failed on {rname}.")

            # --- ALL USERS EXHAUSTED LOGIC ---
            if len(exhausted_emails) == len(self.agents):
                logger.warning(f"[SCHEDULER] [BOOKING] [FAILED] All users exhausted limit for {slot['key']}.")
                self._update_calendar_event(slot['event_id'], 'FAILURE', slot['original_event'], reason="All Users Quotas Exhausted")
                return False
            
            # Likely "Not Open Yet". Sleep tiny bit and Retry.
            time.sleep(0.5)

    def run(self):
        # Initialize agents at startup
        logger.info("[SCHEDULER] Initializing agents at startup...")
        self.initialize_agents()
        
        # Sync on startup
        if self.syncer:
            logger.info("[SCHEDULER] Running startup calendar sync...")
            try:
                self.syncer.sync_all_users()
            except Exception as e:
                logger.error(f"[SCHEDULER] Startup sync failed: {e}")
            
            # Start periodic sync daemon thread
            sync_thread = threading.Thread(target=self._sync_worker, daemon=True)
            sync_thread.start()
            logger.info(f"[SCHEDULER] Periodic sync thread started (every {config.SYNC_INTERVAL_SECONDS}s).")
        
            logger.info(f"Periodic sync thread started (every {config.SYNC_INTERVAL_SECONDS}s).")

        while True:
            target, seconds_wait = self.get_next_target_slot()
            
            if not target:
                logger.info(f"[SCHEDULER] No bookings found in calendar (next {config.CALENDAR_SCAN_DAYS} days). Sleeping {config.CALENDAR_POLL_INTERVAL_SECONDS}s.")
                time.sleep(config.CALENDAR_POLL_INTERVAL_SECONDS)
                continue

            if seconds_wait > 60:
                # Cap sleep time to ensure we poll for new events
                if seconds_wait > config.CALENDAR_POLL_INTERVAL_SECONDS:
                    logger.info(f"[SCHEDULER] >> Target is far ({int(seconds_wait/60)}m). Sleeping 5m then re-scanning...")
                    time.sleep(config.CALENDAR_POLL_INTERVAL_SECONDS)
                    continue

                sleep_time = seconds_wait - 60
                wake_time = datetime.now().astimezone() + timedelta(seconds=sleep_time)
                logger.info(f"[SCHEDULER] >> Target: '{target['key']}' opens in {int(seconds_wait/60)}m.")
                logger.info(f"[SCHEDULER] >> Sleeping until {wake_time.strftime('%H:%M:%S')}...")
                time.sleep(sleep_time)
            
            # WAKE UP SEQUENCE
            logger.info(f"[SCHEDULER] !!! WAKING UP FOR: {target['key']} !!!")
            
            # 1. Re-Verify Login
            self.initialize_agents()
            
            # 2. Wait exactly for opening time
            # Recalculate wait
            now = datetime.now().astimezone()
            op_time = target['opening_time']
            if op_time.tzinfo is None: op_time = op_time.replace(tzinfo=now.tzinfo)

            final_wait = (op_time - now).total_seconds() - 5
            if final_wait > 0:
                time.sleep(final_wait)
            
            # 3. ATTACK
            logger.info(f"--- EXECUTING BOOKING: {target['key']} ---")
            self.attempt_booking(target)