import logging
import colorlog
import signal
import sys

# Global Running Flag
RUNNING = True

def signal_handler(sig, frame):
    global RUNNING
    print("\n🛑 Shutting down (Signal Received)...")
    RUNNING = False

def install_signal_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def setup_logger(name="BinanceBot"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    if logger.handlers:
        return logger
        
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    
    formatter = colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

LOGGER = setup_logger()
