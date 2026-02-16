import threading
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Any, Optional

class MonitorState:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(MonitorState, cls).__new__(cls)
                    cls._instance._init_data()
        return cls._instance

    def _init_data(self):
        self.status = {
            "scheduler": "Initializing",
            "last_scan": None,
            "last_sync": None,
            "next_action": "Unknown",
            "active_tasks": 0
        }
        self.logs = deque(maxlen=200) # Increased log retention
        self.actions = {} 
        self.events = [] 

        # --- Enhanced Data ---
        self.agents: List[Dict] = [] # [{email, status: 'Ready'|'Failed', last_active}]
        
        # Operations: DELETE, SYNC, CREATE
        # active: bool, status: str, items: [{id, text, status: 'pending'|'success'|'error', details}]
        self.operations = {
            "DELETE": {"active": False, "status": "Idle", "items": []},
            "SYNC":   {"active": False, "status": "Idle", "items": []},
            "CREATE": {"active": False, "status": "Idle", "items": []}
        }
        
        self.timer = {"target": None, "label": None} # {target: timestamp_iso, label: "Booking Room X"}

    def update_status(self, key: str, value: Any):
        with self._lock:
            self.status[key] = value

    def update_agents(self, agents_list: List[Dict]):
        """Replace entire agent list."""
        with self._lock:
            self.agents = agents_list

    def set_timer(self, target_time: Optional[datetime], label: str = ""):
        with self._lock:
            self.timer = {
                "target": target_time.isoformat() if target_time else None,
                "label": label
            }

    def op_start(self, op_type: str, status_msg: str = "Running..."):
        """Start an operation section."""
        with self._lock:
            if op_type in self.operations:
                self.operations[op_type]["active"] = True
                self.operations[op_type]["status"] = status_msg
                self.operations[op_type]["items"] = [] # Clear previous items on new run? Yes.

    def op_update(self, op_type: str, key: str, value: Any):
        """Update a specific key in an operation state (e.g. status)."""
        with self._lock:
            if op_type in self.operations:
                self.operations[op_type][key] = value

    def op_end(self, op_type: str, status_msg: str = "Idle"):
        """End an operation section."""
        with self._lock:
            if op_type in self.operations:
                self.operations[op_type]["active"] = False
                self.operations[op_type]["status"] = status_msg

    def op_add_item(self, op_type: str, text: str, status: str = "pending") -> str:
        """Add a trackable item to an operation. Returns item ID."""
        item_id = str(int(time.time() * 10000)) # Simple ID
        with self._lock:
            if op_type in self.operations:
                self.operations[op_type]["items"].append({
                    "id": item_id,
                    "text": text,
                    "status": status, # pending, success, error, loading
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                })
        return item_id

    def op_update_item(self, op_type: str, item_id: str, new_status: str, extra_text: str = None):
        """Update status of a trackable item."""
        with self._lock:
            if op_type in self.operations:
                for item in self.operations[op_type]["items"]:
                    if item["id"] == item_id:
                        item["status"] = new_status
                        if extra_text:
                            item["text"] += f" {extra_text}"
                        break

    def log(self, level: str, source: str, message: str):
        """
        Add a log entry. 
        Level: INFO, WARNING, ERROR, SUCCESS, PENDING
        """
        entry = {
            "id": int(time.time() * 1000),
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "source": source,
            "message": message
        }
        with self._lock:
            self.logs.appendleft(entry) # Newest first

    def register_action(self, name: str, callback):
        with self._lock:
            self.actions[name] = callback

    def trigger_action(self, name: str):
        with self._lock:
            cb = self.actions.get(name)
        
        if cb:
            # Run in separate thread to not block API
            threading.Thread(target=cb, daemon=True).start()
            self.log("INFO", "DASHBOARD", f"Manual action triggered: {name}")
            return True
        return False

    def get_snapshot(self):
        with self._lock:
            return {
                "status": self.status.copy(),
                "logs": list(self.logs),
                "events": list(self.events),
                
                # Enhanced Data
                "agents": list(self.agents),
                "operations": self.operations.copy(), # Deep copy might be safer but shallow copy of dict structure is ok for serialization
                "timer": self.timer.copy()
            }

# Global Instance
state = MonitorState()
