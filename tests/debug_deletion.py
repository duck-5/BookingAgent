
import unittest
from unittest.mock import MagicMock
import sys
import os
import re

# Setup path
sys.path.insert(0, os.getcwd())

from services.calendar_manager import CalendarManager
from core.enums import CalendarStatus
import config

class TestDeletionDebug(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.calendar_manager = CalendarManager(self.mock_client)
        self.calendar_manager.calendar_id = "cal_id"
        config.DELETE_KEYWORD = "DELETE"

    def test_case_insensitive_delete(self):
        """Test if 'Delete' (mixed case) is removed."""
        original_event = {
            'id': '1',
            'summary': '[P] Delete Booking: Room 1',
            'description': 'Ref: 123',
            'start': {'dateTime': '2023-01-01T10:00:00Z'},
            'end': {'dateTime': '2023-01-01T11:00:00Z'}
        }
        
        self.calendar_manager.update_event_status('1', CalendarStatus.DELETED, original_event)
        
        call_args = self.mock_client.service.events().update.call_args
        body = call_args[1]['body']
        
        print(f"\n[Mixed Case] Input: {original_event['summary']}")
        print(f"[Mixed Case] Output: {body['summary']}")
        
        # This will fail with current code if it's case sensitive
        self.assertNotIn('Delete', body['summary'])

    def test_happy_path(self):
        """Test standard uppercase DELETE."""
        original_event = {
            'id': '2',
            'summary': '[P] DELETE Booking: Room 1',
            'description': 'Ref: 123',
        }
        
        self.calendar_manager.update_event_status('2', CalendarStatus.DELETED, original_event)
        
        call_args = self.mock_client.service.events().update.call_args
        body = call_args[1]['body']
        
        print(f"\n[Happy Path] Input: {original_event['summary']}")
        print(f"[Happy Path] Output: {body['summary']}")
        
        self.assertEqual(body['summary'], '[DELETED] Booking: Room 1')

if __name__ == '__main__':
    unittest.main()
