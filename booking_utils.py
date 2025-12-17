import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class TimeUtils:
    @staticmethod
    def get_utc_times(target_class_datetime):
        """
        Input: Datetime of the class (e.g. 11:00)
        Output: Start/End UTC strings (e.g. 09:00Z)
        
        Logic: Direct conversion. Israel Winter Time is UTC+2.
        11:00 Local -> 09:00 UTC.
        """
        # Ensure clean start on the hour
        base_dt = target_class_datetime.replace(minute=0, second=0, microsecond=0)
        
        # Israel Winter Time offset is UTC+2
        # Subtract 2 hours from Local time to get UTC
        start_utc = base_dt - timedelta(hours=2)
        end_utc = start_utc + timedelta(hours=1)
        
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        s_str, e_str = start_utc.strftime(fmt), end_utc.strftime(fmt)
        return s_str, e_str

    @staticmethod
    def is_biweekly_match(target_class_date, anchor_str):
        try:
            anchor = datetime.strptime(anchor_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0)
            target = target_class_date.replace(hour=0, minute=0, second=0)
            delta = target - anchor
            weeks_diff = delta.days // 7
            return delta.days >= 0 and (weeks_diff % 2 == 0)
        except Exception:
            return False

    @staticmethod
    def get_next_opening_time(rule, now, booking_delay_hours=1):
        """
        Scans all hours in the rule (e.g., 17, 18 for a 17-19 rule)
        and returns the absolute nearest future opening time.
        Returns: (opening_time, target_class_time)
        """
        booking_weekday = rule['day_of_week']
        best_open_time = None
        best_class_time = None

        # Iterate through EACH hour in the range (e.g., 17, 18)
        # because each hour is a separate booking event
        for h in range(rule['start_hour'], rule['end_hour']):
            
            # Booking opens 'booking_delay_hours' AFTER the class starts
            booking_hour = h + booking_delay_hours
            
            days_offset = 0
            if booking_hour >= 24:
                booking_hour -= 24
                days_offset = 1

            # Find next occurrence of booking weekday
            days_ahead = (booking_weekday - now.weekday() + 7) % 7
            
            candidate_open = now.replace(hour=booking_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
            candidate_open += timedelta(days=days_offset)
            
            # If the candidate opening time has passed, look at next week
            if candidate_open <= now:
                candidate_open += timedelta(days=7)

            # Calculate actual class time: (Booking Time - Delay + 7 Days)
            # We book 1 week in advance relative to the opening time
            class_time = candidate_open - timedelta(hours=booking_delay_hours) + timedelta(days=7)

            # Handle bi-weekly check
            if rule.get('biweekly'):
                # Check up to 4 weeks ahead to find a valid week
                for _ in range(4):
                    if TimeUtils.is_biweekly_match(class_time, rule['anchor_date']):
                        break
                    candidate_open += timedelta(days=7)
                    class_time += timedelta(days=7)
            
            # We want the earliest valid opening time that hasn't passed (handled by loop in main)
            # or simply the nearest one to "now"
            if best_open_time is None or candidate_open < best_open_time:
                best_open_time = candidate_open
                best_class_time = class_time
                
        return best_open_time, best_class_time

class ConfigLoader:
    @staticmethod
    def load_data():
        try:
            with open("credentials.json", 'r') as f: users = json.load(f)
            with open("bookings.json", 'r') as f: rules = json.load(f)
            return users, rules
        except Exception as e:
            logger.error(f"Error loading files: {e}")
            return [], []