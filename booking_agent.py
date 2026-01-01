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
            self.session.post(self.LOGIN_URL, data=payload, allow_redirects=True)
            if "login_token" in self.session.cookies or "PHPSESSID" in self.session.cookies:
                self.is_logged_in = True
                return True
            return False
        except: return False

    def get_csrf_token(self):
        try:
            # Fast scan of text
            t = self.session.get(f"{self.BASE_URL}/schedule.php").text
            if m := re.search(r'name="CSRF_TOKEN"\s+value="([^"]+)"', t): return m.group(1)
            if m := re.search(r"CSRF_TOKEN\s*=\s*['\"]([^'\"]+)['\"]", t): return m.group(1)
            return None
        except: return None

    def book_room(self, resource_id, start_utc, end_utc):
        if not self.is_logged_in: self.login()
        csrf = self.get_csrf_token()
        if not csrf: return "ERROR"

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

        files = {
            'request': (None, json.dumps(data)),
            'CSRF_TOKEN': (None, csrf),
            'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
        }

        try:
            r = self.session.post(self.BOOKING_URL, files=files)
            try:
                res = r.json()
                if res.get("success") or res.get("data", {}).get("success"):
                    if err := res.get("data", {}).get("errors"):
                        s = json.dumps(err)
                        if "conflicting" in s: return "ROOM_TAKEN"
                        if "limited" in s: return "USER_LIMIT"
                        return "ERROR"
                    return "SUCCESS"
                
                s = json.dumps(res)
                if "conflicting" in s: return "ROOM_TAKEN"
                if "limited" in s: return "USER_LIMIT"
                return "ERROR"
            except:
                return "ERROR" # HTML response = likely error
        except:
            return "ERROR"