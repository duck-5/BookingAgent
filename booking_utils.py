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
                dt = datetime.strptime(rec['class_time'], "%Y-%m-%d %H:%M")
                if dt > now:
                    valid_history.append(rec)
            except ValueError:
                pass 
        
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
        Scans rule hours.
        1. Checks for MISSED slots (past opening time, future class time, not booked).
        2. Checks for FUTURE slots.
        Returns: (opening_time, target_class_time, is_missed_retry)
        """
        booking_weekday = rule['day_of_week']
        best_event = None # (open_time, class_time, is_missed)

        for h in range(rule['start_hour'], rule['end_hour']):
            booking_hour = h + booking_delay_hours
            days_offset = 0
            if booking_hour >= 24:
                booking_hour -= 24
                days_offset = 1

            # Logic to find the RECENT past or NEAR future
            days_ahead = (booking_weekday - now.weekday() + 7) % 7
            
            # Start checking from 1 week ago (to catch missed slots)
            base_candidate = now.replace(hour=booking_hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead) - timedelta(days=7) 
            
            # Check last week, this week, next week
            for w in range(3): 
                candidate_open = base_candidate + timedelta(days=w*7) + timedelta(days=days_offset)
                class_time = candidate_open - timedelta(hours=booking_delay_hours) + timedelta(days=7)

                # Skip if class is in the past
                if class_time <= now:
                    continue

                # Check Bi-weekly
                if rule.get('biweekly') and not TimeUtils.is_biweekly_match(class_time, rule['anchor_date']):
                    continue

                # Check History
                if history_manager.is_booked(class_time):
                    continue

                # Categorize
                is_missed = False
                if candidate_open < now:
                    is_missed = True
                
                current_event = (candidate_open, class_time, is_missed)
                
                if best_event is None:
                    best_event = current_event
                else:
                    best_open, _, best_is_missed = best_event
                    
                    # Prioritize Missed slots over Future slots
                    if is_missed and not best_is_missed:
                        best_event = current_event
                    # If both are same type, pick the earlier one
                    elif (is_missed == best_is_missed) and (candidate_open < best_open):
                        best_event = current_event
                
        return best_event

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