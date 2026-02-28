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
        self.logs = [] # Changed from deque to list for easier searching/filtering
        self.actions = {} 
        self.events = [] 

        # --- Enhanced Data ---
        self.agents: List[Dict] = [] # [{email, status: 'Ready'|'Failed', last_active}]
        
        # Operations: Scanner, Booker, Syncer
        # Each operation now has an 'active_run' and a 'history' of past runs
        self.operations = {
            "Scanner": {"active_run": None, "history": []},
            "Booker":  {"active_run": None, "history": []},
            "Syncer":  {"active_run": None, "history": []}
        }
        
        self.timers = {
            "scan": {"target": None, "label": "Next Scan"},
            "sync": {"target": None, "label": "Next Sync"},
            "booking": {"target": None, "label": "Next Booking"}
        }

    def update_status(self, key: str, value: Any):
        with self._lock:
            self.status[key] = value

    def update_agents(self, agents_list: List[Dict]):
        """Replace entire agent list."""
        with self._lock:
            self.agents = agents_list

    def set_timer(self, key: str, target_time: Optional[datetime], label: str = ""):
        with self._lock:
            if target_time:
                self.timers[key] = {
                    "target": target_time.isoformat(),
                    "label": label
                }
            else:
                 # Remove or set to null? Setting to null keeps the slot
                 self.timers[key] = {"target": None, "label": label}

    def op_start(self, op_type: str, status_msg: str = "Running..."):
        """Start a new operation run. Moves the previous active_run to history."""
        with self._lock:
            if op_type in self.operations:
                op = self.operations[op_type]
                # Archive previous run if exists
                if op["active_run"]:
                    # Keep at most 20 history items
                    op["history"].insert(0, op["active_run"])
                    if len(op["history"]) > 20:
                        op["history"].pop()
                
                # Start new run
                run_id = f"run-{int(time.time() * 1000)}"
                op["active_run"] = {
                    "id": run_id,
                    "status": status_msg,
                    "startTime": datetime.now().strftime("%H:%M:%S"),
                    "items": []
                }

    def op_update(self, op_type: str, key: str, value: Any):
        """Update a specific key in the active operation run (e.g. status)."""
        with self._lock:
            if op_type in self.operations and self.operations[op_type]["active_run"]:
                self.operations[op_type]["active_run"][key] = value

    def op_end(self, op_type: str, status_msg: str = "Idle"):
        """End the active operation section."""
        with self._lock:
            if op_type in self.operations and self.operations[op_type]["active_run"]:
                self.operations[op_type]["active_run"]["status"] = status_msg
                self.operations[op_type]["active_run"]["endTime"] = datetime.now().strftime("%H:%M:%S")

    def clear_history(self, mode: str):
        """Clears operation history. modes: 'all', 'empty'."""
        with self._lock:
            for op_type, op in self.operations.items():
                if mode == "all":
                    op["history"] = []
                elif mode == "empty":
                    op["history"] = [run for run in op["history"] if len(run.get("items", [])) > 0]

    _id_counter = 0

    def op_add_item(self, op_type: str, text: str, status: str = "pending", agent: str = None, details: str = None) -> str:
        """Add a trackable item to the active operation run. Returns item ID."""
        with self._lock:
            self._id_counter += 1
            item_id = f"{int(time.time() * 1000)}-{self._id_counter}"
            if op_type in self.operations and self.operations[op_type]["active_run"]:
                self.operations[op_type]["active_run"]["items"].append({
                    "id": item_id,
                    "text": text,
                    "status": status, # pending, success, error, loading
                    "agent": agent,   # Grouping key
                    "details": details, # Tooltip details
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                })
        return item_id

    def op_update_item(self, op_type: str, item_id: str, new_status: str, extra_text: str = None, details: str = None, agent: str = None):
        """Update status, text, agent, or details of a trackable item in the active run."""
        with self._lock:
            if op_type in self.operations and self.operations[op_type]["active_run"]:
                for item in self.operations[op_type]["active_run"]["items"]:
                    if item["id"] == item_id:
                        if new_status: item["status"] = new_status
                        if extra_text: item["text"] = extra_text 
                        if details: item["details"] = details
                        if agent: item["agent"] = agent
                        break

    def log(self, level: str, source: str, message: str):
        """
        Add a log entry. 
        Level: INFO, WARNING, ERROR, SUCCESS, PENDING
        """
        entry = {
            "id": int(time.time() * 1000),
            "time": datetime.now().strftime("%H:%M:%S"),
            "timestamp": time.time(),
            "level": level,
            "source": source,
            "message": message
        }
        with self._lock:
            self.logs.insert(0, entry) # Newest first
            self._cleanup_logs()
            
    def _cleanup_logs(self):
        """Remove logs older than 15 minutes."""
        # 15 mins = 900 seconds
        cutoff = time.time() - 900
        # self.logs is sorted desc by time. iterate from end.
        while self.logs and self.logs[-1]["timestamp"] < cutoff:
            self.logs.pop()

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
                "timers": self.timers.copy()
            }

# Global Instance
state = MonitorState()
