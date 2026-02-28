import logging
import sys
import colorama

colorama.init()

class ColoredFormatter(logging.Formatter):
    """
    Formatter that adds colors to log levels and structure to messages.
    Format: [TIME] [LEVEL] [ENTITY] Message
    """
    
    # Colors
    GREY = "\x1b[38;20m"
    GREEN = "\x1b[32;20m"
    YELLOW = "\x1b[33;20m"
    RED = "\x1b[31;20m"
    BOLD_RED = "\x1b[31;1m"
    RESET = "\x1b[0m"
    CYAN = "\x1b[36;20m"

    FORMAT_STR = "%(asctime)s [%(levelname)s] %(message)s"

    FORMATS = {
        logging.DEBUG: GREY + FORMAT_STR + RESET,
        logging.INFO: GREEN + FORMAT_STR + RESET,
        logging.WARNING: YELLOW + FORMAT_STR + RESET,
        logging.ERROR: RED + FORMAT_STR + RESET,
        logging.CRITICAL: BOLD_RED + FORMAT_STR + RESET
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%H:%M:%S')
        return formatter.format(record)

def setup_colored_logging(log_file="system.log"):
    """
    Sets up the root logger with colored console output and file output.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Clear existing handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Console Handler (Colored)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColoredFormatter())
    console_handler.setLevel(logging.DEBUG) # Show everything on console for now
    root_logger.addHandler(console_handler)

    # File Handler (Standard)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)

    return root_logger
