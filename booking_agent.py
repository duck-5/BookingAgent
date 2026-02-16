import requests
import json
import re
import logging
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)

class BookingResult(Enum):
    SUCCESS = auto()
    ROOM_TAKEN = auto()
    USER_LIMIT = auto()
    TOO_EARLY = auto()
    CLOSED = auto()
    ERROR = auto()

class BookingAgent:
    BASE_URL = "https://schedule.tau.ac.il/scilib/Web"
    LOGIN_URL = f"{BASE_URL}/index.php"
    BOOKING_URL = f"{BASE_URL}/api/reservation.php?action=create"
    UPDATE_URL = f"{BASE_URL}/api/reservation.php?action=update"

    def __init__(self, user_data):
        self.email = user_data['email']
        self.password = user_data['password']
        self.owner_id = user_data['owner_id']
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest", 
            "Origin": "https://schedule.tau.ac.il",
            "Referer": f"{self.BASE_URL}/schedule.php"
        })
        self.is_logged_in = False

    def login(self):
        try:
            payload = { "email": self.email, "password": self.password, "persistLogin": "on", "login": "submit" }
            # Allow redirects so we can check if we landed on index.php (fail) or schedule.php (success)
            resp = self.session.post(self.LOGIN_URL, data=payload, allow_redirects=True, timeout=20)
            
            # Check for specific success indicators
            # 1. 'login_token' cookie is usually set on success
            if "login_token" in self.session.cookies:
                self.is_logged_in = True
                return True
                
            # 2. URL check: invalid login usually stays on index.php or redirects back to it
            # Valid login redirects to schedule.php
            if "schedule.php" in resp.url:
                self.is_logged_in = True
                return True
            
            # If we are here, login failed (likely wrong password or account issue)
            # If we are here, login failed (likely wrong password or account issue)
            logger.warning(f"[AGENT] Login fail for {self.email} (invalid credential or session).")
            self.is_logged_in = False
            return False
            
        except Exception as e:
            logger.error(f"[AGENT] Login exception for {self.email}: {e}")
            self.is_logged_in = False
            return False

    def get_csrf_token(self):
        try:
            # Fast scan of text
            t = self.session.get(f"{self.BASE_URL}/schedule.php", timeout=20).text
            if m := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', t): return m.group(1)
            if m := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", t): return m.group(1)
            return None
        except Exception as e:
            logger.error(f"Failed to get CSRF token: {e}")
            return None

    def _send_reservation_request(self, url, data):
        if not self.is_logged_in: 
            if not self.login(): return BookingResult.ERROR, None
            
        csrf = self.get_csrf_token()
        if not csrf: return BookingResult.ERROR, None

        files = {
            'request': (None, json.dumps(data)),
            'CSRF_TOKEN': (None, csrf),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            r = self.session.post(url, files=files, timeout=30)
            try:
                res = r.json()
                if res.get("success") or res.get("data", {}).get("success"):
                    if err := res.get("data", {}).get("errors"):
                        s = json.dumps(err)
                        if "conflicting" in s: return BookingResult.ROOM_TAKEN, None
                        if "limited" in s: return BookingResult.USER_LIMIT, None
                        if "this far in the future" in s: return BookingResult.TOO_EARLY, None
                        if "not valid" in s: return BookingResult.CLOSED, None
                        logger.error(f"Booking Error (Success=True but has errors): {s}") # Log unexpected errors
                        return BookingResult.ERROR, None
                    
                    ref_num = res.get("data", {}).get("referenceNumber")
                    return BookingResult.SUCCESS, ref_num
                
                # --- FAILURE CASE ---
                s = json.dumps(res)
                if "conflicting" in s: return BookingResult.ROOM_TAKEN, None
                if "limited" in s: return BookingResult.USER_LIMIT, None
                if "this far in the future" in s: return BookingResult.TOO_EARLY, None
                if "not valid" in s: return BookingResult.CLOSED, None
                logger.error(f"Booking Failed: {s}") # Log the full response for debugging
                return BookingResult.ERROR, None
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON response")
                return BookingResult.ERROR, None 
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return BookingResult.ERROR, None

    def book_room(self, resource_id, start_utc, end_utc):
        data = {
            "reservation": {
                "ownerId": self.owner_id, "resourceIds": [resource_id],
                "title": "Study", "description": "AutoBook",
                "start": start_utc, "end": end_utc,
                "recurrence": { "type": "none", "interval": 1, "weekdays": None, "monthlyType": None, "weekOfMonth": None, "terminationDate": None, "repeatDates": [] },
                "startReminder": None, "endReminder": None, "inviteeIds": [], "coOwnerIds": [], "participantIds": [], "guestEmails": [], "participantEmails": [], "allowSelfJoin": False, "attachments": [], "requiresApproval": False, "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None, "attributeValues": [], "meetingLink": None, "displayColor": None
            },
            "updateScope": "full"
        }
        logger.debug(f"Booking payload: {json.dumps(data)}")
        return self._send_reservation_request(self.BOOKING_URL, data)

    def extend_booking(self, resource_id, ref_num, original_start_utc, new_end_utc):
        data = {
            "reservation": {
                "referenceNumber": ref_num,
                "ownerId": self.owner_id, "resourceIds": [resource_id],
                "title": "", "description": "", # Empty per user example
                "start": original_start_utc, "end": new_end_utc,
                "recurrence": { "type": "none", "interval": 1, "weekdays": None, "monthlyType": None, "weekOfMonth": None, "terminationDate": None, "repeatDates": [] },
                "startReminder": None, "endReminder": None, "inviteeIds": [], "coOwnerIds": [], "participantIds": [], "guestEmails": [], "participantEmails": [], "allowSelfJoin": False, "attachments": [], "requiresApproval": False, "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None, "attributeValues": [], "meetingLink": None, "displayColor": None
            },
            "updateScope": "full",
            "retryParameters": []
        }
        # Ideally, _send_reservation_request should handle the difference in success/failure checks if any.
        # But extend_booking previously had slightly less strict checks or different returns.
        # The refactored method unifies them. If 'referenceNumber' is missing in extend response, we might need adjustments.
        # However, checking the old code: it was returning ref_num (passed in) if missing from response.
        # The unified method returns what's in 'data.referenceNumber'.
        # We might need to handle that edge case if the API doesn't return refNum on update.
        
        result, res_ref_num = self._send_reservation_request(self.UPDATE_URL, data)
        if result == BookingResult.SUCCESS and not res_ref_num:
             return BookingResult.SUCCESS, ref_num
        return result, res_ref_num

    def delete_booking(self, ref_num: str) -> bool:
        """
        Deletes a booking by reference number.
        Returns True on success, False on failure.
        """
        logger.debug(f"[DELETE] Starting delete for {ref_num}, is_logged_in={self.is_logged_in}")
        
        if not self.is_logged_in:
            logger.debug(f"[DELETE] Agent {self.email} not logged in, attempting login...")
            login_result = self.login()
            logger.debug(f"[DELETE] Login result: {login_result}, is_logged_in now={self.is_logged_in}")
        
        # Get CSRF token (required for delete API like other operations)
        logger.debug(f"[DELETE] Fetching CSRF token...")
        csrf = self.get_csrf_token()
        if not csrf:
            logger.error(f"[DELETE] Failed to get CSRF token for delete operation")
            return False
        logger.debug(f"[DELETE] CSRF token obtained: {csrf[:10]}...")
        
        # Use api=delete endpoint (Confirmed by user)
        delete_url = f"{self.BASE_URL}/api/reservation.php?api=delete"
        
        # Payload (Confirmed by user: referenceNumber at top level)
        payload = {
            "referenceNumber": ref_num,
            "scope": "full",
            "reason": "",
            "browserTimezone": "Asia/Jerusalem"
        }
        
        try:
            # Update headers with CSRF token (Required for JSON payload)
            # Referer and Origin are also critical for permission checks
            headers = {
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.BASE_URL}/schedule.php",
                "Origin": "https://schedule.tau.ac.il",
                "Content-Type": "application/json"
            }
            
            # Send pure JSON payload, NOT multipart
            r = self.session.post(delete_url, json=payload, headers=headers, timeout=20)
            
            logger.debug(f"Delete API response status: {r.status_code}")
            logger.debug(f"Delete API response body: {r.text}")
            
            # Start of response handling
            if r.status_code != 200:
                logger.error(f"Delete failed with status {r.status_code}")
                try:
                    res = r.json()
                    if errors := res.get("errors"):
                        logger.error(f"Server errors: {errors}")
                except:
                    pass
                return False

            # Empty response with 200 status means success (seen in some tests)
            if not r.text.strip():
                logger.info(f"Successfully deleted booking {ref_num} (Empty 200 OK)")
                return True
            
            try:
                # Handle JSON response
                res = r.json()
                
                # Check for success in data.success first
                if res.get("data", {}).get("success"):
                    logger.info(f"Successfully deleted booking {ref_num}")
                    return True
                
                # Check for errors in response
                errors = []
                if "errors" in res:
                    err = res["errors"]
                    if isinstance(err, list):
                        errors = err
                    else:
                        errors = [str(err)]
                elif res.get("data", {}).get("errors"):
                    errors = res["data"]["errors"]
                
                # Log the specific error
                if errors:
                    error_str = ", ".join(errors)
                    logger.error(f"Failed to delete booking {ref_num}: {error_str}")
                else:
                    # If success is false but no errors found
                    logger.error(f"Failed to delete booking {ref_num}: Unknown error")
                    logger.debug(f"Full delete response: {res}")
                
                return False
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse delete response as JSON: {e}")
                # If we got 200 with unparseable response but it wasn't empty, valid JSON is expected
                return False
                
        except Exception as e:
            logger.error(f"Error deleting booking {ref_num}: {e}")
            return False

    def get_reference_number(self, booking_id: str) -> Optional[str]:
        """
        Fetches the reference number for a given booking ID by scraping the detail page.
        
        Args:
            booking_id: The booking ID from my-calendar.php (e.g., "12345")
            
        Returns:
            Reference number string (e.g., "BA3382D5") or None if not found
        """
        if not self.is_logged_in:
            self.login()
        
        try:
            # View booking detail page
            url = f"{self.BASE_URL}/view_entry.php?id={booking_id}"
            resp = self.session.get(url, timeout=20)
            
            if resp.status_code != 200:
                logger.error(f"Failed to fetch booking detail page for ID {booking_id}: {resp.status_code}")
                return None
            
            # Parse HTML to find reference number
            # Look for pattern: "Reference Number:" followed by the value
            match = re.search(r'Reference\s+Number[:\s]*([A-Z0-9]+)', resp.text, re.IGNORECASE)
            if match:
                ref_num = match.group(1)
                logger.debug(f"Found reference number {ref_num} for booking ID {booking_id}")
                return ref_num
            else:
                logger.warning(f"Reference number not found in booking detail page for ID {booking_id}")
                return None
                
        except Exception as e:
            logger.error(f"Error fetching reference number for booking {booking_id}: {e}")
            return None

    def get_user_bookings(self, start_date, end_date):
        """
        Fetches all bookings for this user from the server between start_date and end_date.
        Iterates through SIDs 1-5.
        Returns a list of dicts: { 'id': ..., 'start': ..., 'end': ..., 'room_id': ... }
        """
        if not self.is_logged_in: 
            if not self.login():
                # If login specifically failed (returned False), don't try to fetch
                logger.error(f"Cannot fetch bookings for {self.email} - Login failed")
                return []
        
        s_str = start_date.strftime("%Y-%m-%d")
        e_str = end_date.strftime("%Y-%m-%d")
        
        bookings = []
        
        for sid in range(1, 6):
            url = f"{self.BASE_URL}/my-calendar.php?dr=events&start={s_str}&end={e_str}&sid={sid}&rid=&gid="
            headers = {
                "X-Requested-With": "XMLHttpRequest", 
                "Referer": f"{self.BASE_URL}/schedule.php"
            }
            try:
                # Use a short timeout (5s) for these read-only checks to avoid hanging the main flow
                logger.debug(f"SID={sid} Requesting: {url}...")
                resp = self.session.get(url, headers=headers, timeout=5)
                logger.debug(f"SID={sid} Response: {resp.status_code}")
                
                # Check for HTML response (Login Page redirect despite 'is_logged_in' flag)
                if "DOCTYPE html" in resp.text or "<html" in resp.text:
                    if "Log In" in resp.text:
                        logger.error(f"Session expired for {self.email} during fetch (SID={sid}). Marking as logged out.")
                        self.is_logged_in = False
                        return [] # Stop fetching to avoid spamming errors
                
                if resp.status_code == 200:
                    try:
                        events = resp.json()
                        for evt in events:
                            cls = evt.get('className', '')
                            # Only return bookings the user OWNS
                            if "mine" in cls or "coowner" in cls:
                                bookings.append({
                                    "id": evt.get('id'),
                                    "title": evt.get('title'),
                                    "start": evt.get('start'),
                                    "end": evt.get('end'),
                                    "resourceId": evt.get('resourceId'),
                                    "raw": evt
                                })
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse JSON for {self.email} SID={sid}. Response might be HTML.")
                        # Log snippet for debug
                        logger.debug(f"Response snippet: {resp.text[:100]}")
                        
            except Exception as e:
                # Fail fast on timeout/connection error: if one fails, likely all will fail or server is dragging.
                # Don't wait 5s * 5 times.
                logger.warning(f"Error fetching bookings for {self.email} SID={sid}: {e}. Aborting fetch.")
                break
                
        return bookings
