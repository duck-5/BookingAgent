import logging
import threading
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
        tmin = now.isoformat() + 'Z'
        tmax = end.isoformat() + 'Z'
        
        requests = []
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
                if config.DELETE_KEYWORD.upper() not in summary.upper(): continue
                if '[D]' in summary: continue # Already deleted
                
                # Must be processed/synced
                if '[P]' not in summary and '[S]' not in summary: continue
                
                description = event.get('description', '')
                
                # Extract Ref
                import re
                ref_match = re.search(r'Ref:\s*([A-Z0-9]+)', description)
                if not ref_match:
                    logger.warning(f"DELETE request found but no Ref num: {summary}")
                    continue
                ref_num = ref_match.group(1)
                
                # Extract Owner
                owner_match = re.search(r'(?:Booked for )?User:\s*([^\s\n]+)', description)
                owner_email = owner_match.group(1).strip() if owner_match else None
                
                from core.entities import DeletionRequest # Local import or move top
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
            updates = {}
            new_summary = original_event.get('summary', '')
            if not new_summary.startswith("[P]"):
                new_summary = f"[P] {new_summary}"

            current_desc = original_event.get('description', '')

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

            updates['summary'] = new_summary
            
            body = {**original_event, **updates}
            
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
            if booked_end < orig_end:
                updated_event = {**original_event}
                updated_event['start'] = {'dateTime': booked_end.isoformat(), 'timeZone': 'Asia/Jerusalem'}
                self.client.service.events().update(
                    calendarId=self.calendar_id, eventId=original_event['id'], body=updated_event
                ).execute()
            elif booked_start > orig_start:
                updated_event = {**original_event}
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
