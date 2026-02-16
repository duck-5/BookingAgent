import logging
from .state import state

class DashboardHandler(logging.Handler):
    """
    Custom logging handler that forwards logs to the MonitorState.
    """
    def emit(self, record):
        try:
            msg = self.format(record)
            # Map Python levels to Dashboard levels
            level = record.levelname
            if level == "WARNING": level = "WARN"
            
            # Simple source extraction (e.g., [SCHEDULER])
            source = "SYSTEM"
            if "[SCHEDULER]" in msg: source = "SCHEDULER"
            elif "[SYNCER]" in msg: source = "SYNCER"
            elif "[AGENT]" in msg: source = "AGENT"
            
            # Clean message (remove prefix if desired, but keep for now)
            
            state.log(level, source, msg)
        except Exception:
            self.handleError(record)
