"""
Google Calendar integration utilities.

This module provides:
- GoogleCalendarClient: Low-level wrapper around Google Calendar API
- CalendarSync: High-level utility to sync booking system events to Google Calendar
"""

import os.path
import json
import logging
import time
from datetime import datetime, timedelta
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']

logger = logging.getLogger(__name__)


class GoogleCalendarClient:
    """Client for interacting with Google Calendar API."""
    
    def __init__(self, credentials_file='client_secret.json', token_file='token.json'):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.service = None
        self.creds = None

    def authenticate(self):
        """Authenticates the user and creates a service object."""
        if os.path.exists(self.token_file):
            self.creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        
        # If there are no (valid) credentials available, let the user log in.
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                try:
                    self.creds.refresh(Request())
                except Exception as e:
                    logger.error(f"Error refreshing token: {e}")
                    self.creds = None  # Force re-login
            
            if not self.creds:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(f"Credentials file not found: {self.credentials_file}")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, SCOPES)
                self.creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            with open(self.token_file, 'w') as token:
                token.write(self.creds.to_json())

        try:
            self.service = build('calendar', 'v3', credentials=self.creds)
            return True
        except Exception as e:
            logger.error(f"Failed to build service: {e}")
            return False

    def list_calendars(self):
        """Lists the names of the user's calendars."""
        if not self.service:
            if not self.authenticate():
                return None
        
        try:
            page_token = None
            calendar_list = []
            while True:
                events = self.service.calendarList().list(pageToken=page_token).execute()
                for calendar_list_entry in events['items']:
                    calendar_list.append(calendar_list_entry['summary'])
                page_token = events.get('nextPageToken')
                if not page_token:
                    break
            return calendar_list
        except Exception as e:
            logger.error(f"Error listing calendars: {e}")
            return None

    def get_or_create_calendar(self, calendar_name):
        """
        Finds a calendar by name. If it exists, returns its ID.
        If it doesn't exist, creates it and returns the new ID.
        """
        if not self.service:
            if not self.authenticate():
                return None

        try:
            # List all calendars
            page_token = None
            while True:
                calendar_list = self.service.calendarList().list(pageToken=page_token).execute()
                for calendar_list_entry in calendar_list['items']:
                    if calendar_list_entry['summary'] == calendar_name:
                        return calendar_list_entry['id']
                page_token = calendar_list.get('nextPageToken')
                if not page_token:
                    break
            
            # If not found, create it
            calendar = {
                'summary': calendar_name,
                'timeZone': 'Asia/Jerusalem'
            }
            created_calendar = self.service.calendars().insert(body=calendar).execute()
            logger.info(f"Created new calendar: {calendar_name} (ID: {created_calendar['id']})")
            return created_calendar['id']

        except Exception as e:
            logger.error(f"Error getting/creating calendar: {e}")
            return None

    def add_event(self, summary, start_time, end_time, description=None, location=None, calendar_id='primary', color_id=None):
        """
        Adds an event to the specified calendar.
        start_time and end_time should be in ISO format (e.g. '2023-10-27T10:00:00-07:00' or datetime objects)
        color_id: Google Calendar color ID string (e.g. '10' for green).
        """
        if not self.service:
            if not self.authenticate():
                return None

        event = {
            'summary': summary,
            'location': location,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': 'Asia/Jerusalem',
            },
            'end': {
                'dateTime': end_time,
                'timeZone': 'Asia/Jerusalem',
            },
        }
        if color_id:
            event['colorId'] = color_id

        try:
            event = self.service.events().insert(calendarId=calendar_id, body=event).execute()
            logger.info(f"Event created: {event.get('htmlLink')}")
            return event
        except Exception as e:
            logger.error(f"Error creating event: {e}")
            return None


