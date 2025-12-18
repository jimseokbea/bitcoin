# core/runtime_guards.py
"""
[RUNTIME SAFETY GUARDS]
Prevents the 5 runtime killers that pass compile but fail in production.
"""

import time
import json
from datetime import datetime
from pathlib import Path
from .system_utils import LOGGER

class MarketCacheManager:
    """
    A. Symbol/Market cache staleness prevention.
    Reloads markets every N hours.
    """
    def __init__(self, exchange, reload_hours=6):
        self.exchange = exchange
        self.reload_interval = reload_hours * 3600
        self.last_reload = 0
        
    def ensure_fresh(self):
        """Call this periodically in main loop."""
        now = time.time()
        if now - self.last_reload > self.reload_interval:
            try:
                LOGGER.info("🔄 Market cache reload...")
                self.exchange.load_markets(True)  # Force reload
                self.last_reload = now
                LOGGER.info("✅ Market cache refreshed")
            except Exception as e:
                LOGGER.warning(f"Market reload failed: {e}")


class QuantityNormalizer:
    """
    B. Position quantity unit standardization.
    Converts between contracts/base amount consistently.
    """
    def __init__(self, exchange):
        self.exchange = exchange
        
    def to_contracts(self, symbol, amount):
        """Convert base amount to contracts."""
        try:
            market = self.exchange.market(symbol)
            contract_size = market.get('contractSize', 1)
            return amount / contract_size
        except:
            return amount
            
    def to_base(self, symbol, contracts):
        """Convert contracts to base amount."""
        try:
            market = self.exchange.market(symbol)
            contract_size = market.get('contractSize', 1)
            return contracts * contract_size
        except:
            return contracts
            
    def get_step_size(self, symbol):
        """Get minimum qty increment for symbol."""
        try:
            market = self.exchange.market(symbol)
            return market.get('precision', {}).get('amount', 0.001)
        except:
            return 0.001
            
    def round_qty(self, symbol, qty):
        """Round qty to valid step size."""
        step = self.get_step_size(symbol)
        if step > 0:
            return round(qty / step) * step
        return qty


class SLSyncGuard:
    """
    C. SL quantity sync after partial close.
    Ensures SL order matches remaining position.
    """
    def __init__(self):
        self.pending_sync = {}  # symbol -> remaining_qty
        
    def mark_partial_close(self, symbol, closed_qty, remaining_qty):
        """Mark that a partial close happened and SL needs sync."""
        self.pending_sync[symbol] = remaining_qty
        LOGGER.info(f"🔔 SL sync pending for {symbol}: {remaining_qty} remaining")
        
    def needs_sync(self, symbol):
        """Check if SL sync is needed."""
        return symbol in self.pending_sync
        
    def get_sync_qty(self, symbol):
        """Get the qty that SL should be updated to."""
        return self.pending_sync.get(symbol, 0)
        
    def clear_sync(self, symbol):
        """Clear sync flag after update."""
        if symbol in self.pending_sync:
            del self.pending_sync[symbol]
            LOGGER.info(f"✅ SL sync completed for {symbol}")


class OrderCleanupGate:
    """
    D. reduceOnly verification gate.
    Ensures orphan orders are cleaned when position=0.
    """
    def __init__(self, exchange):
        self.exchange = exchange
        
    def cleanup_orphans(self, symbol):
        """
        Final gate: If position=0, cancel ALL open orders for symbol.
        This catches reduceOnly bypass cases.
        """
        try:
            # Check position
            positions = self.exchange.fetch_positions([symbol])
            has_position = False
            for p in positions:
                if p['symbol'] == symbol and float(p.get('contracts', 0)) != 0:
                    has_position = True
                    break
                    
            if not has_position:
                # Cancel all orders
                open_orders = self.exchange.fetch_open_orders(symbol)
                if open_orders:
                    LOGGER.warning(f"🧹 Orphan orders detected for {symbol} (position=0). Cleaning {len(open_orders)} orders...")
                    for o in open_orders:
                        try:
                            self.exchange.cancel_order(o['id'], symbol)
                        except:
                            pass
                    LOGGER.info(f"✅ Orphan cleanup done for {symbol}")
                    
        except Exception as e:
            LOGGER.error(f"Orphan cleanup error ({symbol}): {e}")


class ConsecutiveErrorKillSwitch:
    """
    E. Kill-switch level 2.
    Triggers safe shutdown on N consecutive errors/rejects within M minutes.
    """
    def __init__(self, max_errors=5, window_minutes=10):
        self.max_errors = max_errors
        self.window_seconds = window_minutes * 60
        self.error_timestamps = []
        self.triggered = False
        
    def record_error(self, error_type="generic"):
        """Record an error occurrence."""
        now = time.time()
        self.error_timestamps.append(now)
        
        # Clean old errors outside window
        cutoff = now - self.window_seconds
        self.error_timestamps = [t for t in self.error_timestamps if t > cutoff]
        
        LOGGER.warning(f"❌ Error recorded ({error_type}): {len(self.error_timestamps)}/{self.max_errors} in window")
        
        if len(self.error_timestamps) >= self.max_errors:
            self.triggered = True
            LOGGER.critical(f"🛑 KILL SWITCH TRIGGERED: {self.max_errors} errors in {self.window_seconds//60} minutes!")
            
    def is_triggered(self):
        return self.triggered
        
    def reset(self):
        """Manual reset after investigation."""
        self.triggered = False
        self.error_timestamps = []
        LOGGER.info("🔄 Kill switch reset")


class StateSnapshotManager:
    """
    Bonus: State snapshot for fast recovery.
    Saves position/order summary on important events.
    """
    def __init__(self, filepath="state_snapshot.json"):
        self.filepath = Path(filepath)
        
    def save(self, data):
        """Save state snapshot."""
        try:
            snapshot = {
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            with open(self.filepath, 'w') as f:
                json.dump(snapshot, f, indent=2, default=str)
            LOGGER.debug(f"💾 State snapshot saved")
        except Exception as e:
            LOGGER.error(f"Snapshot save failed: {e}")
            
    def load(self):
        """Load state snapshot."""
        try:
            if self.filepath.exists():
                with open(self.filepath) as f:
                    return json.load(f)
        except:
            pass
        return None
