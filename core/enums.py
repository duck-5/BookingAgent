from enum import Enum, auto

class BookingResult(Enum):
    SUCCESS = auto()
    ROOM_TAKEN = auto()
    USER_LIMIT = auto()
    TOO_EARLY = auto()
    CLOSED = auto()
    ERROR = auto()
    TOO_LONG = auto()

class CalendarStatus:
    PROCESSING = '5'   # Yellow
    SUCCESS = '10'     # Green
    SYNCED = '10'      # Green (same as SUCCESS)
    FAILURE = '11'     # Red
    DELETED = '8'      # Gray

class ActionType(Enum):
    BOOK = "BOOK"
    SYNC = "SYNC"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    SCAN = "SCAN"

class ActionStatus(Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    WARNING = "WARNING"
    INFO = "INFO"
