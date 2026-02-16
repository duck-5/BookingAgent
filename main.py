import config
from scheduler import Scheduler
import threading
import uvicorn
from dashboard.app import app
import logging
import time

logger = logging.getLogger(__name__)

def run_dashboard():
    # Run Uvicorn in a thread. 
    # Use 0.0.0.0 so it's accessible externally if needed, but localhost is safer.
    # log_level="error" suppresses uvicorn's own access logs to keep console clean for our app logs.
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="error")

if __name__ == "__main__":
    config.setup_logging()
    
    logger.info("Starting Booking Agent...")
    
    # 1. Start Dashboard in background daemon thread
    # Daemon means it will die when the main thread dies.
    dash_thread = threading.Thread(target=run_dashboard, daemon=True)
    dash_thread.start()
    logger.info(">> Monitor Dashboard available at: http://localhost:8000 <<")
    
    # 2. Start Scheduler in main thread
    # Doing this in main thread allows catching KeyboardInterrupt easily.
    s = Scheduler()
    try:
        s.run() 
    except KeyboardInterrupt:
        logger.info("\nStopping Scheduler (Ctrl+C detected)...")
        # Signal stop to threads
        s._stop_event.set()
        
        # Wake up any sleeping threads so they can exit gracefully
        s._scan_wake_event.set()
        s._sync_wake_event.set()
        s._booking_in_progress.set() # Unblock sync if waiting
        
        # Give them a moment to finish current loop iteration
        time.sleep(1) 
        logger.info("Shutdown complete.")