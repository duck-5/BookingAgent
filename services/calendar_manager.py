import logging
import threading
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Tuple

from clients.google_calendar import GoogleCalendarClient
from core.entities import BookingRequest, DeletionRequest
from core.enums import CalendarStatus
import config

logger = logging.getLogger(__name__)

def parse_iso_datetime(dt_str: str) -> datetime:
    """Safely parse ISO 8601 strings across Python versions including 'Z' suffix and fractional seconds."""
    if not dt_str:
        raise ValueError("Empty datetime string")
    try:
        return datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
    except ValueError:
        return datetime.strptime(dt_str.replace('Z', '+00:00'), "%Y-%m-%dT%H:%M:%S%z")

class RefAndUser(tuple):
    """
    Tuple (primary_ref, user) with .ref_nums attribute exposing all reference numbers.
    """
    def __new__(cls, primary_ref: Optional[str], user: Optional[str], ref_nums: Optional[List[str]] = None):
        return super(RefAndUser, cls).__new__(cls, (primary_ref, user))

    def __init__(self, primary_ref: Optional[str], user: Optional[str], ref_nums: Optional[List[str]] = None):
        self.ref_num = primary_ref
        self.user = user
        self.ref_nums = ref_nums if ref_nums is not None else ([primary_ref] if primary_ref else [])

class PrecedingBooking(tuple):
    """
    Represents a preceding booking that can be unpacked as (event, ref_num, user, room)
    for backwards compatibility, but also exposes named attributes and duration_hours.
    """
    def __new__(cls, event: Dict[str, Any], ref_num: str, user: str, room: str, duration_hours: float = 1.0):
        return super(PrecedingBooking, cls).__new__(cls, (event, ref_num, user, room))

    def __init__(self, event: Dict[str, Any], ref_num: str, user: str, room: str, duration_hours: float = 1.0):
        self.event = event
        self.ref_num = ref_num
        self.user = user
        self.room = room
        self.duration_hours = duration_hours

