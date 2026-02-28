import sys
import os
import time

# Add project root to path (assuming running from root)
sys.path.append(os.getcwd())

import config
from services.agent_manager import AgentManager
from core.enums import BookingResult
from clients.tau_client import TauClient

def debug_extend():
    print("--- DEBUG EXTEND START ---")
    
    manager = AgentManager(config.CREDENTIALS_FILE)
    agents = manager.get_all_agents()
    
    if not agents:
        print("No agents loaded.")
        return
        
    # Get the first logged in agent
    agent = None
    for a in agents:
        if a.is_logged_in:
            agent = a
            break
            
    if not agent:
        print("No agents logged in, trying to force login on first agent...")
        agent = agents[0]
        if not agent.login():
            print("Failed to login first agent.")
            return
            
    print(f"Using Agent: {agent.email}")
    
    # Target: Monday 3/2/26, 8:00-10:00 (Israel Time)
    start_utc = "2026-03-02T06:00:00.000Z"
    end_utc_hour1 = "2026-03-02T07:00:00.000Z"
    end_utc_hour2 = "2026-03-02T08:00:00.000Z"
    
    # Room 016 (Resource ID 26)
    room_id = 26
    
    from datetime import datetime
    test_date = datetime(2026, 3, 2)
    
    print("\n[0] Cleaning up any existing test bookings for the day across all agents...")
    active_agents = [a for a in agents if a.is_logged_in]
    for a in active_agents:
        bookings = a.get_user_bookings(test_date, test_date)
        for b in bookings:
            print(f"Deleting booking {b['id']} for {a.email}...")
            a.delete_booking(b['id'])
            
    print(f"\n[1] Attempting to book first hour... Room {room_id}, {start_utc} - {end_utc_hour1}")
    
    agent = active_agents[0]
    res, msg = agent.book_room(room_id, start_utc, end_utc_hour1)
    
    if res != BookingResult.SUCCESS:
        print(f"Failed to book with {agent.email}. Result: {res}, Message: {msg}")
        # Try next agent
        for next_agent in active_agents[1:]:
            print(f"Trying with {next_agent.email}...")
            agent = next_agent
            res, msg = agent.book_room(room_id, start_utc, end_utc_hour1)
            if res == BookingResult.SUCCESS:
                break
                
        if res != BookingResult.SUCCESS:
            print("All agents failed to book.")
            return
    
    print(f"Successfully booked first hour! Reference Number: {msg}")
    
    # Reference number is returned in msg
    ref_num = msg
    
    print("\nWaiting a couple seconds before trying to extend...")
    time.sleep(2)
    
    print(f"\n[2] Attempting to extend booking to second hour... {start_utc} - {end_utc_hour2}")
    
    # We will do a raw request to debug the HTML error response
    import requests
    import json
    
    url = f"https://schedule.tau.ac.il/scilib/Web/api/reservation.php?action=update"
    
    data = {"reservation": {"userId": agent.owner_id, "ownerId": agent.owner_id, "resourceIds": [room_id], "title": "Study", "description": "AutoBook", "start": start_utc, "end": end_utc_hour2, "referenceNumber": ref_num, "recurrence": {"type": "none", "interval": 1, "weekdays": None, "monthlyType": None, "weekOfMonth": None, "terminationDate": None, "repeatDates": []}, "startReminder": None, "endReminder": None, "inviteeIds": [], "coOwnerIds": [], "participantIds": [], "guestEmails": [], "participantEmails": [], "allowSelfJoin": False, "attachments": [], "requiresApproval": False, "checkinDate": None, "checkoutDate": None, "termsAcceptedDate": None, "attributeValues": [], "meetingLink": None, "displayColor": None}, "updateScope": "full"}
    
    files = {
        'request': (None, json.dumps(data)),
        'CSRF_TOKEN': (None, agent.get_csrf_token()),
        'BROWSER_TIMEZONE': (None, 'Asia/Jerusalem')
    }
    
    print("Sending raw POST request for update API with full payload...")
    r = agent.session.post(url, files=files, timeout=30)
    
    print(f"Status Code: {r.status_code}")
    print(f"Content-Type: {r.headers.get('Content-Type')}")
    
    if "json" in r.headers.get("Content-Type", ""):
        try:
            res = r.json()
            print("Successfully parsed JSON Response:")
            print(json.dumps(res, indent=2))
        except Exception as e:
            print("Failed to parse JSON response:", e)
    else:
        with open("update_debug_playground/error_response.html", "wb") as f:
            f.write(r.content)
        print("Response saved to update_debug_playground/error_response.html")
    
    print("\n--- DEBUG EXTEND END ---")

if __name__ == "__main__":
    debug_extend()
