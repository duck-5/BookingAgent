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
            
            # Simple source extraction
            source = "SYSTEM"
            if "[SCANNER]" in msg: source = "SCANNER"
            elif "[BOOKER]" in msg: source = "BOOKER"
            elif "[SYNCER]" in msg: source = "SYNCER"
            elif "[SYSTEM]" in msg: source = "SYSTEM"
            elif "[AGENT]" in msg: source = "AGENT"
            
            # Clean message (remove prefix if desired, but keep for now)
            
            state.log(level, source, msg)
        except Exception:
            self.handleError(record)
