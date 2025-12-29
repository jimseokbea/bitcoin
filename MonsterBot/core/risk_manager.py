from datetime import datetime
from .utils import get_logger

logger = get_logger()

class RiskManager:
    def __init__(self, config):
        self.max_daily_loss = config.get('risk', {}).get('max_daily_loss', 0.015)
        self.daily_start_equity = None
        self.last_reset_day = datetime.now().day
        self.daily_trade_count = 0
        
        # Regime-based risk config
        portfolio_cfg = config.get('portfolio_risk', {})
        self.daily_loss_limit_pct = portfolio_cfg.get('daily_loss_limit_pct', 2.0) / 100
        self.max_consecutive_losses = portfolio_cfg.get('max_consecutive_losses', 3)
        self.cooldown_bars = portfolio_cfg.get('cooldown_bars', 6)
        
        # Consecutive loss tracking (Safeguard G)
        self.consecutive_losses = 0
        self.cooldown_until_bar = 0
        self.current_bar_count = 0
        
        logger.info(f"🛡️ Risk Manager Initialized (Daily: {self.daily_loss_limit_pct*100}%, "
                   f"MaxLoss: {self.max_consecutive_losses}, Cooldown: {self.cooldown_bars})")

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
    
    # ============================================================
    # Consecutive Loss & Cooldown (Safeguard G)
    # ============================================================
    
    def record_trade_result(self, pnl: float):
        """Record trade result for consecutive loss tracking."""
        if pnl < 0:
            self.consecutive_losses += 1
            logger.info(f"📉 Loss recorded: consecutive={self.consecutive_losses}")
            
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.activate_cooldown()
        else:
            self.consecutive_losses = 0
            logger.info(f"📈 Win recorded: consecutive losses reset")
    
    def activate_cooldown(self):
        """Activate cooldown period after consecutive losses."""
        self.cooldown_until_bar = self.current_bar_count + self.cooldown_bars
        logger.warning(f"⏸️ Cooldown activated: {self.cooldown_bars} bars until bar #{self.cooldown_until_bar}")
    
    def check_cooldown(self) -> bool:
        """Check if in cooldown period. Returns True if trading allowed."""
        if self.current_bar_count < self.cooldown_until_bar:
            remaining = self.cooldown_until_bar - self.current_bar_count
            logger.info(f"⏳ Cooldown: {remaining} bars remaining")
            return False
        return True
    
    def increment_bar_count(self):
        """Increment bar counter for cooldown tracking."""
        self.current_bar_count += 1
    
    def is_trading_allowed(self, current_equity: float) -> tuple:
        """
        Check all trading conditions.
        Returns: (allowed, reason)
        """
        # Check daily loss limit (using regime config if available)
        if self.daily_start_equity and self.daily_start_equity > 0:
            pnl_pct = (current_equity - self.daily_start_equity) / self.daily_start_equity
            if pnl_pct < -self.daily_loss_limit_pct:
                return False, f"daily_loss({pnl_pct*100:.1f}%)"
        
        # Check cooldown
        if not self.check_cooldown():
            remaining = self.cooldown_until_bar - self.current_bar_count
            return False, f"cooldown({remaining}_bars)"
        
        return True, "ok"


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

    def check_fee_ratio(self, db):
        """
        [Safety Pin C] Fee Monitor
        Checks last 10 trades. If Fee / Gross_PnL > 30%, trigger cooldown.
        """
        try:
            # Query last 10 filled trades (WS_FILL log)
            db.cursor.execute("""
                SELECT pnl, commission FROM trades 
                WHERE strategy_type='WS_FILL' OR commission > 0
                ORDER BY timestamp DESC LIMIT 10
            """)
            rows = db.cursor.fetchall()
            
            if not rows or len(rows) < 3: # Need at least 3 trades to judge
                return True
            
            total_pnl = sum([abs(r[0]) for r in rows]) # Use absolute PnL (Activity base) or Net?
            # User said: "Total PnL 중 수수료 비중"
            # Usually means: Sum(Fees) / Sum(Gross Profit)? 
            # Or Sum(Fees) / Sum(Abs(PnL))?
            # Or Net PnL vs Fees?
            # "총 PnL 중 수수료 비중" -> If I made 100 USDT, and fee was 30 USDT -> 30%.
            # If I lost 100 USDT, fee is still positive.
            # Let's use: Sum(Fees) / Sum(Abs(PnL) + Fees) ? 
            # Or simply: Sum(Fees) vs Net PnL?
            # Interpretation: If trading burns too much fee relative to outcome.
            # Let's use: Sum(Fees) / Sum(Abs(Realized_PnL))
            
            total_comm = sum([abs(r[1]) for r in rows])
            total_abs_pnl = sum([abs(r[0]) for r in rows]) # Absolute movement captured
            
            if total_abs_pnl == 0: return True
            
            ratio = total_comm / total_abs_pnl
            
            if ratio > 0.30:
                logger.warning(f"🚨 [Fee Monitor] High Fee Ratio: {ratio*100:.1f}% > 30% (Last {len(rows)} trades). Cooldown triggered.")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Fee Monitor Error: {e}")
            return True
