from .utils import get_logger

logger = get_logger()

class PositionSizer:
    def __init__(self, config):
        self.risk_pct = config.get('risk', {}).get('risk_per_trade', 0.01)
        self.max_lev = config.get('risk', {}).get('max_leverage', 3)
        
        # Regime-based defaults (can be overridden per call)
        portfolio_cfg = config.get('portfolio_risk', {})
        self.default_risk_pct = portfolio_cfg.get('risk_per_trade_pct', 0.35) / 100

    def calc_qty(self, equity, entry_price, stop_price, adx=None, 
                position_scale=1.0, leverage_cap=None):
        """
        Calculates position size based on risk percentage and stop loss distance.
        
        Args:
            equity: Current account equity
            entry_price: Entry price
            stop_price: Stop loss price
            adx: ADX value for side mode check (optional)
            position_scale: Regime-based position scale (0.35-1.0)
            leverage_cap: Maximum leverage allowed for this regime
        """
        try:
            if equity <= 0: return 0.0

            # 1. Distance
            dist = abs(entry_price - stop_price)
            if dist == 0: return 0.0

            # Base risk ratio (use regime config or fallback)
            risk_ratio = self.default_risk_pct
            
            # Apply position scale from regime
            risk_ratio *= position_scale
            
            # [Safety Pin B] Side Mode (Range) Check
            if adx is not None and adx < 25:
                risk_ratio *= 0.6
            
            risk_amt = equity * risk_ratio
            
            # 2. SL Distance %
            sl_dist_pct = abs(entry_price - stop_price) / entry_price
            if sl_dist_pct == 0: return 0.0
            
            # 3. Position Size ($)
            pos_size_usdt = risk_amt / sl_dist_pct
            
            # [Golden Time Booster] Apply BEFORE Cap
            from datetime import datetime, timezone
            utc_now = datetime.now(timezone.utc)
            h = utc_now.hour
            is_golden = (13 <= h <= 16) or (7 <= h <= 10)
            if is_golden:
                pos_size_usdt *= 1.2
            
            # 4. Leverage Cap (Use regime-specific or default)
            effective_lev_cap = leverage_cap if leverage_cap else self.max_lev
            max_allowed_usdt = equity * effective_lev_cap
            
            if pos_size_usdt > max_allowed_usdt:
                pos_size_usdt = max_allowed_usdt
                logger.warning(f"⚠️ Leverage Cap ({effective_lev_cap}x) Hit! Size: {pos_size_usdt:.2f} USDT")
                
            qty = pos_size_usdt / entry_price
            return qty

        except Exception as e:
            logger.error(f"Size Calc Error: {e}")
            return 0.0
    
    def calc_qty_regime(self, equity, entry_price, stop_price, 
                       regime_settings: dict, market_gate: str = None) -> float:
        """
        Calculate position size with regime settings.
        
        Args:
            equity: Current account equity
            entry_price: Entry price
            stop_price: Stop loss price
            regime_settings: Dict with position_scale, leverage_cap, etc.
            market_gate: Current market gate status ('NORMAL', 'RISKOFF', 'PANIC')
        """
        position_scale = regime_settings.get('position_scale', 1.0)
        leverage_cap = regime_settings.get('leverage_cap', self.max_lev)
        
        # Apply market gate modifiers
        if market_gate == 'PANIC':
            position_scale *= 0.5
            leverage_cap = min(leverage_cap, 2)
        elif market_gate == 'RISKOFF':
            position_scale *= 0.7
        
        return self.calc_qty(
            equity, entry_price, stop_price,
            position_scale=position_scale,
            leverage_cap=leverage_cap
        )
