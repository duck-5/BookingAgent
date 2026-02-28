import requests
import json
import re
import logging
from typing import Optional, Dict, Any, Tuple
from core.enums import BookingResult, ActionType, ActionStatus
from core.entities import UserCredentials, ActionLogDetails

logger = logging.getLogger(__name__)

class TauClient:
    BASE_URL = "https://schedule.tau.ac.il/scilib/Web"
    LOGIN_URL = f"{BASE_URL}/index.php"
    BOOKING_URL = f"{BASE_URL}/api/reservation.php?action=create"
    UPDATE_URL = f"{BASE_URL}/api/reservation.php?action=update"

    def __init__(self, credentials: UserCredentials):
        self.email = credentials.email
        self.password = credentials.password
        self.owner_id = credentials.owner_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest", 
            "Origin": "https://schedule.tau.ac.il",
            "Referer": f"{self.BASE_URL}/schedule.php"
        })
        self.is_logged_in = False

    def login(self) -> bool:
        try:
            self.session.cookies.clear() # Ensure fresh session
            payload = { "email": self.email, "password": self.password, "persistLogin": "on", "login": "submit" }
            # Allow redirects so we can check if we landed on index.php (fail) or schedule.php (success)
            resp = self.session.post(self.LOGIN_URL, data=payload, allow_redirects=True, timeout=20)
            
            # Check for specific success indicators
            if "login_token" in self.session.cookies:
                self.is_logged_in = True
                return True
                
            if "schedule.php" in resp.url:
                self.is_logged_in = True
                return True
            
            logger.warning(f"[AGENT] Login fail for {self.email} (invalid credential or session).")
            self.is_logged_in = False
            return False
            
        except Exception as e:
            logger.error(f"[AGENT] Login exception for {self.email}: {e}")
            self.is_logged_in = False
            return False

    def get_csrf_token(self) -> Optional[str]:
        try:
            resp = self.session.get(f"{self.BASE_URL}/schedule.php", timeout=20)
            t = resp.text
            
            if m := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', t): return m.group(1)
            if m := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", t): return m.group(1)
            
            # Debugging
            logger.warning(f"[AGENT] CSRF extraction failed. URL: {resp.url}")
            logger.debug(f"[AGENT] HTML Peek: {t[:500]}...")
            if "Log In" in t or "login" in resp.url:
                logger.warning("[AGENT] Session appears invalid (redirected to login).")
                self.is_logged_in = False
                
            return None
        except Exception as e:
            logger.error(f"Failed to get CSRF token: {e}")
            return None

    def _send_reservation_request(self, url: str, data: Dict[str, Any], _retry: bool = False) -> Tuple[BookingResult, Optional[str]]:
        if not self.is_logged_in: 
            if not self.login(): return BookingResult.ERROR, "Login Failed"
            
        csrf = self.get_csrf_token()
        if not csrf: return BookingResult.ERROR, "CSRF Token Missing"

        files = {
            'request': (None, json.dumps(data)),
            'CSRF_TOKEN': (None, csrf),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            r = self.session.post(url, files=files, timeout=30)
            
            # Handle Empty but Successful Response (200 OK with no content)
            # Strip: all C0 controls, BOM, and ZWSP
            garbage_bytes = bytes(range(33)) + b'\xef\xbb\xbf\xe2\x80\x8b'
            if r.status_code == 200 and not r.content.strip(garbage_bytes):
                return BookingResult.SUCCESS, "Success (Empty Response)"

            # Check for HTML Error Page (200 OK but HTML content)
            # This typically means the session expired and the server returned a login/error page
            if r.content.strip().startswith(b'<') or b'<!DOCTYPE' in r.content:
                if not _retry:
                    logger.warning(f"[AGENT] Session expired (HTML response). Re-logging in...")
                    self.is_logged_in = False
                    if self.login():
                        return self._send_reservation_request(url, data, _retry=True)
                
                # Already retried or re-login failed
                logger.error(f"Server returned HTML Error Page. URL: {r.url}")
                if m := re.search(rb'<title>(.*?)</title>', r.content, re.IGNORECASE | re.DOTALL):
                    logger.error(f"  Page Title: {m.group(1).decode('utf-8', errors='replace').strip()}")
                snippet = r.content.strip()[:200]
                logger.error(f"  Snippet: {repr(snippet)}...")
                return BookingResult.ERROR, "Server Error: HTML Response"

            try:
                res = r.json()
                logger.debug(f"API Response: {json.dumps(res)}")
                if res is None: res = {} # Handle 'null' response
                
                if res.get("success") or res.get("data", {}).get("success"):
                    if err := res.get("data", {}).get("errors"):
                        s = json.dumps(err) # e.g. ["User restricted"]
                        # Clean up formatting if it's a list
                        if isinstance(err, list): s = ", ".join(map(str, err))
                        
                        s_lower = s.lower()
                        if "conflicting" in s_lower or "unavailable" in s_lower: return BookingResult.ROOM_TAKEN, f"Room Conflict: {s}"
                        if "limited" in s_lower: return BookingResult.USER_LIMIT, f"User Limit: {s}"
                        if "this far in the future" in s_lower: return BookingResult.TOO_EARLY, "Too Early"
                        if "not last longer than 3 hours" in s_lower: return BookingResult.TOO_LONG, "3-Hour Limit Exceeded"
                        if "not valid" in s_lower: return BookingResult.CLOSED, "Booking Closed"
                        return BookingResult.ERROR, f"API Error: {s}"
                    
                    ref_num = res.get("data", {}).get("referenceNumber")
                    return BookingResult.SUCCESS, ref_num
                
                # --- FAILURE CASE ---
                # Try to extract readable error
                error_msg = "Unknown Error"
                if "errors" in res:
                    err = res["errors"]
                    error_msg = ", ".join(map(str, err)) if isinstance(err, list) else str(err)
                elif res.get("data", {}).get("errors"):
                    err = res["data"]["errors"]
                    error_msg = ", ".join(map(str, err)) if isinstance(err, list) else str(err)
                else:
                    error_msg = json.dumps(res)

                s = error_msg.lower()
                if "conflicting" in s or "unavailable" in s: return BookingResult.ROOM_TAKEN, f"Room Conflict: {error_msg}"
                if "limited" in s: return BookingResult.USER_LIMIT, f"User Limit: {error_msg}"
                if "this far in the future" in s: return BookingResult.TOO_EARLY, "Too Early"
                if "not last longer than 3 hours" in s: return BookingResult.TOO_LONG, "3-Hour Limit Exceeded"
                if "not valid" in s: return BookingResult.CLOSED, "Booking Closed"
                
                logger.error(f"Booking Failed: {error_msg}")
                return BookingResult.ERROR, f"Server Error: {error_msg}"
            except json.JSONDecodeError:
                # Fallback: check stripped content again just in case
                if not r.content.strip(garbage_bytes):
                     return BookingResult.SUCCESS, "Success (Empty Response Fallback)"
                
                # Enhanced Logging for invisible chars
                logger.error(f"Failed to decode JSON response. Status: {r.status_code}")
                logger.error(f"  Encoding: {r.encoding}")
                logger.error(f"  Content (bytes): {repr(r.content[:200])}...")
                logger.error(f"  Content (hex): {r.content[:200].hex()}")
                return BookingResult.ERROR, "Invalid JSON Response"
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return BookingResult.ERROR, f"Request Failed: {str(e)}"

    
    def update_booking(self, ref_num: str, resource_id: int, start_utc: str, end_utc: str) -> Tuple[bool, str]:
        """
        Updates an existing booking to extend its end time.
        """
        log = ActionLogDetails(action=ActionType.UPDATE, status=ActionStatus.INFO, user=self.email, room=str(resource_id), ref_num=ref_num, end_time=end_utc, message="Extending booking")
        logger.debug(str(log))
        
        if not self.is_logged_in:
            if not self.login(): return False, "Login Failed"
        
        csrf = self.get_csrf_token()
        if not csrf: return False, "CSRF Missing"
        
        # Payload for update needs full object
        data = {
            "reservation": {
                "userId": self.owner_id, "ownerId": self.owner_id,
                "resourceIds": [resource_id],
                "title": "Study", "description": "AutoBook",
                "start": start_utc, "end": end_utc, 
                "referenceNumber": ref_num,
                "recurrence": { "type": "none", "interval": 1, "weekdays": None, "monthlyType": None, "weekOfMonth": None, "terminationDate": None, "repeatDates": [] },
                "startReminder": None, "endReminder": None, "inviteeIds": [], "coOwnerIds": [], "participantIds": [], "guestEmails": [], "participantEmails": [], "allowSelfJoin": False, "attachments": [], "requiresApproval": False, "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None, "attributeValues": [], "meetingLink": None, "displayColor": None
            },
            "updateScope": "full" 
        }
        
        res, msg = self._send_reservation_request(self.UPDATE_URL, data)
        return res == BookingResult.SUCCESS, msg

    def book_room(self, resource_id: int, start_utc: str, end_utc: str) -> Tuple[BookingResult, Optional[str]]:
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
        log = ActionLogDetails(action=ActionType.BOOK, status=ActionStatus.INFO, user=self.email, room=str(resource_id), start_time=start_utc, end_time=end_utc, message=f"Payload: {json.dumps(data)}")
        logger.debug(str(log))
        return self._send_reservation_request(self.BOOKING_URL, data)

    def delete_booking(self, ref_num: str) -> Tuple[bool, str]:
        log = ActionLogDetails(action=ActionType.DELETE, status=ActionStatus.INFO, user=self.email, ref_num=ref_num, message=f"Starting delete, is_logged_in={self.is_logged_in}")
        logger.debug(str(log))
        
        if not self.is_logged_in:
            if not self.login(): return False, "Login Failed"
        
        csrf = self.get_csrf_token()
        if not csrf: return False, "CSRF Missing"
        
        delete_url = f"{self.BASE_URL}/api/reservation.php?api=delete"
        
        payload = {
            "referenceNumber": ref_num,
            "scope": "full",
            "reason": "",
            "browserTimezone": "Asia/Jerusalem"
        }
        
        try:
            headers = {
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.BASE_URL}/schedule.php",
                "Origin": "https://schedule.tau.ac.il",
                "Content-Type": "application/json"
            }
            
            r = self.session.post(delete_url, json=payload, headers=headers, timeout=20)
            
            if r.status_code != 200:
                return False, f"HTTP {r.status_code}"

            if not r.text.strip():
                return True, "Success"
            
            try:
                res = r.json()
                logger.debug(f"API Response: {json.dumps(res)}")
                if res.get("data", {}).get("success"): return True, "Success"
                
                errors = []
                if "errors" in res:
                    err = res["errors"]
                    errors = err if isinstance(err, list) else [str(err)]
                elif res.get("data", {}).get("errors"):
                    errors = res["data"]["errors"]
                
                if errors:
                    msg = "; ".join([str(e) for e in errors])
                    log_err = ActionLogDetails(action=ActionType.DELETE, status=ActionStatus.FAILED, user=self.email, ref_num=ref_num, reason=msg)
                    logger.error(str(log_err))
                    return False, msg
                return False, "Unknown Error"
                    
            except json.JSONDecodeError:
                return False, "Invalid JSON Response"
                
        except Exception as e:
            log_err = ActionLogDetails(action=ActionType.DELETE, status=ActionStatus.FAILED, user=self.email, ref_num=ref_num, reason=str(e))
            logger.error(str(log_err))
            return False, str(e)

    def get_user_bookings(self, start_date, end_date):
        if not self.is_logged_in: 
            if not self.login(): return []
        
        s_str = start_date.strftime("%Y-%m-%d")
        e_str = end_date.strftime("%Y-%m-%d")
        
        bookings = []
        
        for sid in range(1, 6):
            url = f"{self.BASE_URL}/my-calendar.php?dr=events&start={s_str}&end={e_str}&sid={sid}&rid=&gid="
            headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{self.BASE_URL}/schedule.php"}
            try:
                resp = self.session.get(url, headers=headers, timeout=5)
                
                if "DOCTYPE html" in resp.text:
                    if "Log In" in resp.text:
                        self.is_logged_in = False
                        return []
                
                if resp.status_code == 200:
                    try:
                        events = resp.json()
                        for evt in events:
                            cls = evt.get('className', '')
                            if "mine" in cls or "coowner" in cls:
                                bookings.append(evt)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                break
        return bookings
