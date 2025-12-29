"""
Portfolio Manager for Multi-Coin Regime Trading

Safeguards:
- (B) Market Gate applies to existing positions
- (E) max_new_entries_per_bar enforced
- (G) Circuit breakers (daily loss, consecutive loss, execution failures)
- (H) Comprehensive logging
"""
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from .utils import get_logger

logger = get_logger()


class PortfolioManager:
    """
    Portfolio-level management for multi-coin trading.
    Enforces position limits, entry restrictions, and Market Gate actions.
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # Portfolio risk config
        risk_cfg = config.get('portfolio_risk', {})
        self.risk_per_trade_pct = risk_cfg.get('risk_per_trade_pct', 0.35) / 100
        self.daily_loss_limit_pct = risk_cfg.get('daily_loss_limit_pct', 2.0) / 100
        self.max_consecutive_losses = risk_cfg.get('max_consecutive_losses', 3)
        self.cooldown_bars = risk_cfg.get('cooldown_bars', 6)
        
        # Position limits
        self.max_positions_total = risk_cfg.get('max_positions_total', 2)
        self.max_longs = risk_cfg.get('max_longs', 2)
        self.max_shorts = risk_cfg.get('max_shorts', 2)
        self.max_new_entries_per_bar = risk_cfg.get('max_new_entries_per_bar', 1)
        
        # Leverage caps
        self.leverage_default = risk_cfg.get('leverage_default', 3)
        leverage_caps = risk_cfg.get('leverage_caps', {})
        self.leverage_cap_lowvol = leverage_caps.get('LOWVOL', 5)
        self.leverage_cap_highvol = leverage_caps.get('HIGHVOL', 3)
        self.leverage_cap_panic = leverage_caps.get('PANIC', 2)
        
        # Market Gate config
        gate_cfg = config.get('market_gate', {})
        self.panic_cfg = gate_cfg.get('panic_actions', {})
        self.riskoff_cfg = gate_cfg.get('riskoff_actions', {})
        
        # State tracking
        self._daily_start_equity = None
        self._last_reset_day = None
        self._consecutive_losses = 0
        self._cooldown_until_bar = 0
        self._entries_this_bar = 0
        self._current_bar_ts = None
        self._execution_failures = 0
        self._daily_realized_pnl = 0.0
        
        # Circuit breaker state (Safeguard G)
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = None
        
        logger.info(f"💼 PortfolioManager initialized (max_pos: {self.max_positions_total}, "
                   f"entries/bar: {self.max_new_entries_per_bar}, daily_limit: {self.daily_loss_limit_pct*100}%)")
    
    def reset_daily(self, current_equity: float):
        """Reset daily counters at start of trading day."""
        today = datetime.now().day
        if self._last_reset_day != today:
            self._daily_start_equity = current_equity
            self._last_reset_day = today
            self._daily_realized_pnl = 0.0
            self._consecutive_losses = 0
            self._circuit_breaker_active = False
            self._circuit_breaker_reason = None
            logger.info(f"🔄 Daily reset: equity={current_equity:.2f}")
    
    def record_trade_result(self, pnl: float):
        """Record trade result for consecutive loss tracking (Safeguard G)."""
        self._daily_realized_pnl += pnl
        
        if pnl < 0:
            self._consecutive_losses += 1
            logger.info(f"📉 Loss recorded: consecutive={self._consecutive_losses}")
            
            if self._consecutive_losses >= self.max_consecutive_losses:
                self._activate_cooldown("consecutive_losses")
        else:
            self._consecutive_losses = 0
    
    def record_execution_failure(self):
        """Record execution failure (Safeguard G)."""
        self._execution_failures += 1
        if self._execution_failures >= 3:
            self._circuit_breaker_active = True
            self._circuit_breaker_reason = f"execution_failures({self._execution_failures})"
            logger.critical(f"🚫 [Circuit Breaker] Activated: {self._circuit_breaker_reason}")
    
    def reset_execution_failures(self):
        """Reset execution failure counter after successful trade."""
        self._execution_failures = 0
    
    def _activate_cooldown(self, reason: str):
        """Activate cooldown period."""
        self._cooldown_until_bar = self.cooldown_bars
        logger.warning(f"⏸️ Cooldown activated: {reason}, bars={self.cooldown_bars}")
    
    def check_new_bar(self, bar_timestamp):
        """Update bar tracking for entry limits (Safeguard E)."""
        if bar_timestamp != self._current_bar_ts:
            self._current_bar_ts = bar_timestamp
            self._entries_this_bar = 0
            
            # Decrement cooldown
            if self._cooldown_until_bar > 0:
                self._cooldown_until_bar -= 1
                logger.info(f"⏳ Cooldown remaining: {self._cooldown_until_bar} bars")
    
    def check_daily_loss_limit(self, current_equity: float) -> Tuple[bool, str]:
        """Check if daily loss limit is reached (Safeguard G)."""
        if self._daily_start_equity is None or self._daily_start_equity <= 0:
            return True, "ok"
        
        pnl_pct = (current_equity - self._daily_start_equity) / self._daily_start_equity
        
        if pnl_pct < -self.daily_loss_limit_pct:
            self._circuit_breaker_active = True
            self._circuit_breaker_reason = f"daily_loss({pnl_pct*100:.2f}%)"
            logger.critical(f"🛑 [Daily Limit] {pnl_pct*100:.2f}% < -{self.daily_loss_limit_pct*100}%")
            return False, self._circuit_breaker_reason
        
        return True, "ok"
    
    def can_open_position(self, 
                         current_positions: List[Dict],
                         trade_direction: str,
                         market_gate: str,
                         current_equity: float) -> Tuple[bool, str]:
        """
        Check if a new position can be opened.
        Returns: (allowed, rejection_reason)
        """
        # 1. Circuit breaker check (Safeguard G)
        if self._circuit_breaker_active:
            return False, f"circuit_breaker({self._circuit_breaker_reason})"
        
        # 2. Daily loss limit check (Safeguard G)
        self.reset_daily(current_equity)
        allowed, reason = self.check_daily_loss_limit(current_equity)
        if not allowed:
            return False, reason
        
        # 3. Cooldown check (Safeguard G)
        if self._cooldown_until_bar > 0:
            return False, f"cooldown({self._cooldown_until_bar}_bars)"
        
        # 4. Entries per bar limit (Safeguard E)
        if self._entries_this_bar >= self.max_new_entries_per_bar:
            return False, f"max_entries_this_bar({self._entries_this_bar}/{self.max_new_entries_per_bar})"
        
        # 5. Total positions limit
        max_total = self.max_positions_total
        if market_gate == "PANIC":
            max_total = self.panic_cfg.get('max_positions_total', 1)
        elif market_gate == "RISKOFF":
            max_total = self.riskoff_cfg.get('max_positions_total', 1)
        
        if len(current_positions) >= max_total:
            return False, f"max_positions({len(current_positions)}/{max_total})"
        
        # 6. Direction-specific limits
        long_count = sum(1 for p in current_positions if p.get('side') == 'long')
        short_count = sum(1 for p in current_positions if p.get('side') == 'short')
        
        if trade_direction == 'long':
            if long_count >= self.max_longs:
                return False, f"max_longs({long_count}/{self.max_longs})"
            # Market Gate: Panic blocks new longs (Safeguard B)
            if market_gate == "PANIC" and not self.panic_cfg.get('new_long_enabled', False):
                return False, "panic_gate_no_longs"
        else:
            if short_count >= self.max_shorts:
                return False, f"max_shorts({short_count}/{self.max_shorts})"
        
        return True, "ok"
    
    def mark_entry(self):
        """Mark that an entry was made this bar."""
        self._entries_this_bar += 1
        logger.info(f"🎯 Entry recorded: {self._entries_this_bar}/{self.max_new_entries_per_bar} this bar")
    
    def get_position_scale(self, regime: str, market_gate: str) -> float:
        """Get position scale based on regime and market gate."""
        settings = self.config.get('settings_by_regime', {}).get(regime, {})
        base_scale = settings.get('position_scale', 0.35)
        
        # Apply market gate modifier (Safeguard B)
        if market_gate == "PANIC":
            base_scale *= self.panic_cfg.get('position_scale_mult', 0.5)
        elif market_gate == "RISKOFF":
            base_scale *= self.riskoff_cfg.get('position_scale_mult', 0.7)
        
        return base_scale
    
    def get_leverage_cap(self, regime: str, market_gate: str) -> int:
        """Get leverage cap based on regime and market gate."""
        if market_gate == "PANIC":
            return self.leverage_cap_panic
        
        is_highvol = "HIGHVOL" in regime
        if is_highvol:
            return self.leverage_cap_highvol
        else:
            return self.leverage_cap_lowvol
    
    def get_existing_position_actions(self, 
                                      position: Dict,
                                      market_gate: str) -> Dict:
        """
        Get actions to apply to existing positions based on Market Gate (Safeguard B).
        Returns: {action, params}
        """
        if market_gate != "PANIC":
            return {'action': 'none'}
        
        side = position.get('side')
        
        # Panic gate actions for existing positions
        if side == 'long':
            return {
                'action': 'defensive',
                'tighten_trailing': self.panic_cfg.get('force_trailing_tighten', True),
                'sl_tighten_mult': self.panic_cfg.get('force_sl_tighten_mult', 0.8),
                'partial_close_pct': 0.5 if self.panic_cfg.get('force_partial_close', False) else 0
            }
        else:
            # Shorts are okay in panic, but still apply some protection
            return {
                'action': 'monitor',
                'exit_on_reversal': True
            }
    
    def get_status_summary(self) -> Dict:
        """Get current status for logging (Safeguard H)."""
        return {
            'circuit_breaker': self._circuit_breaker_active,
            'circuit_reason': self._circuit_breaker_reason,
            'consecutive_losses': self._consecutive_losses,
            'cooldown_bars': self._cooldown_until_bar,
            'entries_this_bar': self._entries_this_bar,
            'daily_pnl': self._daily_realized_pnl
        }
