from datetime import datetime
from .system_utils import LOGGER

class FuturesRiskManager:
    def __init__(self, max_daily_loss_pct=0.02):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.daily_pnl_pct = 0.0
        self.start_equity = None
        self.last_reset = datetime.now()

    def update(self, current_equity):
        """
        Updates daily PnL and checks if trading should be paused.
        """
        now = datetime.now()
        if (now - self.last_reset).total_seconds() > 86400:
            self.daily_pnl_pct = 0.0
            self.start_equity = current_equity
            self.last_reset = now
            return True

        if self.start_equity is None:
            self.start_equity = current_equity
            return True

        pnl = current_equity - self.start_equity
        if self.start_equity > 0:
            self.daily_pnl_pct = pnl / self.start_equity
        else:
            self.daily_pnl_pct = 0.0

        if self.daily_pnl_pct < -self.max_daily_loss_pct:
            LOGGER.critical(f"🛑 Daily Loss Limit Hit: {self.daily_pnl_pct*100:.2f}% < -{self.max_daily_loss_pct*100}%")
            return False
        
        return True

    def check_liquid_safety(self, entry_price, sl_price, side, leverage):
        """
        Calculates Estimated Liquidation Price and ensures SL is safe.
        Buffer: Liquid price should be at least 0.5% away from SL.
        """
        # Rough Liq Price Estimate (Isolated)
        if side == 'buy':
            liq_price = entry_price * (1 - 1/leverage + 0.005)
            if sl_price <= liq_price:
                LOGGER.warning(f"⚠️ Risk Reject: SL {sl_price} is too close to Est. Liq {liq_price}")
                return False
        else:
            liq_price = entry_price * (1 + 1/leverage - 0.005)
            if sl_price >= liq_price:
                 LOGGER.warning(f"⚠️ Risk Reject: SL {sl_price} is too close to Est. Liq {liq_price}")
                 return False
        
        return True


class FuturesPositionSizer:
    def __init__(self, risk_per_trade_pct=1.0, max_leverage=3.0, min_notional=6.0):
        self.risk_pct = risk_per_trade_pct
        self.max_lev = max_leverage
        self.min_notional = min_notional

    def calc_qty(self, equity, entry_price, stop_price, abort_on_risk_overflow=True):
        """
        Calculates quantity based on Risk % and Distance.
        
        If minNotional forces qty increase that exceeds risk budget:
        - abort_on_risk_overflow=True: Returns 0 (entry aborted)
        - abort_on_risk_overflow=False: Returns adjusted qty with warning
        
        Returns: (qty, status_message)
        """
        try:
            # 1. Distance
            dist = abs(entry_price - stop_price)
            if dist == 0: return 0.0, "Zero Distance"

            # 2. Risk Amount (How much we are willing to lose)
            risk_amt = equity * self.risk_pct 
            
            # 3. Raw Qty (Risk / Distance)
            raw_qty = risk_amt / dist
            original_risk = dist * raw_qty  # Should equal risk_amt
            
            # 4. Leverage Cap
            notional = raw_qty * entry_price
            curr_lev = notional / equity
            
            if curr_lev > self.max_lev:
                # Cap to max leverage
                raw_qty = (equity * self.max_lev) / entry_price
                notional = raw_qty * entry_price
            
            # 5. Min Notional Check
            if notional < self.min_notional:
                # Need to increase qty to meet minNotional
                adjusted_qty = self.min_notional / entry_price
                actual_risk = dist * adjusted_qty
                
                # [SAFETY] Check if adjusted qty exceeds risk budget
                if actual_risk > risk_amt * 1.5:  # 50% tolerance
                    if abort_on_risk_overflow:
                        LOGGER.warning(f"🚫 Entry aborted: minNotional adjustment (${actual_risk:.2f}) exceeds risk budget (${risk_amt:.2f})")
                        return 0.0, f"Risk Overflow (${actual_risk:.2f} > ${risk_amt:.2f})"
                    else:
                        LOGGER.warning(f"⚠️ Risk overflow accepted: ${actual_risk:.2f} > ${risk_amt:.2f}")
                        return adjusted_qty, f"Risk Adjusted (${actual_risk:.2f})"
                
                LOGGER.info(f"📐 position size adjusted to minNotional: qty={adjusted_qty:.6f}")
                return adjusted_qty, "MinNotional Adjusted"

            return raw_qty, "OK"

        except Exception as e:
            LOGGER.error(f"Size Calc Error: {e}")
            return 0.0, f"Error: {e}"
