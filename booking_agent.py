import requests
import json
import re
import logging
from enum import Enum, auto

logger = logging.getLogger(__name__)

class BookingResult(Enum):
    SUCCESS = auto()
    ROOM_TAKEN = auto()
    USER_LIMIT = auto()
    TOO_EARLY = auto()
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
            self.session.post(self.LOGIN_URL, data=payload, allow_redirects=True)
            if "login_token" in self.session.cookies or "PHPSESSID" in self.session.cookies:
                self.is_logged_in = True
                return True
            return False
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    def get_csrf_token(self):
        try:
            # Fast scan of text
            t = self.session.get(f"{self.BASE_URL}/schedule.php").text
            if m := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', t): return m.group(1)
            if m := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", t): return m.group(1)
            return None
        except Exception as e:
            logger.error(f"Failed to get CSRF token: {e}")
            return None

    def _send_reservation_request(self, url, data):
        if not self.is_logged_in: self.login()
        csrf = self.get_csrf_token()
        if not csrf: return BookingResult.ERROR, None

        files = {
            'request': (None, json.dumps(data)),
            'CSRF_TOKEN': (None, csrf),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            r = self.session.post(url, files=files)
            try:
                res = r.json()
                if res.get("success") or res.get("data", {}).get("success"):
                    if err := res.get("data", {}).get("errors"):
                        s = json.dumps(err)
                        if "conflicting" in s: return BookingResult.ROOM_TAKEN, None
                        if "limited" in s: return BookingResult.USER_LIMIT, None
                        if "this far in the future" in s: return BookingResult.TOO_EARLY, None
                        logger.error(f"Booking Error (Success=True but has errors): {s}") # Log unexpected errors
                        return BookingResult.ERROR, None
                    
                    ref_num = res.get("data", {}).get("referenceNumber")
                    return BookingResult.SUCCESS, ref_num
                
                # --- FAILURE CASE ---
                s = json.dumps(res)
                if "conflicting" in s: return BookingResult.ROOM_TAKEN, None
                if "limited" in s: return BookingResult.USER_LIMIT, None
                if "this far in the future" in s: return BookingResult.TOO_EARLY, None
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