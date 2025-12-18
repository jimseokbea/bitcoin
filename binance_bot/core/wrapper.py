import ccxt
import time
import pandas as pd
from .system_utils import LOGGER

class RateLimiter:
    def __init__(self, max_weight=10):
        self.tokens = max_weight
        self.last_ts = time.time()
    
    def consume(self, weight):
        now = time.time()
        # Refill 10 tokens per second
        refill = (now - self.last_ts) * 10 
        self.tokens = min(10, self.tokens + refill)
        self.last_ts = now
        
        if self.tokens < weight:
            # LOGGER.debug(f"⏳ Rate Limit Sleep: Need {weight}, Have {self.tokens:.2f}")
            time.sleep(1.0) # Forced Sleep
            self.tokens = 0
        else:
            self.tokens -= weight

class FuturesExecutor:
    def __init__(self, api_key, secret, leverage=5, testnet=False):
        """
        Initializes the Binance Futures Executor using CCXT.
        Multi-Symbol Capable.
        """
        self.leverage = leverage
        options = {'defaultType': 'future'}
        
        config = {
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': options
        }
        
        # [FIX] Demo Trading URL configuration (demo.binance.com)
        if testnet:
            LOGGER.info("⚠️ USING DEMO TRADING (demo.binance.com) ⚠️")
            # Demo trading uses same endpoints as production but different API keys
            # The API authenticates against demo.binance.com servers
            config['urls'] = {
                'api': {
                    'fapiPublic': 'https://fapi.binance.com/fapi/v1',
                    'fapiPrivate': 'https://fapi.binance.com/fapi/v1', 
                    'fapiPrivateV2': 'https://fapi.binance.com/fapi/v2',
                }
            }
            # Demo trading might need sandbox mode for proper authentication routing
            
        self.exchange = ccxt.binance(config)
        
        if testnet:
            # Try sandbox mode for demo trading
            self.exchange.set_sandbox_mode(True)

        self.limiter = RateLimiter()

    def set_leverage_for_symbol(self, symbol):
        try:
            self.limiter.consume(1)
            self.exchange.set_leverage(self.leverage, symbol)
        except Exception as e:
            LOGGER.warning(f"Set Lev Error ({symbol}): {e}")

        try:
            self.exchange.set_margin_mode('ISOLATED', symbol)
        except Exception as e:
            # Code -4168: Multi-Asset Mode prevention
            if "Multi-Assets mode" in str(e):
                LOGGER.info(f"ℹ️ Multi-Asset Mode Detected. Skipping Isolation.")
            else:
                LOGGER.warning(f"Set Margin Error ({symbol}): {e}")

    def fetch_ohlcv(self, symbol, interval='15m', limit=100):
        try:
            self.limiter.consume(2)
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            LOGGER.error(f"Error fetching OHLCV ({symbol}): {e}")
            return None

    def fetch_balance(self):
        try:
            self.limiter.consume(5)
            balance = self.exchange.fetch_balance()
            # Try/Catch for different API responses (Testnet vs Real)
            try:
                return float(balance['info']['totalWalletBalance'])
            except:
                return float(balance['total']['USDT'])
        except Exception as e:
            LOGGER.error(f"Error fetching balance: {e}")
            return 0.0

    def fetch_open_positions(self):
        """
        Returns a list of all open positions.
        Used for Anti-Ghosting (Sync).
        """
        try:
            self.limiter.consume(5)
            # Binance Futures usually requires symbols=None to fetch all, or check specific logic
            # CCXT fetch_positions behavior varies.
            # Safety: fetch_balance()['info']['positions'] is often reliable for Binance.
            # But fetch_positions is standard.
            positions = self.exchange.fetch_positions()
            active_positions = []
            for p in positions:
                amt = float(p['contracts'])
                if amt != 0:
                     active_positions.append({
                         'symbol': p['symbol'],
                         'amt': amt,
                         'side': p['side'],
                         'entryPrice': float(p['entryPrice']),
                         'leverage': p.get('leverage', self.leverage),
                         'unrealizedPnl': float(p.get('unrealizedPnl', 0))
                     })
            return active_positions
        except Exception as e:
            LOGGER.error(f"Fetch Positions Error: {e}")
            return []

    def get_real_position(self, symbol):
        """
        Returns: amount, side, entry_price
        """
        try:
            self.limiter.consume(5)
            positions = self.exchange.fetch_positions([symbol])
            for p in positions:
                if p['symbol'] == symbol:
                    amt = float(p['contracts'])
                    side = p['side'] # 'long', 'short', or None
                    if amt == 0: return 0.0, None, 0.0
                    
                    entry_price = float(p['entryPrice']) if p.get('entryPrice') else 0.0
                    if side == 'short': amt = -amt
                    
                    return amt, side, entry_price
            return 0.0, None, 0.0
        except Exception as e:
            LOGGER.error(f"Pos Check Error ({symbol}): {e}")
            return 0.0, None, 0.0

    def close_position(self, symbol):
        # 1. Cancel All
        try:
            self.limiter.consume(1)
            self.exchange.cancel_all_orders(symbol)
        except Exception as e:
            LOGGER.warning(f"Cancel All Error ({symbol}): {e}")

        # 2. Check Real
        amt, side, _ = self.get_real_position(symbol)
        if amt == 0 or side is None:
            return

        # 3. Reduce Only
        try:
            close_side = 'sell' if side == 'long' else 'buy'
            abs_amt = abs(amt)
            self.limiter.consume(1)
            
            LOGGER.info(f"🧹 Closing {symbol}: {close_side} {abs_amt}")
            self.exchange.create_order(
                symbol, 'market', close_side, abs_amt,
                {'reduceOnly': True}
            )
            LOGGER.info(f"✅ Position Closed Successfully ({symbol})")
        except Exception as e:
            LOGGER.error(f"❌ Close Fail ({symbol}): {e}")

    def _normalize_order_params(self, order_type, stop_price, reduce_only=True):
        """
        Normalize order params for cross-version/exchange compatibility.
        Returns: (normalized_type, params_dict, fingerprint)
        """
        import hashlib
        
        # Normalize order type (CCXT sometimes uses lowercase)
        type_map = {
            'stop_market': 'STOP_MARKET',
            'take_profit_market': 'TAKE_PROFIT_MARKET',
            'STOP': 'STOP_MARKET',
            'TAKE_PROFIT': 'TAKE_PROFIT_MARKET'
        }
        normalized_type = type_map.get(order_type, order_type.upper())
        
        params = {
            'stopPrice': float(stop_price),
            'reduceOnly': reduce_only
        }
        
        # Create fingerprint for reproducibility
        fingerprint_str = f"{normalized_type}:{stop_price}:{reduce_only}"
        fingerprint = hashlib.md5(fingerprint_str.encode()).hexdigest()[:8]
        
        LOGGER.debug(f"📋 Order params normalized: type={normalized_type}, fingerprint={fingerprint}")
        
        return normalized_type, params, fingerprint

    def update_stop_loss(self, symbol, side, qty, new_stop_price):
        """
        Cancels existing STOP orders and places a new one.
        Uses normalized params with fingerprint logging.
        """
        try:
            # 1. Cancel existing stops
            self.limiter.consume(1)
            open_orders = self.exchange.fetch_open_orders(symbol)
            for o in open_orders:
                if o['type'] in ['STOP', 'STOP_MARKET', 'stop_market']:
                    self.exchange.cancel_order(o['id'], symbol)
            
            # 2. Normalize params
            sl_side = 'sell' if side == 'long' else 'buy'
            order_type, params, fingerprint = self._normalize_order_params('STOP_MARKET', new_stop_price)
            
            # 3. Place new stop
            self.limiter.consume(1)
            self.exchange.create_order(symbol, order_type, sl_side, abs(qty), None, params)
            
            LOGGER.info(f"🔄 SL Updated ({symbol}): {new_stop_price} [fp:{fingerprint}]")
            return True
        except Exception as e:
            LOGGER.error(f"SL Update Failed ({symbol}): {e}")
            return False

    def open_position(self, symbol, side, qty, sl_price):
        try:
            # 1. Entry
            self.limiter.consume(1)
            entry_order = self.exchange.create_order(symbol, 'market', side, qty)
            
            # 2. SL
            sl_side = 'sell' if side == 'buy' else 'buy'
            self.limiter.consume(1)
            
            params = {
                'stopPrice': sl_price,
                'reduceOnly': True
            }
            # FIX: Arg order (symbol, type, side, amount, price, params)
            self.exchange.create_order(symbol, 'STOP_MARKET', sl_side, qty, None, params)
            
            LOGGER.info(f"✅ Entry+SL Placed ({symbol}). SL: {sl_price}")
            return entry_order

        except Exception as e:
            LOGGER.error(f"❌ Open Position Fail ({symbol}): {e}")
            self.close_all(symbol)
            return None
    
    def create_order_market(self, symbol, side, qty, params={}):
        self.limiter.consume(1)
        return self.exchange.create_order(symbol, 'market', side, qty, params)
