import requests
import json
import re
import logging
from datetime import datetime

# --- Constants ---
BASE_URL = "https://schedule.tau.ac.il/scilib/Web"
LOGIN_URL = f"{BASE_URL}/index.php"
BOOKING_URL = f"{BASE_URL}/api/reservation.php?action=create"

logger = logging.getLogger(__name__)

class BookingAgent:
    def __init__(self, user_data):
        self.email = user_data['email']
        self.password = user_data['password']
        self.owner_id = user_data['owner_id']
        self.session = requests.Session()
        
        # Standard Headers
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest", 
            "Origin": "https://schedule.tau.ac.il",
            "Referer": f"{BASE_URL}/schedule.php"
        })
        logger.debug(f"[{self.email}] Agent initialized.")

    def _save_error_response(self, content):
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"error_{self.email}_{timestamp}.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[{self.email}] HTML Error content saved to {filename}")
        except Exception:
            pass

    def login(self):
        logger.info(f"[{self.email}] Attempting login to {LOGIN_URL}...")
        payload = {
            "email": self.email, "password": self.password,
            "persistLogin": "on", "login": "submit"
        }
        try:
            resp = self.session.post(LOGIN_URL, data=payload)
            
            if "login_token" in self.session.cookies:
                logger.info(f"[{self.email}] Login successful.")
                return True
            
            logger.warning(f"[{self.email}] Login failed. Cookies: {self.session.cookies.get_dict()}")
            return False
        except Exception as e:
            logger.error(f"[{self.email}] Login network error: {e}")
            return False

    def get_csrf_token(self):
        try:
            response = self.session.get(f"{BASE_URL}/schedule.php")
            
            # Try input field
            match = re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', response.text)
            if match: return match.group(1)
            
            # Try JS variable
            match = re.search(r"CSRF_TOKEN\s*=\s*'([^']+)'", response.text)
            if match: return match.group(1)
            
            logger.error(f"[{self.email}] CSRF Token could not be found in page HTML.")
            return None
        except Exception as e:
            logger.error(f"[{self.email}] CSRF Fetch Exception: {e}")
            return None

    def book_room(self, resource_id, start_utc, end_utc):
        """
        Returns:
            "SUCCESS": Booking confirmed.
            "ROOM_TAKEN": Conflicting reservations error.
            "USER_LIMIT": User limit reached error.
            "ERROR": General failure.
        """
        csrf_token = self.get_csrf_token()
        if not csrf_token:
            return "ERROR"

        reservation_data = {
            "reservation": {
                "referenceNumber": None,
                "ownerId": self.owner_id,
                "resourceIds": [resource_id],
                "accessories": [],
                "title": "Study",
                "description": "123",
                "start": start_utc,
                "end": end_utc,
                "recurrence": {
                    "type": "none", "interval": 1, "weekdays": None,
                    "monthlyType": None, "weekOfMonth": None,
                    "terminationDate": None, "repeatDates": []
                },
                "startReminder": None, "endReminder": None,
                "inviteeIds": [], "coOwnerIds": [], "participantIds": [],
                "guestEmails": [], "participantEmails": [],
                "allowSelfJoin": False, "attachments": [], "requiresApproval": False,
                "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None,
                "attributeValues": [], "meetingLink": None, "displayColor": None
            },
            "retryParameters": [],
            "updateScope": "full"
        }

        # --- LOGGING ROOM ATTEMPT ---
        # resource_id 23 is room 13, etc.
        room_number = resource_id - 10
        logger.info(f"[{self.email}] Attempting: Room {room_number} (ID:{resource_id}) | UTC: {start_utc}")

        files = {
            'request': (None, json.dumps(reservation_data)),
            'CSRF_TOKEN': (None, csrf_token),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            response = self.session.post(BOOKING_URL, files=files)
            
            try:
                res_json = response.json()
                
                # 1. Check Success
                if res_json.get("success") or res_json.get("data", {}).get("success"):
                    # Check for hidden errors inside success message
                    if errors := res_json.get("data", {}).get("errors"):
                        err_str = json.dumps(errors)
                        logger.warning(f"[{self.email}] Logic Failure: {err_str}")
                        
                        if "conflicting reservations" in err_str:
                            return "ROOM_TAKEN"
                        if "limited to 1.00 reservations" in err_str:
                            return "USER_LIMIT"
                        return "ERROR"
                    
                    ref = res_json.get("data", {}).get("referenceNumber", "Unknown")
                    logger.info(f"[{self.email}] SUCCESS! Room {room_number} Booked. Ref: {ref}")
                    return "SUCCESS"
                
                # 2. Check Specific Failure Messages
                full_resp_str = json.dumps(res_json)
                
                if "conflicting reservations" in full_resp_str:
                    logger.warning(f"[{self.email}] Room {room_number} is TAKEN (Conflicting Reservation).")
                    return "ROOM_TAKEN"
                
                if "limited to 1.00 reservations" in full_resp_str:
                    logger.warning(f"[{self.email}] User hit DAILY LIMIT.")
                    return "USER_LIMIT"

                # Log generic failure
                msg = res_json.get("msg") or res_json.get("message") or "Unknown Server Error"
                logger.info(f"[{self.email}] Failed: {msg}")
                return "ERROR"

            except json.JSONDecodeError:
                logger.error(f"[{self.email}] Response was not JSON. Saving debug file.")
                self._save_error_response(response.text)
                return "ERROR"
        except Exception as e:
            logger.error(f"[{self.email}] Booking Request Exception: {e}")
            return "ERROR"