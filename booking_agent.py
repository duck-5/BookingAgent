import requests
import json
import re
import logging

logger = logging.getLogger(__name__)

class BookingAgent:
    BASE_URL = "https://schedule.tau.ac.il/scilib/Web"
    LOGIN_URL = f"{BASE_URL}/index.php"
    BOOKING_URL = f"{BASE_URL}/api/reservation.php?action=create"

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
            resp = self.session.post(self.LOGIN_URL, data=payload, allow_redirects=True)
            if "login_token" in self.session.cookies or "PHPSESSID" in self.session.cookies:
                self.is_logged_in = True
                return True
            return False
        except: return False

    def get_csrf_token(self):
        try:
            resp = self.session.get(f"{self.BASE_URL}/schedule.php")
            html = resp.text
            if match := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', html): return match.group(1)
            if match := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", html): return match.group(1)
            return None
        except: return None

    def book_room(self, resource_id, start_utc, end_utc, retry=True):
        if not self.is_logged_in:
            if not self.login(): return "ERROR"

        csrf = self.get_csrf_token()
        if not csrf: return "ERROR"

        reservation_data = {
            "reservation": {
                "ownerId": self.owner_id, "resourceIds": [resource_id],
                "title": "Study", "description": "AutoBook",
                "start": start_utc, "end": end_utc, # Using UTC strings passed from scheduler
                "recurrence": { "type": "none", "interval": 1, "weekdays": None, "monthlyType": None, "weekOfMonth": None, "terminationDate": None, "repeatDates": [] },
                "startReminder": None, "endReminder": None, "inviteeIds": [], "coOwnerIds": [], "participantIds": [], "guestEmails": [], "participantEmails": [], "allowSelfJoin": False, "attachments": [], "requiresApproval": False, "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None, "attributeValues": [], "meetingLink": None, "displayColor": None
            },
            "retryParameters": [],
            "updateScope": "full"
        }

        files = {
            'request': (None, json.dumps(reservation_data)),
            'CSRF_TOKEN': (None, csrf),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        # Log UTC time for verification
        time_display = start_utc.split('T')[1][:8]
        logger.info(f"   [REQ] {self.email} -> Room {resource_id} @ {time_display} (UTC)")

        try:
            response = self.session.post(self.BOOKING_URL, files=files)
            
            try:
                res = response.json()
                if res.get("success") or res.get("data", {}).get("success"):
                    if errs := res.get("data", {}).get("errors"):
                        s = json.dumps(errs)
                        if "conflicting" in s: 
                            logger.info(f"   [RES] {self.email} <- ROOM_TAKEN")
                            return "ROOM_TAKEN"
                        if "limited" in s: 
                            logger.info(f"   [RES] {self.email} <- USER_LIMIT")
                            return "USER_LIMIT"
                        logger.info(f"   [RES] {self.email} <- LOGIC ERROR: {s}")
                        return "ERROR"
                    logger.info(f"   [RES] {self.email} <- SUCCESS")
                    return "SUCCESS"
                
                s = json.dumps(res)
                if "conflicting" in s: 
                    logger.info(f"   [RES] {self.email} <- ROOM_TAKEN")
                    return "ROOM_TAKEN"
                if "limited" in s: 
                    logger.info(f"   [RES] {self.email} <- USER_LIMIT")
                    return "USER_LIMIT"
                
                logger.info(f"   [RES] {self.email} <- ERROR (Server Msg)")
                return "ERROR"

            except json.JSONDecodeError:
                if retry:
                    logger.info(f"   [RETRY] Session expired. Relogging...")
                    if self.login():
                        return self.book_room(resource_id, start_utc, end_utc, retry=False)
                return "ERROR"
        except Exception as e:
            logger.error(f"   [RES] {self.email} <- NETWORK ERROR: {e}")
            return "ERROR"