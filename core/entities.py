from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from core.enums import ActionType, ActionStatus

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

@dataclass
class ActionLogDetails:
    action: ActionType
    status: ActionStatus
    user: Optional[str] = None
    room: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    ref_num: Optional[str] = None
    reason: Optional[str] = None
    message: Optional[str] = None

    def __str__(self):
        parts = [f"[{self.action.value}] [{self.status.value}]"]
        if self.message:
            parts.append(self.message)
        
        details = []
        if self.user: details.append(f"User: {self.user}")
        if self.room: details.append(f"Room: {self.room}")
        if self.start_time or self.end_time: 
            t = f"{self.start_time or '?'} - {self.end_time or '?'}"
            details.append(f"Time: {t}")
        if self.ref_num: details.append(f"Ref: {self.ref_num}")
        if self.reason: details.append(f"Reason: {self.reason}")
        
        if details:
            parts.append("(" + " | ".join(details) + ")")
            
        return " ".join(parts)
