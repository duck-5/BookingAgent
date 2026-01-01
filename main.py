import time
import logging
from datetime import datetime
import config
from scheduler import Scheduler

def main():
    config.setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Service Started. Initializing Agents...")

    scheduler = Scheduler()
    scheduler.initialize_agents()

    while True:
        try:
            scheduler.run_cycle()
            
            now = datetime.now()
            if now.minute >= 58 or now.minute == 0:
                sleep_sec = 10
            else:
                mins = 59 - now.minute
                sleep_sec = max(10, min(300, mins * 60))
            
            time.sleep(sleep_sec)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Loop Crash: {e}", exc_info=True)
            time.sleep(30)

if __name__ == "__main__":
    main()