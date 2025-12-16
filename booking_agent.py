import requests
import json
import re
import logging
from datetime import datetime

# --- Configuration ---
# EXACT URL from your working script
BASE_URL = "https://schedule.tau.ac.il/scilib/Web"
LOGIN_URL = f"{BASE_URL}/index.php"
BOOKING_URL = f"{BASE_URL}/api/reservation.php?action=create"

# Logger setup
logger = logging.getLogger(__name__)

class BookingAgent:
    def __init__(self, user_data):
        self.email = user_data['email']
        self.password = user_data['password']
        self.owner_id = user_data['owner_id']
        self.session = requests.Session()
        
        # EXACT Headers from your working script
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest", 
            "Origin": "https://schedule.tau.ac.il",
            "Referer": f"{BASE_URL}/schedule.php"
        })

    def _save_error_response(self, content):
        """Saves response content to a file for debugging."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"error_{self.email}_{timestamp}.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            logger.error(f"Saved error response to: {filename}")
        except Exception as e:
            logger.error(f"Failed to save error file: {e}")

    def login(self):
        logger.info(f"[{self.email}] Logging in...")
        payload = {
            "email": self.email,
            "password": self.password,
            "persistLogin": "on",
            "login": "submit"
        }
        
        try:
            self.session.post(LOGIN_URL, data=payload)
            if "login_token" in self.session.cookies:
                logger.info(f"[{self.email}] Login successful.")
                return True
            else:
                logger.warning(f"[{self.email}] Login failed. Cookies not found.")
                return False
        except Exception as e:
            logger.error(f"[{self.email}] Login error: {e}")
            return False

    def get_csrf_token(self):
        try:
            response = self.session.get(f"{BASE_URL}/schedule.php")
            
            match = re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', response.text)
            if match: return match.group(1)
            
            match = re.search(r"CSRF_TOKEN\s*=\s*'([^']+)'", response.text)
            if match: return match.group(1)
            
            logger.error(f"[{self.email}] CSRF Token not found.")
            return None
        except Exception as e:
            logger.error(f"[{self.email}] CSRF fetch error: {e}")
            return None

    def book_room(self, resource_id, start_utc, end_utc):
        csrf_token = self.get_csrf_token()
        if not csrf_token:
            return

        # 1. Prepare JSON Payload (Exact structure from working script)
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

        json_payload = json.dumps(reservation_data)

        # 2. Prepare Multipart Data
        multipart_data = {
            'request': (None, json_payload),
            'CSRF_TOKEN': (None, csrf_token),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        # 3. Log Request Details
        logger.info(f"[{self.email}] Sending POST to {BOOKING_URL}")

        try:
            # 4. Send Request
            response = self.session.post(BOOKING_URL, files=multipart_data)

            logger.info(f"Status Code: {response.status_code}")
            
            # --- LOGGING FULL RESPONSE ---
            logger.info(f"[{self.email}] FULL SERVER RESPONSE:\n{response.text}\n")
            # -----------------------------

            # 5. Parse Response
            try:
                res_json = response.json()
                if res_json.get("success") or res_json.get("data", {}).get("success"):
                    if errors:=res_json.get("data").get("errors"):
                        logger.error(f"[{self.email}] Booking Error: {errors}")
                    else:
                        logger.info(f"[{self.email}] SUCCESS!")
                
                else:
                    logger.warning(f"[{self.email}] Booking Failed (Server Logic).")
            except json.JSONDecodeError:
                logger.error(f"[{self.email}] Response was not JSON.")
                self._save_error_response(response.text)

        except Exception as e:
            logger.error(f"[{self.email}] Network Request failed: {e}")