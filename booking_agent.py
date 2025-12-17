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
            logger.debug(f"[{self.email}] Login POST status: {resp.status_code}")
            
            if "login_token" in self.session.cookies:
                logger.info(f"[{self.email}] Login successful. Cookies acquired.")
                return True
            
            logger.warning(f"[{self.email}] Login failed. Cookies: {self.session.cookies.get_dict()}")
            return False
        except Exception as e:
            logger.error(f"[{self.email}] Login network error: {e}")
            return False

    def get_csrf_token(self):
        try:
            logger.debug(f"[{self.email}] Fetching schedule page for CSRF...")
            response = self.session.get(f"{BASE_URL}/schedule.php")
            
            # Try input field
            match = re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', response.text)
            if match: 
                logger.debug(f"[{self.email}] CSRF found (Method 1).")
                return match.group(1)
            
            # Try JS variable
            match = re.search(r"CSRF_TOKEN\s*=\s*'([^']+)'", response.text)
            if match: 
                logger.debug(f"[{self.email}] CSRF found (Method 2).")
                return match.group(1)
            
            logger.error(f"[{self.email}] CSRF Token could not be found in page HTML.")
            return None
        except Exception as e:
            logger.error(f"[{self.email}] CSRF Fetch Exception: {e}")
            return None

    def book_room(self, resource_id, start_utc, end_utc):
        """Returns True on success, False on failure."""
        csrf_token = self.get_csrf_token()
        if not csrf_token:
            return False

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

        # Debug log for the specific payload being sent
        logger.debug(f"[{self.email}] Payload for Room {resource_id-10}: {json.dumps(reservation_data)}")

        files = {
            'request': (None, json.dumps(reservation_data)),
            'CSRF_TOKEN': (None, csrf_token),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            response = self.session.post(BOOKING_URL, files=files)
            logger.debug(f"[{self.email}] POST Response Code: {response.status_code}")
            
            try:
                res_json = response.json()
                
                # Check for successful booking
                if res_json.get("success") or res_json.get("data", {}).get("success"):
                    # Check for hidden errors inside success message
                    if errors := res_json.get("data", {}).get("errors"):
                        logger.warning(f"[{self.email}] Logic Failure: {errors}")
                        return False 
                    
                    ref = res_json.get("data", {}).get("referenceNumber", "Unknown")
                    logger.info(f"[{self.email}] SUCCESS! Room {resource_id-10} Booked. Ref: {ref}")
                    return True
                
                # Log failure reason
                msg = res_json.get("msg") or res_json.get("message") or "Unknown Server Error"
                logger.info(f"[{self.email}] Failed: {msg}")
                return False

            except json.JSONDecodeError:
                logger.error(f"[{self.email}] Response was not JSON. Saving debug file.")
                self._save_error_response(response.text)
                return False
        except Exception as e:
            logger.error(f"[{self.email}] Booking Request Exception: {e}")
            return False