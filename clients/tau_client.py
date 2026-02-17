import requests
import json
import re
import logging
from typing import Optional, Dict, Any, Tuple
from core.enums import BookingResult
from core.entities import UserCredentials

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
            
            logger.warning(f"[CLIENT] Login fail for {self.email} (invalid credential or session).")
            self.is_logged_in = False
            return False
            
        except Exception as e:
            logger.error(f"[CLIENT] Login exception for {self.email}: {e}")
            self.is_logged_in = False
            return False

    def get_csrf_token(self) -> Optional[str]:
        try:
            t = self.session.get(f"{self.BASE_URL}/schedule.php", timeout=20).text
            if m := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', t): return m.group(1)
            if m := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", t): return m.group(1)
            return None
        except Exception as e:
            logger.error(f"Failed to get CSRF token: {e}")
            return None

    def _send_reservation_request(self, url: str, data: Dict[str, Any]) -> Tuple[BookingResult, Optional[str]]:
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
                        logger.error(f"Booking Error (Success=True but has errors): {s}")
                        return BookingResult.ERROR, None
                    
                    ref_num = res.get("data", {}).get("referenceNumber")
                    return BookingResult.SUCCESS, ref_num
                
                # --- FAILURE CASE ---
                s = json.dumps(res)
                if "conflicting" in s: return BookingResult.ROOM_TAKEN, None
                if "limited" in s: return BookingResult.USER_LIMIT, None
                if "this far in the future" in s: return BookingResult.TOO_EARLY, None
                if "not valid" in s: return BookingResult.CLOSED, None
                logger.error(f"Booking Failed: {s}")
                return BookingResult.ERROR, None
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON response")
                return BookingResult.ERROR, None 
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return BookingResult.ERROR, None

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
        logger.debug(f"Booking payload: {json.dumps(data)}")
        return self._send_reservation_request(self.BOOKING_URL, data)

    def delete_booking(self, ref_num: str) -> Tuple[bool, str]:
        logger.debug(f"[DELETE] Starting delete for {ref_num}, is_logged_in={self.is_logged_in}")
        
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
                if res.get("data", {}).get("success"): return True, "Success"
                
                errors = []
                if "errors" in res:
                    err = res["errors"]
                    errors = err if isinstance(err, list) else [str(err)]
                elif res.get("data", {}).get("errors"):
                    errors = res["data"]["errors"]
                
                if errors:
                    msg = "; ".join([str(e) for e in errors])
                    logger.error(f"Failed to delete {ref_num}: {msg}")
                    return False, msg
                return False, "Unknown Error"
                    
            except json.JSONDecodeError:
                return False, "Invalid JSON Response"
                
        except Exception as e:
            logger.error(f"Error deleting booking {ref_num}: {e}")
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
