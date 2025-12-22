import json
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class HistoryManager:
    FILE_PATH = "booking_history.json"

    def __init__(self):
        self.history = self._load()

    def _load(self):
        if not os.path.exists(self.FILE_PATH):
            return []
        try:
            with open(self.FILE_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            return []

    def _save(self):
        try:
            with open(self.FILE_PATH, 'w') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def is_booked(self, class_time):
        """Checks if a specific class time is already in history."""
        # Compare strings to avoid timezone headaches
        time_str = class_time.strftime("%Y-%m-%d %H:%M")
        for record in self.history:
            if record.get('class_time') == time_str:
                return True
        return False

    def add_booking(self, class_time, room_num, email):
        """Adds a successful booking to history."""
        record = {
            "class_time": class_time.strftime("%Y-%m-%d %H:%M"),
            "room": room_num,
            "user": email,
            "booked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.history.append(record)
        self._save()
        logger.info(f"Recorded booking for {record['class_time']} in history file.")

    def clean_history(self):
        """Removes bookings that have already passed."""
        now = datetime.now()
        original_count = len(self.history)
        
        valid_history = []
        for rec in self.history:
            try:
                # Parse the saved time
                dt = datetime.strptime(rec['class_time'], "%Y-%m-%d %H:%M")
                # Keep if the class is in the future
                if dt > now:
                    valid_history.append(rec)
            except ValueError:
                pass # Remove corrupt records
        
        self.history = valid_history
        if len(self.history) < original_count:
            self._save()
            logger.info(f"Cleaned {original_count - len(self.history)} old records from history.")

class TimeUtils:
    @staticmethod
    def get_utc_times(target_class_datetime):
        """Local Time -> UTC (Israel Winter -2)"""
        base_dt = target_class_datetime.replace(minute=0, second=0, microsecond=0)
        start_utc = base_dt - timedelta(hours=2)
        end_utc = start_utc + timedelta(hours=1)
        
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        return start_utc.strftime(fmt), end_utc.strftime(fmt)

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
    def get_next_opening_time(rule, now, history_manager, booking_delay_hours=1):
        """
        Scans rule hours and returns the nearest opening time that isn't already booked.
        """
        booking_weekday = rule['day_of_week']
        best_open_time = None
        best_class_time = None

        # Iterate through EACH hour in the rule (e.g. 17, 18)
        for h in range(rule['start_hour'], rule['end_hour']):
            booking_hour = h + booking_delay_hours
            days_offset = 0
            if booking_hour >= 24:
                booking_hour -= 24
                days_offset = 1

            # Find next occurrence
            days_ahead = (booking_weekday - now.weekday() + 7) % 7
            candidate_open = now.replace(hour=booking_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
            candidate_open += timedelta(days=days_offset)
            
            if candidate_open <= now:
                candidate_open += timedelta(days=7)

            # Calculate actual class time
            class_time = candidate_open - timedelta(hours=booking_delay_hours) + timedelta(days=7)

            # Check Bi-weekly
            if rule.get('biweekly'):
                for _ in range(4):
                    if TimeUtils.is_biweekly_match(class_time, rule['anchor_date']):
                        break
                    candidate_open += timedelta(days=7)
                    class_time += timedelta(days=7)

            # --- CHECK HISTORY ---
            # If this specific slot is already successfully booked, skip it!
            if history_manager.is_booked(class_time):
                # logger.debug(f"Skipping {class_time} (Already in history).")
                continue
            # ---------------------
            
            # Select the nearest valid one
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