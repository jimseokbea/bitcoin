import os
import sys
import time
import json
import signal
import logging
import pytz
from logging.handlers import RotatingFileHandler

# --- 상수 정의 ---
LOCK_FILE = "bot.lock"
STATE_FILE = "bot_state.json"
KST = pytz.timezone("Asia/Seoul")
RUNNING = True  # 루프 제어용 전역 변수

# --- 1. Logger Setup (중복 방지) ---
def setup_logger():
    logger = logging.getLogger("TradingBot")
    if logger.handlers:
        return logger # 이미 설정됨

    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler("bot_final.log", maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger

LOGGER = setup_logger()

# --- 2. Request Limiter (API 과속 방지) ---
class RequestLimiter:
    def __init__(self, min_interval=0.15): # 초당 8회 이하 권장
        self.min_interval = min_interval
        self.last_ts = 0

    def wait(self):
        now = time.time()
        diff = now - self.last_ts
        if diff < self.min_interval:
            time.sleep(self.min_interval - diff)
        self.last_ts = time.time()

LIMITER = RequestLimiter()

# --- 3. Lock File (중복 실행 방지) ---
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = f.read().strip()
            LOGGER.error(f"❌ 이미 봇이 실행 중입니다. (PID: {old_pid})")
            return False
        except:
            pass # 파일 읽기 에러 시 덮어쓰기 시도

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True

def release_lock():
    if os.path.exists(LOCK_FILE):
        try:
             os.remove(LOCK_FILE)
        except:
             pass

# --- 4. State Persistence (상태 저장) ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            LOGGER.error(f"State Load Error: {e}")
    return {}

def save_state(data):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        LOGGER.error(f"State Save Error: {e}")

# --- 5. Signal Handling (안전 종료) ---
def handle_sigint(sig, frame):
    global RUNNING
    LOGGER.warning("🛑 종료 신호 감지! 안전하게 종료합니다...")
    RUNNING = False

def install_signal_handlers():
    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)
