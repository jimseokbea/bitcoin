from datetime import datetime
from .utils import get_logger

logger = get_logger()

class RiskManager:
    def __init__(self, config):
        self.max_daily_loss = config['risk']['max_daily_loss']
        self.daily_start_equity = None
        self.last_reset_day = datetime.now().day
        self.daily_trade_count = 0
        logger.info("🛡️ Risk Manager Initialized (Daily Limit & BTC Fuse Ready)")

    def check_daily_limit(self, executor):
        """
        Checks if the daily loss limit has been reached.
        And resets daily counters if day changed.
        """
        try:
            current_equity = executor.get_balance()
            now_day = datetime.now().day

            # Reset Daily
            if self.daily_start_equity is None or now_day != self.last_reset_day:
                self.daily_start_equity = current_equity
                self.last_reset_day = now_day
                self.daily_trade_count = 0 # Reset trade count
                logger.info(f"🔄 Daily Stats Reset. Trades: {self.daily_trade_count}, Equity: {self.daily_start_equity}")
                return True

            # Calculate PnL
            if self.daily_start_equity <= 0:
                pnl_pct = 0.0
            else:
                pnl_pct = (current_equity - self.daily_start_equity) / self.daily_start_equity
            
            if pnl_pct < -self.max_daily_loss:
                logger.critical(f"🛑 Daily Loss Limit Hit: {pnl_pct*100:.2f}% < -{self.max_daily_loss*100}%")
                return False
            
            return True

        except Exception as e:
            logger.error(f"Risk Check Error: {e}")
            return True # Fail open

    def check_trade_count(self, limit):
        if self.daily_trade_count >= limit:
            logger.warning(f"🛑 Daily Trade Limit Reached ({self.daily_trade_count}/{limit}) - Resting...")
            return False
        return True

    def increment_trade_count(self):
        self.daily_trade_count += 1
        logger.info(f"🔢 Daily Trade Count Updated: {self.daily_trade_count}")

    def check_btc_crash(self, executor):
        """
        [Secret Guard] Checks if BTC is crashing (-1% in 5m).
        """
        try:
            # Quick fetch ticker or 5m candle
            ticker = executor.exchange.fetch_ticker('BTC/USDT')
            # 5m candle fetch (limit 20 to calc ATR if needed, simplified: use 0.8%)
            ohlcv = executor.exchange.fetch_ohlcv('BTC/USDT', timeframe='5m', limit=20)
            if not ohlcv or len(ohlcv) < 20: return False
            
            # Calc Dynamic Threshold: max(0.8%, 0.8 * ATR_pct)
            # Simple ATR approx on 5m
            closes = [x[4] for x in ohlcv]
            highs = [x[2] for x in ohlcv]
            lows = [x[3] for x in ohlcv]
            # Manual ATR-ish (High-Low average)
            tr_sum = 0
            for i in range(1, len(ohlcv)):
                tr_sum += (highs[i] - lows[i])
            avg_tr = tr_sum / (len(ohlcv)-1)
            avg_price = sum(closes) / len(closes)
            atr_pct = avg_tr / avg_price
            
            threshold = max(0.008, 0.8 * atr_pct)
            
            # Check Drop
            open_p = ohlcv[-1][1] # Current candle open
            curr_p = ohlcv[-1][4] # Current candle close (or real-time)
            # Actually, catching the crash *during* the candle is better.
            # Compare current close vs open of current, AND prev close.
            # User said: return_5m < -max(...)
            # Let's check Drop from Open of current candle.
            
            drop = (curr_p - open_p) / open_p
            
            if drop < -threshold:
                logger.warning(f"🚨 BTC Crash Detected: {drop*100:.2f}% drop > Threshold {threshold*100:.2f}%")
                return True
            return False
        except Exception:
            return False
