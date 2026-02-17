from clients.google_calendar import GoogleCalendarClient
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)

def main():
    client = GoogleCalendarClient()
    if not client.authenticate():
        return

    calendar_name = "Library Bookings"
    calendar_id = client.get_or_create_calendar(calendar_name)
    
    # Create "Booking: Test"
    now = datetime.now()
    start = (now + timedelta(days=2)).replace(hour=14, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=2)
    
    print(f"Creating 'Booking: Test Session' for {start}")
    
    event = client.add_event(
        summary="Booking: Test Session", 
        start_time=start.isoformat(), 
        end_time=end.isoformat(), 
        description="Please book this.", 
        calendar_id=calendar_id
    )
    print(f"Created: {event.get('htmlLink')}")

if __name__ == "__main__":
    main()
