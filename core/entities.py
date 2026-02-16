from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any

@dataclass
class Room:
    id: int
    name: str
    priority: str  # 'HIGH' or 'LOW'

@dataclass
class BookingRequest:
    event_id: str
    summary: str
    start_time: datetime
    end_time: datetime
    utc_start: str
    utc_end: str
    original_event: Dict[str, Any]
    opening_time: datetime

@dataclass
class UserCredentials:
    email: str
    password: str
    owner_id: str

@dataclass
class DeletionRequest:
    event_id: str
    summary: str
    ref_num: str
    original_event: Dict[str, Any]
    owner_email: Optional[str]
