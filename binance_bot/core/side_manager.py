# core/side_manager.py
"""
[SIDE SCALPER MODULE - V2]
Adaptive Side Market Trading Logic:
1. ATR-based dynamic TP with proper unit conversion
2. Cooldown after 3 consecutive wins (TREND bypass allowed)
3. Trade frequency monitoring with enhanced logging
"""

from datetime import datetime, timedelta
from .system_utils import LOGGER

class SideManager:
    def __init__(self):
        # TP Config (Percentage based)
        self.min_tp_pct = 0.007   # 0.7% (includes fee buffer)
        self.max_tp_pct = 0.035   # 3.5% cap (unreachable prevention)
        self.atr_tp_mult = 0.5    # 50% of ATR
        
        # Cooldown Config
        self.max_consecutive_wins = 3
        self.cooldown_minutes = 45
        
        # State
        self.consecutive_wins = 0
        self.last_win_time = None
        self.cooldown_until = None
        self.daily_side_trades = 0
        self.last_reset_date = None
        
    def reset_daily_counter(self):
        """Reset daily trade counter at midnight."""
        now = datetime.now()
        if self.last_reset_date != now.date():
            self.daily_side_trades = 0
            self.last_reset_date = now.date()
            LOGGER.info("🔄 SIDE 일일 카운터 리셋")
    
    def is_on_cooldown(self, adx=None, is_strong_trend=False):
        """
        Check if SIDE trading is on cooldown.
        TREND bypass: If ADX > 30 and strong signal, allow entry.
        """
        if self.cooldown_until is None:
            return False
            
        if datetime.now() >= self.cooldown_until:
            self.cooldown_until = None
            self.consecutive_wins = 0
            LOGGER.info("✅ SIDE 쿨다운 해제")
            return False
        
        # TREND Bypass Logic
        if adx is not None and adx >= 30 and is_strong_trend:
            LOGGER.info(f"⚡ TREND 강신호 (ADX={adx:.1f})로 쿨다운 무시")
            return False
            
        remaining = (self.cooldown_until - datetime.now()).seconds // 60
        LOGGER.debug(f"⏸️ Cooldown active: skip entry ({remaining}분 남음)")
        return True
    
    def calculate_side_tp(self, entry_price, atr_1h):
        """
        Calculate dynamic SIDE TP with proper unit conversion.
        
        Formula:
            atr_pct = ATR / entry_price
            tp_pct = max(min_tp, 0.5 * atr_pct)
            tp_pct = min(tp_pct, max_tp)  # Cap
        """
        if entry_price <= 0 or atr_1h <= 0:
            LOGGER.warning("⚠️ Invalid price/ATR for TP calc. Using min TP.")
            return self.min_tp_pct, self.min_tp_pct, 0
        
        # Convert ATR to percentage
        atr_pct = atr_1h / entry_price
        
        # Calculate TP percentage
        raw_tp_pct = self.atr_tp_mult * atr_pct
        
        # Apply min/max caps
        final_tp_pct = max(self.min_tp_pct, raw_tp_pct)
        final_tp_pct = min(final_tp_pct, self.max_tp_pct)
        
        LOGGER.info(f"📊 SIDE TP computed: atr_pct={atr_pct*100:.3f}%, raw={raw_tp_pct*100:.2f}%, final={final_tp_pct*100:.2f}%")
        
        return final_tp_pct, atr_pct, raw_tp_pct
    
    def record_side_result(self, is_win, trade_info=None):
        """
        Record SIDE trade result and manage cooldown.
        Enhanced logging for analysis.
        """
        self.reset_daily_counter()
        self.daily_side_trades += 1
        
        cooldown_triggered = False
        
        if is_win:
            self.consecutive_wins += 1
            self.last_win_time = datetime.now()
            
            if self.consecutive_wins >= self.max_consecutive_wins:
                self.cooldown_until = datetime.now() + timedelta(minutes=self.cooldown_minutes)
                cooldown_triggered = True
                LOGGER.warning(f"⏸️ SIDE WIN streak = {self.consecutive_wins} → Cooldown start {self.cooldown_minutes}m")
            else:
                LOGGER.info(f"🎯 SIDE 연속 승리: {self.consecutive_wins}회")
        else:
            self.consecutive_wins = 0
            LOGGER.info("💔 SIDE 손실 발생. 연속 승리 카운터 리셋.")
        
        # Return cooldown status for logging
        return cooldown_triggered
    
    def get_risk_params(self, adx, is_cooldown_bypass=False):
        """
        Get risk parameters based on market regime.
        If bypassing cooldown, reduce risk by 0.8x for safety.
        Returns: (leverage, risk_per_trade, regime_label)
        """
        is_side = adx < 25
        
        if is_side:
            base_risk = 0.006
            lev = 4
            regime = 'SIDE'
        else:
            base_risk = 0.013
            lev = 7
            regime = 'TREND'
        
        # [SAFETY] Reduce risk on cooldown bypass (high volatility expected)
        if is_cooldown_bypass:
            base_risk *= 0.8
            LOGGER.info(f"⚠️ 쿨다운 우회: 리스크 0.8x 감산 적용 ({base_risk*100:.2f}%)")
        
        return lev, base_risk, regime
    
    def build_trade_record(self, symbol, action, entry_price, sl, tp_pct, atr_pct, adx, regime, entry_reason, cooldown_triggered=False):
        """
        Build comprehensive trade record for analysis.
        Contains the 5 essential fields for GA/tuner compatibility.
        """
        sl_pct = abs(entry_price - sl) / entry_price if entry_price > 0 else 0
        fees_estimate = 0.0008  # 0.08% roundtrip (conservative)
        
        record = {
            'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'symbol': symbol,
            'action': action,
            'entry_price': entry_price,
            
            # 5 Essential Fields
            'regime': regime,
            'entry_reason': entry_reason,
            'tp_pct': tp_pct,
            'sl_pct': sl_pct,
            'atr_pct': atr_pct,
            'fees_estimate': fees_estimate,
            'cooldown_triggered': cooldown_triggered,
            
            # Extra
            'adx': adx,
            'daily_trade_count': self.daily_side_trades
        }
        
        LOGGER.info(f"📝 Regime label saved: {regime} | TP={tp_pct*100:.2f}% | SL={sl_pct*100:.2f}% | ADX={adx:.1f}")
        
        return record
    
    def get_status_report(self):
        """Get current SIDE manager status for logging."""
        return {
            'consecutive_wins': self.consecutive_wins,
            'daily_trades': self.daily_side_trades,
            'on_cooldown': self.cooldown_until is not None,
            'cooldown_until': self.cooldown_until.strftime("%H:%M") if self.cooldown_until else None
        }