class CalendarSync:
    """Syncs bookings from the server to Google Calendar."""
    
    def __init__(self):
        import config
        from clients.tau_client import TauClient
        from core.entities import UserCredentials
        
        self.config = config
        self.TauClient = TauClient
        self.users = []
        
        try:
            with open(config.CREDENTIALS_FILE, 'r') as f:
                raw_users = json.load(f)
                self.users = [UserCredentials(**u) for u in raw_users]
        except Exception as e:
            logger.error(f"Error loading credentials: {e}")

        self.gc = GoogleCalendarClient()
        if not self.gc.authenticate():
            logger.error("Failed to authenticate with Google Calendar")
            raise Exception("Google Calendar Auth Failed")
        
        self.calendar_id = self.gc.get_or_create_calendar(config.CALENDAR_NAME)
        if not self.calendar_id:
             raise Exception(f"Failed to get/create '{config.CALENDAR_NAME}' calendar")
             
        logger.info(f"Calendar Sync Ready. Target: {config.CALENDAR_NAME}")

    def _sync_user_internal(self, user_creds, s_str, e_str, progress_callback=None):
        """
        Internal method to sync a single user.
        Returns a dictionary of bookings found for this user.
        """
        email = user_creds.email
        logger.info(f"[SYNCER] Syncing user: {email}...")
        
        if progress_callback:
            progress_callback(email, "loading", "Fetching...")

        agent = self.TauClient(user_creds)
        if not agent.login():
            logger.warning(f"[SYNCER] Skipping {email} (Login Failed).")
            if progress_callback:
                progress_callback(email, "error", "Login Failed")
            return {}
        
        user_bookings = {}
        found_count = 0
        
        for sid in range(1, 6):
            time.sleep(0.2)
            url = f"{agent.BASE_URL}/my-calendar.php?dr=events&start={s_str}&end={e_str}&sid={sid}&rid=&gid="
            headers = {
                "X-Requested-With": "XMLHttpRequest", 
                "Referer": f"{agent.BASE_URL}/schedule.php"
            }
            
            try:
                resp = agent.session.get(url, headers=headers)
                if resp.status_code == 200:
                    try:
                        events = resp.json()
                    except json.JSONDecodeError:
                        if "Log In" in resp.text[:500]:
                            logger.error(f"  SID={sid}: Session expired. Re-login disabled.")
                            continue
                        else:
                            logger.error(f"  SID={sid}: JSON decode failed.")
                            continue
                    
                    for evt in events:
                        cls = evt.get('className', '')
                        if any(x in cls for x in ["mine", "coowner", "participating"]):
                            bid = evt.get('id')
                            if bid not in user_bookings:
                                user_bookings[bid] = {'event': evt, 'owner': email, 'agent': agent}
                                found_count += 1
                                # Report individual booking found (as detail item)
                                if progress_callback:
                                    # Use special prefix or just text
                                    title = evt.get('title', 'Unknown')
                                    start_t = evt.get('start', '')
                                    progress_callback(email, "details", f" - Found: {title} ({start_t})")
                else:
                    logger.warning(f"  SID={sid} returned status {resp.status_code}")
            except Exception as e:
                logger.error(f"  SID={sid}: {e}")
        
        if progress_callback:
            progress_callback(email, "success", f"Found {found_count} bookings")
            
        return user_bookings

    def sync_all_users(self, progress_callback=None):
        """
        Syncs server bookings to Google Calendar using parallel threads.
        progress_callback: func(email, status, message)
        """
        if not self.users:
            logger.warning("No users to sync.")
            return

        import concurrent.futures

        now = datetime.now()
        scan_days = getattr(self.config, 'CALENDAR_SCAN_DAYS', 7)
        start_date = now - timedelta(days=scan_days)
        end_date = now + timedelta(days=30)
        
        s_str = start_date.strftime("%Y-%m-%d")
        e_str = end_date.strftime("%Y-%m-%d")

        # 1. Fetch from Server in Parallel
        all_bookings = {}
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_user = {
                executor.submit(self._sync_user_internal, u, s_str, e_str, progress_callback): u 
                for u in self.users
            }
            
            for future in concurrent.futures.as_completed(future_to_user):
                user = future_to_user[future]
                try:
                    user_bookings = future.result()
                    all_bookings.update(user_bookings)
                except Exception as exc:
                    logger.error(f"[SYNCER] User {user.email} generated an exception: {exc}")
                    if progress_callback:
                        progress_callback(user.email, "error", "Sync Error")

        logger.info(f"[SYNCER] Retrieved {len(all_bookings)} bookings from University Server.")

        # 2. Fetch Existing Google Events
        google_events = []
        try:
            tmin = start_date.isoformat() + 'Z' 
            tmax = end_date.isoformat() + 'Z'
            events_result = self.gc.service.events().list(
                calendarId=self.calendar_id, 
                timeMin=tmin, timeMax=tmax, 
                singleEvents=True
            ).execute()
            google_events = events_result.get('items', [])
        except Exception as e:
            logger.error(f"Failed to list Google events: {e}")

        # 3. Sync — add missing events with [S] prefix, green color, username
        count = 0
        skipped = 0
        
        for bid, bdata in all_bookings.items():
            evt = bdata['event']
            owner = bdata['owner']
            title = evt['title'].strip()
            start = evt['start']  # "2026-02-11 08:00"
            end = evt['end']
            
            server_iso_start = start.replace(" ", "T")
            server_iso_end = end.replace(" ", "T")
            
            # Extract room name from title (e.g., "Room 015 Study" -> "Room 015")
            # Title format from server is typically: "Room XXX - Description" or "Room XXX Study"
            room_name = title.split()[0:2] if len(title.split()) >= 2 else [title]
            room_name = " ".join(room_name)  # "Room 015"
            
            # The booking ID from my-calendar.php IS the reference number
            ref_num = bid if bid else 'N/A'
            
            # For [S] events, format title: [S] Room (Title: ...) if remainder exists
            remainder = title.replace(room_name, "", 1).strip()
            # Remove leading whitespace/hyphens
            remainder = remainder.lstrip("-").strip()
            
            if remainder:
                synced_summary = f"[S] {room_name} (Title: {remainder})"
            else:
                synced_summary = f"[S] {room_name}"
            
            # Description matching [P] format: User, Room, Ref
            description = f"User: {owner}\nRoom: {room_name}\nRef: {ref_num}\nSynced from Booking System"
            
            # Enhanced duplicate detection: check time, room, and user
            exists = False
            for ge in google_events:
                ge_summary = ge.get('summary', '')
                ge_start = ge['start'].get('dateTime') or ge['start'].get('date')
                ge_desc = ge.get('description', '')
                
                if not ge_start:
                    continue
                
                # Must match start time
                if not ge_start.startswith(server_iso_start):
                    continue
                
                # Check if it's a synced or processed event with matching room and user
                if ge_summary.startswith("[S]") or ge_summary.startswith("[P]"):
                    # Check room and user in description (more reliable than title)
                    if f"Room: {room_name}" in ge_desc and f"User: {owner}" in ge_desc:
                        exists = True
                        break
                    # Also check title match as fallback
                    if room_name in ge_summary and title.split()[0] in ge_summary:
                        exists = True
                        break
            
            if exists:
                skipped += 1
                continue
            
            # Create event following [P] structure
            logger.info(f"[SYNCER] Adding: {synced_summary} @ {start}")
            self.gc.add_event(
                summary=synced_summary,
                start_time=f"{server_iso_start}:00",
                end_time=f"{server_iso_end}:00",
                description=description,
                location=room_name,  # Match [P] structure
                calendar_id=self.calendar_id,
                color_id=self.config.CalendarStatus.SYNCED
            )
            count += 1

        logger.info(f"[SYNCER] Sync Complete. Added {count}. Skipped {skipped}.")


if __name__ == "__main__":
    """Standalone sync script."""
    logging.basicConfig(level=logging.INFO)
    try:
        syncer = CalendarSync()
        syncer.sync_all_users()
    except Exception as e:
        logger.error(f"Sync failed: {e}")
