import pandas as pd
import numpy as np
from .system_utils import LOGGER

class SignalEngine:
    def __init__(self, config=None):
        self.config = config or {}
        # Scalping Params
        self.vwap_window = 200 # Rolling VWAP approximation or Session based if possible
        self.adx_period = 14
        self.atr_period = 14
        self.vol_ma_period = 20

    def _calculate_range_filter(self, df, period=20, mult=3.0):
        # 1. Range Size
        diff = df['close'].diff().abs()
        wper = period * 2 - 1
        avr = diff.ewm(span=period).mean() 
        rng_size = avr.ewm(span=wper).mean() * mult
        
        # 2. Filter Calculation (Vectorized approximation or loop)
        close = df['close'].values
        r = rng_size.values
        filt = np.zeros_like(close)
        
        filt[0] = close[0]
        
        for i in range(1, len(close)):
            prev_filt = filt[i-1]
            r_val = r[i]
            x = close[i]
            
            if x - r_val > prev_filt:
                filt[i] = x - r_val
            elif x + r_val < prev_filt:
                filt[i] = x + r_val
            else:
                filt[i] = prev_filt
        
        df['rng_filt'] = filt
        
        # 3. Direction
        df['rng_up'] = df['close'] > df['rng_filt']
        
        return df

    def _calculate_3m_indicators(self, df):
        try:
            # 1. VWAP
            v = df['volume']
            pv = df['close'] * v
            df['vwap'] = pv.rolling(window=self.vwap_window).sum() / v.rolling(window=self.vwap_window).sum()
            
            # 2. Volume MA
            df['vol_ma'] = df['volume'].rolling(window=self.vol_ma_period).mean()

            # 3. RSI (14)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

            # 4. Bollinger Bands (20, 2) [NEW for Safety]
            df['bb_mid'] = df['close'].rolling(window=20).mean()
            df['bb_std'] = df['close'].rolling(window=20).std()
            df['bb_up'] = df['bb_mid'] + (df['bb_std'] * 2.0)
            df['bb_low'] = df['bb_mid'] - (df['bb_std'] * 2.0)
            
            # 5. Range Filter [NEW]
            df = self._calculate_range_filter(df)
            
            # 6. EMA 200 [NEW for Trend Filter]
            df['ema200'] = df['close'].ewm(span=200).mean()

            return df
        except Exception as e:
            LOGGER.error(f"3m Ind Error: {e}")
            return df

    def _calculate_1h_indicators(self, df):
        try:
            # 1. ATR
            high = df['high']
            low = df['low']
            close = df['close']
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['atr'] = tr.rolling(window=self.atr_period).mean()
            
            # 2. ADX
            plus_dm = high.diff()
            minus_dm = low.diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm > 0] = 0
            minus_dm = minus_dm.abs()
            
            tr_s = tr.rolling(window=self.adx_period).sum()
            plus_di = 100 * (plus_dm.rolling(window=self.adx_period).sum() / tr_s)
            minus_di = 100 * (minus_dm.rolling(window=self.adx_period).sum() / tr_s)
            dx = (abs(plus_di - minus_di) / abs(plus_di + minus_di)) * 100
            df['adx'] = dx.rolling(window=self.adx_period).mean()
            
            return df
        except Exception as e:
            LOGGER.error(f"1h Ind Error: {e}")
            return df

    def analyze(self, df_3m, df_1h):
        """
        [Hybrid Strategy V2]
        - Range Mode: Tight SL (0.8 ATR) to survive Breakouts.
        - Trend Mode: Score Based Entry.
        """
        if df_3m is None or len(df_3m) < 200 or df_1h is None or len(df_1h) < 50:
            return None, 0, 0, {}

        # Calc Indicators
        df_3m = self._calculate_3m_indicators(df_3m)
        df_1h = self._calculate_1h_indicators(df_1h)

        # Reference Candles
        last_3m = df_3m.iloc[-2]
        last_1h = df_1h.iloc[-2]

        # Data Points
        vwap = last_3m['vwap']
        vol = last_3m['volume']
        vol_ma = last_3m['vol_ma']
        rsi = last_3m['rsi']
        rng_up = last_3m['rng_up']
        ema200 = last_3m['ema200'] # [NEW]
        
        price = last_3m['close'] # [BUGFIX] Define price

        adx = last_1h['adx']
        atr_1h = last_1h['atr']

        # ==========================================
        # MODE 1: RANGE TRADING (Sideways)
        # ==========================================
        if adx < 25:
            # Reversal Strategy: Buy Low (RSI<30), Sell High (RSI>70)
            # RELAXED for higher frequency
            
            # Long: Oversold + Above EMA200 (Uptrend Pullback)
            if rsi < 30 and price > ema200:  # Was 25 -> 30
                sl = price - (atr_1h * 2.0)
                tp = price + (atr_1h * 2.5)
                return 'buy', sl, tp, {'desc': f'Range Long (RSI {int(rsi)} > EMA200)', 'adx': adx}
            
            # Short: Overbought + Below EMA200 (Downtrend Bounce)
            if rsi > 70 and price < ema200:  # Was 75 -> 70
                sl = price + (atr_1h * 2.0)
                tp = price - (atr_1h * 2.5)
                return 'sell', sl, tp, {'desc': f'Range Short (RSI {int(rsi)} < EMA200)', 'adx': adx}
                
            return None, 0, 0, {}

        # ==========================================
        # MODE 2: TREND TRADING (Score Based)
        # ==========================================
        # Score System: Need 4.0 points to enter (was 5.0)
        score_long = 0
        score_short = 0
        
        # A. Volume (+3 points)
        if vol > (vol_ma * 1.3): 
            score_long += 3
            score_short += 3
        if vol > (vol_ma * 2.0): # Jackpot Volume
            score_long += 2
            score_short += 2
            
        # B. Trend Alignment (Price vs VWAP) (+3 points)
        if price > vwap: score_long += 3
        if price < vwap: score_short += 3
        
        # C. ADX Bonus (+2 points)
        if adx > 25:  # Was 30 -> 25
            score_long += 2
            score_short += 2

        # D. Range Filter Confirmation (+5 points) [POWERFUL]
        # [USER REQ] Breakout Strategy (High/Low Breakout)
        
        is_breakout = False # Flag to bypass VWAP check
        
        try:
            current_price = df_3m['close'].iloc[-1]
            
            rng_curr = df_3m['rng_up'].iloc[-2] # Completed Candle
            rng_prev = df_3m['rng_up'].iloc[-3] # Previous Candle
            
            # 1. FRESH LONG SIGNAL (False -> True)
            if rng_curr and not rng_prev:
                signal_high = df_3m['high'].iloc[-2]
                if current_price > signal_high:
                    score_long += 5 # BREAKOUT CONFIRMED!
                    is_breakout = True
                else:
                    pass # Waiting for breakout

            # 2. EXISTING LONG TREND (True -> True)
            elif rng_curr and rng_prev:
                score_long += 3 # Standard Trend Follow
                
            # 3. FRESH SHORT SIGNAL (True -> False)
            elif not rng_curr and rng_prev:
                signal_low = df_3m['low'].iloc[-2]
                if current_price < signal_low:
                    score_short += 5 # BREAKOUT CONFIRMED!
                    is_breakout = True

            # 4. EXISTING SHORT TREND (False -> False)
            elif not rng_curr and not rng_prev:
                score_short += 3 # Standard Trend Follow

        except Exception as e:
             LOGGER.warning(f"Breakout Check Error: {e}")
             pass

        # Threshold
        ENTRY_THRESHOLD = 3.0  # Was 4.0 -> 3.0 (Controlled Aggressive)
        
        # Execution
        if score_long >= ENTRY_THRESHOLD:
            # RELAXED: Accept any price when score is high enough
            sl = price - (atr_1h * 1.2)
            tp = price + (atr_1h * 3.0)
            desc = f'Trend Buy (Score {score_long})'
            if is_breakout: desc += " [Breakout]"
            return 'buy', sl, tp, {'desc': desc, 'adx': adx}

        if score_short >= ENTRY_THRESHOLD:
            # RELAXED: Accept any price when score is high enough
            sl = price + (atr_1h * 1.2)
            tp = price - (atr_1h * 3.0)
            desc = f'Trend Sell (Score {score_short})'
            if is_breakout: desc += " [Breakout]"
            return 'sell', sl, tp, {'desc': desc, 'adx': adx}

        return None, 0, 0, {'desc': f'Trend Low Score (L:{score_long} S:{score_short})', 'adx': adx}
