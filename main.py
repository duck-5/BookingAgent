import config
from scheduler import Scheduler

if __name__ == "__main__":
    config.setup_logging()
    s = Scheduler()
    s.run() # Scheduler now handles its own infinite loop and sleeping