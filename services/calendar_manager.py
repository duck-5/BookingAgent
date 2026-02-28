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

    def _extract_ref_and_user(self, description: str) -> Tuple[Optional[str], Optional[str]]:
        ref_match = re.search(r'Ref:\s*([A-Z0-9]+)', description)
        ref_num = ref_match.group(1) if ref_match else None
        
        user_match = re.search(r'(?:Booked for )?User:\s*([^\s\n]+)', description)
        user = user_match.group(1).strip() if user_match else None
        
        return ref_num, user

    def scan_for_bookings(self) -> List[BookingRequest]:
        """
        Scans for 'Booking' events in the next X days. 
        Returns parsed BookingRequest objects.
        """
        now = datetime.utcnow()
        end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
        tmin = now.isoformat() + 'Z'
        tmax = end.isoformat() + 'Z'
        
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
                    dt_start = datetime.fromisoformat(start_str)
                except ValueError:
                    dt_start = datetime.strptime(start_str.replace('Z', '+00:00'), "%Y-%m-%dT%H:%M:%S%z")
                
                # Opening time calculation (7 days prior + 1 hour)
                opening_time = dt_start - timedelta(days=7) + timedelta(hours=1)
                
                # UTC Start/End strings for the BookingAgent API
                dt_utc_start = dt_start.astimezone(timezone.utc)
                
                # End time
                end_str = event['end'].get('dateTime') or event['end'].get('date')
                dt_end = datetime.fromisoformat(end_str)
                dt_utc_end = dt_end.astimezone(timezone.utc)
                
                candidates.append(BookingRequest(
                    event_id=event['id'],
                    summary=summary,
                    start_time=dt_start,
                    end_time=dt_end,
                    utc_start=dt_utc_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    utc_end=dt_utc_end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    original_event=event,
                    opening_time=opening_time
                ))

        except Exception as e:
            logger.error(f"Error fetching calendar events: {e}")
            
        return candidates

    def scan_for_deletions(self) -> List[Any]: # List[DeletionRequest]
        """
        Scans for DELETE requests in the coming days.
        """
        now = datetime.utcnow()
        end = now + timedelta(days=config.CALENDAR_SCAN_DAYS)
        # Scan slightly in the past to ensure we catch currently active or slightly passed events
        tmin = (now - timedelta(days=1)).isoformat() + 'Z'
        tmax = end.isoformat() + 'Z'
        
        requests = []
        try:
            items = self._fetch_events(tmin, tmax, order_by_start=True)
            for event in items:
                summary = event.get('summary', '')
                if config.DELETE_KEYWORD.upper() not in summary.upper(): continue
                if '[D]' in summary: continue # Already deleted
                
                # Must be processed/synced
                if '[P]' not in summary and '[S]' not in summary: continue
                
                description = event.get('description', '')
                
                ref_num, owner_email = self._extract_ref_and_user(description)
                if not ref_num:
                    logger.warning(f"DELETE request found but no Ref num: {summary}")
                    continue
                
                requests.append(DeletionRequest(
                    event_id=event['id'],
                    summary=summary,
                    ref_num=ref_num,
                    original_event=event,
                    owner_email=owner_email
                ))
                
        except Exception as e:
            logger.error(f"Error scanning for deletions: {e}")
            
        return requests

    def update_event_status(self, event_id: str, status: str, original_event: Dict[str, Any], **kwargs):
        """
        Updates the calendar event with the new status (Color, Title, Description).
        """
        try:
            # Always fetch the strictly latest version of the event to avoid Sequence/Concurrency 400 errors
            latest_event = self.client.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
            
            updates = {}
            new_summary = latest_event.get('summary', '')
            if not new_summary.startswith("[P]"):
                new_summary = f"[P] {new_summary}"

            current_desc = latest_event.get('description', '')

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
            orig_start = datetime.fromisoformat(original_event['start'].get('dateTime'))
            orig_end = datetime.fromisoformat(original_event['end'].get('dateTime'))
            
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

    def find_successful_booking(self, end_time: datetime) -> Optional[Tuple[Dict[str, Any], str, str, str]]:
        """
        Finds a successful booking event that ends at the given time (approx).
        Returns (event, ref_num, user_email, room_name) or None.
        """
        # We need to scan past events. 
        # For efficiency, we might need a separate scan or cache. 
        # But for now, let's just list events for the specific day around the time.
        
        tmin = (end_time - timedelta(hours=4)).isoformat()
        tmax = (end_time + timedelta(hours=1)).isoformat()
        
        try:
            items = self._fetch_events(tmin, tmax, order_by_start=False)
            for event in items:
                summary = event.get('summary', '')
                # check for [P] or [S]
                if not ("[P]" in summary or "[S]" in summary): continue
                # check for SUCCESS color (11?) - or just rely on description/summary
                
                # Check End Time
                # We need strict matching. 
                end_str = event['end'].get('dateTime') or event['end'].get('date')
                dt_end = datetime.fromisoformat(end_str)
                
                # Compare. Note timezone handling.
                # end_time passed in should be timezone aware (UTC or local)
                # Let's standardize to UTC for comparison
                if dt_end.tzinfo:
                     dt_end_utc = dt_end.astimezone(timezone.utc)
                else: 
                     # assume local? illegal.
                     dt_end_utc = dt_end.replace(tzinfo=timezone.utc) # fallback
                     
                target_utc = end_time.astimezone(timezone.utc)
                
                if abs((dt_end_utc - target_utc).total_seconds()) < 60: # 1 min tolerance
                     # Found candidate. Extract details.
                     desc = event.get('description', '')
                     
                     ref_num, user = self._extract_ref_and_user(desc)
                     if not ref_num or not user: continue
                     
                     # Extract Room (from location or summary)
                     room = event.get('location', '')
                     
                     return (event, ref_num, user, room)
                     
            return None
            
        except Exception as e:
            logger.error(f"Error finding past booking: {e}")
            return None
