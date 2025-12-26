from .utils import get_logger

logger = get_logger()

class PositionSizer:
    def __init__(self, config):
        self.risk_pct = config['risk']['risk_per_trade']
        self.max_lev = config['risk']['max_leverage']

    def calc_qty(self, equity, entry_price, stop_price, adx=None):
        """
        Calculates position size based on risk percentage and stop loss distance.
        [Safety Pin B] Side Mode Risk Cap: If ADX < 25, reduce risk to 60%.
        """
        try:
            if equity <= 0: return 0.0

            # 1. Distance
            dist = abs(entry_price - stop_price)
            if dist == 0: return 0.0

            # [Sniper Mode Sizing]
            # 1. Risk Amount (Strict 0.7%)
            risk_ratio = 0.007
            
            # [Safety Pin B] Side Mode (Range) Check
            if adx is not None and adx < 25:
                # logger.info(f"🛡️ [Side Mode] Low ADX ({adx:.1f}) -> Reducing Risk to 0.6x")
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
            
            # 4. Leverage Cap (Max 3.0x)
            max_allowed_usdt = equity * 3.0
            
            if pos_size_usdt > max_allowed_usdt:
                pos_size_usdt = max_allowed_usdt
                logger.warning(f"⚠️ Leverage Cap Hit! Size Reduced to {pos_size_usdt:.2f} USDT")
                
            qty = pos_size_usdt / entry_price
            return qty

        except Exception as e:
            logger.error(f"Size Calc Error: {e}")
            return 0.0
