import sys
import os
import logging

# Add project root to path (assuming running from root)
sys.path.append(os.getcwd())


from utils.google_calendar import GoogleCalendarClient
import config
from datetime import datetime, timedelta

def debug_calendar():
    print("--- DEBUG CALENDAR START ---")
    
    # Init client
    try:
        client = GoogleCalendarClient(
            credentials_file=config.GOOGLE_CALENDAR_CREDENTIALS,
            token_file=config.GOOGLE_CALENDAR_TOKEN
        )
    except Exception as e:
        print(f"FAILED TO INIT CLIENT: {e}")
        return

    # Get Calendar ID
    cal_name = config.CALENDAR_NAME
    print(f"Looking for Calendar: '{cal_name}'")
    
    try:
        cal_id = client.get_or_create_calendar(cal_name)
        print(f"Found Calendar ID: {cal_id}")
    except Exception as e:
        print(f"FAILED TO GET CALENDAR ID: {e}")
        return

    # List Events
    now = datetime.utcnow()
    end = now + timedelta(days=8)
    tmin = now.isoformat() + 'Z'
    tmax = end.isoformat() + 'Z'
    
    print(f"Scanning range: {tmin} to {tmax}")

    try:
        events_result = client.service.events().list(
            calendarId=cal_id,
            timeMin=tmin,
            timeMax=tmax,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        items = events_result.get('items', [])
        print(f"Total Events Found: {len(items)}")
        
        for event in items:
            start = event['start'].get('dateTime') or event['start'].get('date')
            summary = event.get('summary', '(No Title)')
            print(f" - [{start}] {summary}")
            
    except Exception as e:
        print(f"FAILED TO LIST EVENTS: {e}")

    print("--- DEBUG CALENDAR END ---")

if __name__ == "__main__":
    debug_calendar()
