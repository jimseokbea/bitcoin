import sqlite3
import os
from datetime import datetime
from .utils import get_logger

logger = get_logger()

class TradeDB:
    def __init__(self, db_name="monster_records.db"):
        # Ensure correct path (relative to main.py usually)
        self.db_path = os.path.join(os.getcwd(), db_name)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # [Safety] WAL Mode for Concurrency
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.cursor = self.conn.cursor()
        self.create_table()

    def create_table(self):
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    symbol TEXT,
                    side TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    pnl REAL,
                    strategy_type TEXT
                )
            ''')
            self.conn.commit()
        except Exception as e:
            logger.error(f"DB Init Error: {e}")

    def log_trade(self, symbol, side, entry, exit_price, pnl, strategy):
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self.cursor.execute('''
                INSERT INTO trades (timestamp, symbol, side, entry_price, exit_price, pnl, strategy_type)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (timestamp, symbol, side, entry, exit_price, pnl, strategy))
            self.conn.commit()
            logger.info(f"💾 Trade Saved to DB: {symbol} PnL: {pnl:.2f}%")
        except Exception as e:
            logger.error(f"DB Log Error: {e}")