class CalendarManager:
    def __init__(self, google_client: GoogleCalendarClient):
        self.client = google_client
        self.calendar_id = self.client.get_or_create_calendar(config.CALENDAR_NAME)

    def _fetch_events(self, tmin: str, tmax: str, order_by_start: bool = False) -> List[Dict[str, Any]]:
        kwargs = {
            'calendarId': self.calendar_id,
            'timeMin': tmin,
            'timeMax': tmax,
            'singleEvents': True
        }
        if order_by_start:
            kwargs['orderBy'] = 'startTime'
            
        result = self.client.service.events().list(**kwargs).execute()
        return result.get('items', [])

    def _extract_ref_and_user(self, description: str) -> RefAndUser:
        ref_matches = re.findall(r'Ref:\s*([a-zA-Z0-9_-]+)', description)
        primary_ref = ref_matches[0] if ref_matches else None
        
        user_match = re.search(r'(?:Booked for )?User:\s*([^\s\n]+)', description)
        user = user_match.group(1).strip() if user_match else None
        
        return RefAndUser(primary_ref, user, ref_matches)

    def _create_booking_request(self, event: Dict[str, Any], dt_start: datetime, dt_end: datetime) -> BookingRequest:
        summary = event.get('summary', '')
        opening_time = dt_start - timedelta(days=7) + timedelta(hours=1)
        dt_utc_start = dt_start.astimezone(timezone.utc) if dt_start.tzinfo else dt_start.replace(tzinfo=timezone.utc)
        dt_utc_end = dt_end.astimezone(timezone.utc) if dt_end.tzinfo else dt_end.replace(tzinfo=timezone.utc)
        return BookingRequest(
            event_id=event['id'],
            summary=summary,
            start_time=dt_start,
            end_time=dt_end,
            utc_start=dt_utc_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            utc_end=dt_utc_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            original_event=event,
            opening_time=opening_time
        )

    def split_multi_hour_event(self, event: Dict[str, Any], dt_start: datetime, dt_end: datetime) -> List[BookingRequest]:
        """
        Splits a multi-hour Google Calendar event into 1-hour chunks on Google Calendar.
        Updates the original event to be the first 1-hour chunk, and inserts new events
        for each subsequent 1-hour chunk.
        Returns a list of BookingRequest objects.
        """
        total_hours = max(1, int(round((dt_end - dt_start).total_seconds() / 3600.0)))
        if total_hours <= 1:
            return [self._create_booking_request(event, dt_start, dt_end)]

        tz = event.get('start', {}).get('timeZone', 'Asia/Jerusalem')
        results = []

        # 1. Update original event to first 1-hour chunk
        first_end = dt_start + timedelta(hours=1)
        updated_orig = {**event}
        updated_orig['end'] = {'dateTime': first_end.isoformat(), 'timeZone': tz}
        try:
            self.client.service.events().update(
                calendarId=self.calendar_id,
                eventId=event['id'],
                body=updated_orig
            ).execute()
        except Exception as e:
            logger.error(f"Error updating first 1-hour chunk of event {event.get('id')}: {e}")

        results.append(self._create_booking_request(updated_orig, dt_start, first_end))

        # 2. Insert new events for subsequent 1-hour chunks
        for i in range(1, total_hours):
            slot_start = dt_start + timedelta(hours=i)
            slot_end = dt_start + timedelta(hours=i + 1)
            try:
                created = self.client.add_event(
                    summary=event.get('summary', 'Booking:'),
                    start_time=slot_start.isoformat(),
                    end_time=slot_end.isoformat(),
                    description=event.get('description', ''),
                    calendar_id=self.calendar_id
                )
                if not created:
                    logger.error(f"Failed to create subsequent chunk {i} for event {event.get('id')}")
                    continue
                if isinstance(created, dict):
                    created_event = created
                else:
                    created_event = {
                        'id': str(created),
                        'summary': event.get('summary', 'Booking:'),
                        'start': {'dateTime': slot_start.isoformat(), 'timeZone': tz},
                        'end': {'dateTime': slot_end.isoformat(), 'timeZone': tz},
                        'description': event.get('description', '')
                    }
                results.append(self._create_booking_request(created_event, slot_start, slot_end))
            except Exception as e:
                logger.error(f"Error creating subsequent chunk {i} for event {event.get('id')}: {e}")

        return results

    def scan_for_bookings(self, split_multi_hour: bool = False) -> List[BookingRequest]:
        """
        Scans for 'Booking' events in the next X days. 
        Returns parsed BookingRequest objects.
        If split_multi_hour is True, splits multi-hour events on Google Calendar into 1-hour chunks.
        """
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
        tmin = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        tmax = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        candidates = []
        try:
            events_result = self.client.service.events().list(
                calendarId=self.calendar_id,
                timeMin=tmin,
                timeMax=tmax,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            for event in events_result.get('items', []):
                summary = event.get('summary', '')
                
                # Filter Logic
                if not summary.startswith("Booking"): continue
                if summary.startswith("[P]"): continue 
                if summary.startswith("[S]"): continue 
                
                # Parse Start Time
                start_str = event['start'].get('dateTime') or event['start'].get('date')
                if not start_str: continue
                
                try:
                    dt_start = parse_iso_datetime(start_str)
                except Exception as e:
                    logger.warning(f"Failed to parse start time {start_str}: {e}")
                    continue
                
                # End time
                end_str = event['end'].get('dateTime') or event['end'].get('date')
                if not end_str: continue
                try:
                    dt_end = parse_iso_datetime(end_str)
                except Exception as e:
                    logger.warning(f"Failed to parse end time {end_str}: {e}")
                    continue
                
                duration_hours = (dt_end - dt_start).total_seconds() / 3600.0
                if split_multi_hour and duration_hours > 1.05:
                    chunks = self.split_multi_hour_event(event, dt_start, dt_end)
                    candidates.extend(chunks)
                else:
                    candidates.append(self._create_booking_request(event, dt_start, dt_end))

        except Exception as e:
            logger.error(f"Error fetching calendar events: {e}")
            
        return candidates

    def scan_for_deletions(self) -> List[Any]: # List[DeletionRequest]
        """
        Scans for DELETE requests in the coming days.
        """
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
        # Scan slightly in the past to ensure we catch currently active or slightly passed events
        tmin = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmax = end.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        requests = []
        try:
            items = self._fetch_events(tmin, tmax, order_by_start=True)
            for event in items:
                summary = event.get('summary', '')
                if config.DELETE_KEYWORD.upper() not in summary.upper(): continue
                if '[D]' in summary or '[DELETED]' in summary.upper(): continue # Already deleted
                
                # Must be processed/synced
                if '[P]' not in summary and '[S]' not in summary: continue
                
                description = event.get('description', '')
                
                extracted = self._extract_ref_and_user(description)
                if not extracted.ref_num and not extracted.ref_nums:
                    logger.warning(f"DELETE request found but no Ref num: {summary}")
                    continue
                
                requests.append(DeletionRequest(
                    event_id=event['id'],
                    summary=summary,
                    ref_num=extracted.ref_num,
                    ref_nums=extracted.ref_nums,
                    original_event=event,
                    owner_email=extracted.user
                ))
                
        except Exception as e:
            logger.error(f"Error scanning for deletions: {e}")
            
        return requests

    def update_event_status(self, event_id: str, status: str, original_event: Dict[str, Any], **kwargs):
        """
        Updates the calendar event with the new status (Color, Title, Description).
        """
        try:
            latest_event = None
            try:
                fetched = self.client.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
                if isinstance(fetched, dict):
                    latest_event = fetched
            except Exception as get_err:
                logger.debug(f"Could not fetch latest event {event_id}: {get_err}")

            if not latest_event:
                latest_event = dict(original_event) if isinstance(original_event, dict) else {}
            
            updates = {}
            new_summary = str(latest_event.get('summary', '') or '')
            if not new_summary.startswith("[P]"):
                new_summary = f"[P] {new_summary}"

            current_desc = str(latest_event.get('description', '') or '')

            if status == CalendarStatus.PROCESSING:
                updates['colorId'] = status # Yellow
            
            elif status == CalendarStatus.SUCCESS:
                # Assuming status passed IS the color ID
                updates['colorId'] = status 
                room_name = kwargs.get('room_name', 'Unknown')
                ref_num = kwargs.get('ref_num', 'N/A')
                booked_user = kwargs.get('user', 'Unknown')
                
                if f"- {room_name}" not in new_summary:
                    new_summary = f"{new_summary} - {room_name}"
                
                updates['location'] = room_name
                updates['description'] = f"{current_desc}\n\nBooked for User: {booked_user}\nRoom: {room_name}\nRef: {ref_num}"
            
            elif status == CalendarStatus.FAILURE:
                updates['colorId'] = status
                reason = kwargs.get('reason', 'Unknown Error')
                updates['description'] = f"{current_desc}\n\nError: {reason}"

            elif status == CalendarStatus.DELETED:
                updates['colorId'] = status # Gray
                deleted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Cleanse summary
                s = new_summary
                s = s.replace("[P]", "").replace("[S]", "").strip()
                
                # Remove DELETE keyword (Case Insensitive)
                s = re.sub(re.escape(config.DELETE_KEYWORD), "", s, flags=re.IGNORECASE).strip()
                
                # Remove leading non-alphanumeric if messy
                s = re.sub(r'^[^a-zA-Z0-9]+', '', s).strip()
                
                # Update new_summary variable so it gets picked up below
                new_summary = f"[DELETED] {s}"
                updates['description'] = f"{current_desc}\n\nDeleted on: {deleted_at}"

            updates['summary'] = new_summary
            
            body = {**latest_event, **updates}
            
            self.client.service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=body
            ).execute()
            logger.info(f"Updated Event {event_id} status to {status}")
            
        except Exception as e:
            logger.error(f"Failed to update calendar event {event_id}: {e}")

    def split_event(self, original_event: Dict[str, Any], booked_start: datetime, 
                    booked_end: datetime, room_name: str, ref_num: str, user: str):
        """Split a multi-hour event when only part is booked."""
        try:
            start_val = original_event['start'].get('dateTime') or original_event['start'].get('date')
            end_val = original_event['end'].get('dateTime') or original_event['end'].get('date')
            orig_start = parse_iso_datetime(start_val)
            orig_end = parse_iso_datetime(end_val)
            
            # Create booked portion event
            booked_summary = f"[P] Booking: {original_event.get('summary', '').replace('Booking: ', '')} - {room_name}"
            booked_desc = f"{original_event.get('description', '')}\n\nBooked for User: {user}\nRoom: {room_name}\nRef: {ref_num}"
            
            self.client.add_event(
                summary=booked_summary,
                start_time=booked_start.isoformat(),
                end_time=booked_end.isoformat(),
                description=booked_desc,
                location=room_name,
                calendar_id=self.calendar_id,
                color_id=CalendarStatus.SUCCESS
            )
            
            # Update original event to exclude booked time
            # Fetch strictly latest representation to avoid Sequence 400 Error
            latest_event = self.client.service.events().get(calendarId=self.calendar_id, eventId=original_event['id']).execute()
            
            if booked_end < orig_end:
                updated_event = {**latest_event}
                updated_event['start'] = {'dateTime': booked_end.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                self.client.service.events().update(
                    calendarId=self.calendar_id, eventId=original_event['id'], body=updated_event
                ).execute()
            elif booked_start > orig_start:
                updated_event = {**latest_event}
                updated_event['end'] = {'dateTime': booked_start.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                self.client.service.events().update(
                    calendarId=self.calendar_id, eventId=original_event['id'], body=updated_event
                ).execute()
            else:
                self.client.service.events().delete(
                    calendarId=self.calendar_id, eventId=original_event['id']
                ).execute()
                
        except Exception as e:
            logger.error(f"Failed to split event: {e}")

    def find_successful_booking(self, end_time: datetime) -> Optional[PrecedingBooking]:
        """
        Finds a successful booking event that ends at the given time (approx).
        Returns PrecedingBooking(event, ref_num, user_email, room_name, duration_hours) or None.
        """
        tmin_dt = (end_time - timedelta(hours=4)).astimezone(timezone.utc) if end_time.tzinfo else (end_time - timedelta(hours=4)).replace(tzinfo=timezone.utc)
        tmax_dt = (end_time + timedelta(hours=1)).astimezone(timezone.utc) if end_time.tzinfo else (end_time + timedelta(hours=1)).replace(tzinfo=timezone.utc)
        tmin = tmin_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        tmax = tmax_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        try:
            items = self._fetch_events(tmin, tmax, order_by_start=False)
            for event in items:
                summary = event.get('summary') or ''
                # check for [P] or [S]
                if not ("[P]" in summary or "[S]" in summary): continue
                # Skip failed, deleted, or processing events
                if event.get('colorId') in (CalendarStatus.FAILURE, CalendarStatus.DELETED, CalendarStatus.PROCESSING):
                    continue
                if '[DELETED]' in summary.upper() or '[D]' in summary or '[FAILED]' in summary.upper():
                    continue
                
                # Check End Time
                # We need strict matching. 
                end_str = (event.get('end') or {}).get('dateTime') or (event.get('end') or {}).get('date')
                if not end_str: continue
                try:
                    dt_end = parse_iso_datetime(end_str)
                except Exception:
                    continue
                
                # Compare. Note timezone handling.
                # Standardize to UTC for comparison
                if dt_end.tzinfo:
                     dt_end_utc = dt_end.astimezone(timezone.utc)
                else: 
                     dt_end_utc = dt_end.replace(tzinfo=timezone.utc)
                     
                target_utc = end_time.astimezone(timezone.utc) if end_time.tzinfo else end_time.replace(tzinfo=timezone.utc)
                
                if abs((dt_end_utc - target_utc).total_seconds()) < 60: # 1 min tolerance
                     # Found candidate. Extract details.
                     desc = event.get('description') or ''
                     
                     extracted = self._extract_ref_and_user(desc)
                     if not extracted.ref_num or not extracted.user: continue
                     
                     # Extract Room (from location, description, or summary)
                     room = event.get('location', '')
                     if not room:
                         room_match = re.search(r'Room:\s*([^\n\r]+)', desc)
                         if room_match:
                             room = room_match.group(1).strip()
                     if not room:
                         for rname in config.ALL_ROOMS.values():
                             if rname in summary:
                                 room = rname
                                 break
                     
                     # Calculate accumulated duration
                     start_str = event['start'].get('dateTime') or event['start'].get('date')
                     if not start_str: continue
                     try:
                         dt_start = parse_iso_datetime(start_str)
                     except Exception:
                         continue
                     if dt_start.tzinfo:
                         dt_start_utc = dt_start.astimezone(timezone.utc)
                     else:
                         dt_start_utc = dt_start.replace(tzinfo=timezone.utc)
                     accumulated_hours = max(1.0, (dt_end_utc - dt_start_utc).total_seconds() / 3600.0)

                     return PrecedingBooking(event, extracted.ref_num, extracted.user, room, duration_hours=accumulated_hours)
                     
            return None
            
        except Exception as e:
            logger.error(f"Error finding past booking: {e}")
            return None

    find_preceding_booking = find_successful_booking

    def merge_adjacent_events(
        self,
        prev_event: Dict[str, Any],
        current_event: Dict[str, Any],
        new_ref: str,
        room_name: str,
        user_email: str
    ) -> bool:
        """
        Merges current_event into prev_event on Google Calendar:
        1. Extends prev_event end time to current_event['end']
        2. Appends new_ref to prev_event description if not already present
        3. Updates prev_event via Google Calendar API
        4. Deletes current_event from Google Calendar
        """
        try:
            prev_id = prev_event['id']
            curr_id = current_event.get('id')
            
            # Fetch latest prev_event representation
            latest_prev = None
            try:
                fetched = self.client.service.events().get(calendarId=self.calendar_id, eventId=prev_id).execute()
                if isinstance(fetched, dict):
                    latest_prev = fetched
            except Exception as e:
                logger.debug(f"Could not fetch latest prev_event {prev_id}: {e}")
            if not latest_prev:
                latest_prev = dict(prev_event)
                
            curr_end = current_event['end'].get('dateTime') or current_event['end'].get('date')
            tz = current_event.get('end', {}).get('timeZone', 'Asia/Jerusalem')
            
            # Update end time
            latest_prev['end'] = {'dateTime': curr_end, 'timeZone': tz}
            
            # Update description with new reference
            current_desc = latest_prev.get('description') or ''
            if new_ref and new_ref not in current_desc:
                latest_prev['description'] = f"{current_desc}\nRef: {new_ref}"
            
            if room_name:
                latest_prev['location'] = room_name
            latest_prev['colorId'] = CalendarStatus.SUCCESS
            
            # Save updated prev_event
            self.client.service.events().update(
                calendarId=self.calendar_id,
                eventId=prev_id,
                body=latest_prev
            ).execute()
            logger.info(f"[MERGE] Extended previous event {prev_id} to {curr_end}")
            
            # Delete current_event
            if curr_id:
                try:
                    self.client.service.events().delete(
                        calendarId=self.calendar_id,
                        eventId=curr_id
                    ).execute()
                    logger.info(f"[MERGE] Deleted current event {curr_id} after merging")
                except Exception as e:
                    logger.warning(f"[MERGE] Could not delete merged current event {curr_id}: {e}")
                
            return True
        except Exception as e:
            logger.error(f"[MERGE] Error merging adjacent events: {e}")
            return False
