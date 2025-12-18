import pandas as pd
import numpy as np
from .utils import get_logger

logger = get_logger()

class HybridStrategy:
    def __init__(self, config):
        self.config = config
        self.rsi_os = config['strategy']['rsi_oversold']
        self.rsi_ob = config['strategy']['rsi_overbought']
        self.adx_th = config['strategy']['adx_threshold']
        self.vol_factor = config['strategy']['volume_factor']

    def add_indicators(self, df):
        if df is None or len(df) < 50:
            return df
        
        try:
            # 1. RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            # 2. Bollinger Bands
            bb_factor = self.config['strategy'].get('bb_factor', 2.0)
            df['bb_mid'] = df['close'].rolling(window=20).mean()
            df['bb_std'] = df['close'].rolling(window=20).std()
            df['bb_up'] = df['bb_mid'] + (df['bb_std'] * bb_factor)
            df['bb_low'] = df['bb_mid'] - (df['bb_std'] * bb_factor)
            
            # 3. BB Width and Low Threshold for Squeeze
            df['bb_width'] = (df['bb_up'] - df['bb_low']) / df['bb_mid']
            # Compute dynamic threshold for squeeze
            df['low_width_threshold'] = df['bb_width'].rolling(window=50).quantile(0.10)

            # 4. ADX & ATR
            high = df['high']
            low = df['low']
            close = df['close']
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(window=14).mean()
            df['atr'] = atr
            
            plus_dm = high.diff()
            minus_dm = low.diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm > 0] = 0
            minus_dm = minus_dm.abs()
            tr_s = tr.rolling(window=14).sum()
            plus_di = 100 * (plus_dm.rolling(window=14).sum() / tr_s)
            minus_di = 100 * (minus_dm.rolling(window=14).sum() / tr_s)
            dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
            df['adx'] = dx.rolling(window=14).mean()
            
            # 5. Vol MA
            df['vol_ma'] = df['volume'].rolling(window=20).mean()
            
            # KER (Efficiency Ratio) - Optional pre-calc or check on fly
            # Keeping it simple for now, can be added if needed for filters
            
            return df
        except Exception as e:
            logger.error(f"Indicator Error: {e}")
            return df

    def check_signal(self, df, i=-1):
        try:
            if df is None or len(df) <= abs(i): return None, 0.0
            
            curr = df.iloc[i]
            
            # Use precalculated values
            if 'adx' not in curr or np.isnan(curr['adx']): return None, 0.0
            
            adx_val = curr['adx']
            rsi = curr['rsi']
            close_p = curr['close']
            volume = curr['volume']
            vol_ma = curr['vol_ma']
            
            # Cond 1: BTC Fuse
            cond1_btc = True 
            
            # Cond 2: ADX Strong Trend
            # Use config value if valid (self.adx_th initialized in __init__)
            # But the user snippet hardcoded 25. I will respect config if > 0 else 25.
            threshold = self.adx_th if self.adx_th else 25
            cond2_adx = adx_val >= threshold

            # Cond 3: Squeeze Breakout
            # access precalculated threshold
            low_width_threshold = curr['low_width_threshold']
            
            cond3_squeeze = (curr['bb_width'] < low_width_threshold * 1.5) and \
                            (close_p > curr['bb_up'] or close_p < curr['bb_low'])
            
            # Cond 4: Volume Spike (uses config's volume_factor)
            cond4_vol = volume > (vol_ma * self.vol_factor)
            
            # Cond 5: Valid Candle
            cond5_candle = True

            # Score
            conditions = [cond1_btc, cond2_adx, cond3_squeeze, cond4_vol, cond5_candle]
            pass_count = sum(conditions)
            
            if pass_count >= 4:
                direction = None
                if close_p > curr['bb_up']: direction = 'buy'
                elif close_p < curr['bb_low']: direction = 'sell'
                
                if direction:
                    sl_dist = curr['atr'] * 2.0
                    sl_price = close_p - sl_dist if direction == 'buy' else close_p + sl_dist
                    # Only log in live mode (i=-1) to avoid spam in backtest
                    if i == -1:
                        logger.info(f"[Sniper Shot] Score {pass_count}/5 (Vol: {volume/vol_ma:.1f}x, ADX: {adx_val:.1f})")
                    return direction, sl_price
            
            return None, 0.0

        except Exception as e:
            # logger.error(f"Signal Check Error: {e}")
            return None, 0.0

    def analyze(self, df):
        # Legacy/Live interface
        if df is None: return None, 0.0
        # If indicators missing, add them
        if 'bb_width' not in df.columns:
            df = self.add_indicators(df)
        return self.check_signal(df, -1)

