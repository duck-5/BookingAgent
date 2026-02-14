"""
Test script to manually trigger delete request processing.
This helps debug the delete feature without waiting for the scheduler.
"""
import logging
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from scheduler import Scheduler

# Setup logging
config.setup_logging()
logger = logging.getLogger(__name__)

def main():
    logger.info("=== Manual Delete Request Test ===")
    
    # Initialize scheduler
    scheduler = Scheduler()
    
    # Initialize agents (needed for delete API calls)
    scheduler.initialize_agents()
    
    logger.info(f"Initialized {len(scheduler.agents)} agents")
    
    # Manually trigger delete processing
    logger.info("Triggering delete request processing...")
    scheduler._process_delete_requests()
    
    logger.info("=== Test Complete ===")

if __name__ == "__main__":
    main()
