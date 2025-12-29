"""
Regime-Based Trading Strategy

6 regime-specific entry/exit strategies:
- RANGE_LOWVOL: Mean reversion light
- RANGE_HIGHVOL: Protect (no trading)
- UPTREND_LOWVOL: Trend pullback long
- UPTREND_HIGHVOL: Breakout trend long
- DOWNTREND_LOWVOL: Trend pullback short
- DOWNTREND_HIGHVOL: Panic defense / momentum short
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from .utils import get_logger

logger = get_logger()

# Regime constants
REGIME_RANGE_LOWVOL = "RANGE_LOWVOL"
REGIME_RANGE_HIGHVOL = "RANGE_HIGHVOL"
REGIME_UPTREND_LOWVOL = "UPTREND_LOWVOL"
REGIME_UPTREND_HIGHVOL = "UPTREND_HIGHVOL"
REGIME_DOWNTREND_LOWVOL = "DOWNTREND_LOWVOL"
REGIME_DOWNTREND_HIGHVOL = "DOWNTREND_HIGHVOL"


class RegimeStrategy:
    """
    Strategy that adapts entry/exit logic based on detected regime.
    """
    
    def __init__(self, config: dict):
        self.config = config
        
        # Strategy params
        strat_cfg = config.get('strategy', {})
        self.rsi_period = strat_cfg.get('rsi_period', 14)
        self.rsi_oversold = strat_cfg.get('rsi_oversold', 30)
        self.rsi_overbought = strat_cfg.get('rsi_overbought', 70)
        self.bb_len = strat_cfg.get('bb_len', 20)
        self.bb_std = strat_cfg.get('bb_std', 2.0)
        
        # Regime settings
        self.settings_by_regime = config.get('settings_by_regime', {})
        
        logger.info("📈 RegimeStrategy initialized")
    
    def add_strategy_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicators needed for strategy signals."""
        if df is None or len(df) < 50:
            return df
        
        try:
            close = df['close']
            high = df['high']
            low = df['low']
            volume = df['volume']
            
            # RSI
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(window=self.rsi_period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
            rs = gain / (loss + 1e-10)
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # Bollinger Bands
            df['bb_mid'] = close.rolling(window=self.bb_len).mean()
            df['bb_std'] = close.rolling(window=self.bb_len).std()
            df['bb_upper'] = df['bb_mid'] + (df['bb_std'] * self.bb_std)
            df['bb_lower'] = df['bb_mid'] - (df['bb_std'] * self.bb_std)
            df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
            
            # EMA20 for pullback zone
            df['ema20'] = close.ewm(span=20, adjust=False).mean()
            
            # Volume MA
            df['vol_ma'] = volume.rolling(window=20).mean()
            
            # Candle body size (for shock rule)
            df['body_pct'] = abs(close - df['open']) / df['open'] * 100
            df['is_bearish'] = close < df['open']
            df['is_bullish'] = close > df['open']
            
            # Swing high/low (for breakout detection)
            df['swing_high'] = high.rolling(window=10).max().shift(1)
            df['swing_low'] = low.rolling(window=10).min().shift(1)
            
            return df
            
        except Exception as e:
            logger.error(f"Strategy indicator error: {e}")
            return df
    
    def check_signal(self, df: pd.DataFrame, regime: str, 
                    settings: dict, symbol: str) -> Optional[Dict]:
        """
        Check for entry signal based on regime.
        Returns: {direction, sl_price, entry_reason} or None
        """
        if df is None or len(df) < 50:
            return None
        
        # Check if trading is enabled for this regime
        if not settings.get('trade_enabled', False):
            return None
        
        mode = settings.get('mode', 'protect')
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        atr = curr.get('atr', 0)
        close = curr['close']
        sl_atr_mult = settings.get('sl_atr_mult', 1.0)
        
        signal = None
        
        if mode == 'mean_reversion_light':
            signal = self._check_mean_reversion(curr, prev, atr, close, sl_atr_mult)
        elif mode == 'trend_pullback':
            signal = self._check_trend_pullback_long(df, curr, atr, close, sl_atr_mult)
        elif mode == 'breakout_trend':
            signal = self._check_breakout_long(curr, prev, atr, close, sl_atr_mult)
        elif mode == 'trend_pullback_short':
            signal = self._check_trend_pullback_short(df, curr, atr, close, sl_atr_mult)
        elif mode == 'panic_defense_or_momentum_short':
            signal = self._check_momentum_short(curr, prev, atr, close, sl_atr_mult)
        
        if signal:
            signal['symbol'] = symbol
            signal['regime'] = regime
            signal['mode'] = mode
            logger.info(f"🎯 [Signal] {symbol} {signal['direction'].upper()} "
                       f"(regime: {regime}, reason: {signal.get('reason', 'n/a')})")
        
        return signal
    
    def _check_mean_reversion(self, curr, prev, atr, close, sl_mult) -> Optional[Dict]:
        """Mean reversion: RSI oversold/overbought + BB bounce."""
        rsi = curr.get('rsi', 50)
        bb_lower = curr.get('bb_lower', close)
        bb_upper = curr.get('bb_upper', close)
        
        # Long: RSI oversold + price touched lower BB + bounce
        if rsi < self.rsi_oversold and close > bb_lower and prev['close'] <= prev.get('bb_lower', close):
            return {
                'direction': 'long',
                'sl_price': close - (atr * sl_mult),
                'reason': f'mean_rev_long(rsi={rsi:.1f})'
            }
        
        # Short: RSI overbought + price touched upper BB + drop
        if rsi > self.rsi_overbought and close < bb_upper and prev['close'] >= prev.get('bb_upper', close):
            return {
                'direction': 'short',
                'sl_price': close + (atr * sl_mult),
                'reason': f'mean_rev_short(rsi={rsi:.1f})'
            }
        
        return None
    
    def _check_trend_pullback_long(self, df, curr, atr, close, sl_mult) -> Optional[Dict]:
        """Trend pullback long: Price in EMA20-EMA50 zone, bounce confirmed."""
        ema20 = curr.get('ema20', close)
        ema50 = curr.get('ema_fast', close)
        prev = df.iloc[-2]
        
        # Check pullback zone: price between EMA20 and EMA50
        if ema50 > 0 and ema20 > 0:
            zone_low = min(ema20, ema50)
            zone_high = max(ema20, ema50)
            
            in_zone = zone_low <= curr['low'] <= zone_high
            bounce = close > prev['close'] and close > ema20
            
            if in_zone and bounce:
                return {
                    'direction': 'long',
                    'sl_price': close - (atr * sl_mult),
                    'reason': 'pullback_bounce'
                }
        
        return None
    
    def _check_trend_pullback_short(self, df, curr, atr, close, sl_mult) -> Optional[Dict]:
        """Trend pullback short: Price in EMA20-EMA50 zone, rejection confirmed."""
        ema20 = curr.get('ema20', close)
        ema50 = curr.get('ema_fast', close)
        prev = df.iloc[-2]
        
        if ema50 > 0 and ema20 > 0:
            zone_low = min(ema20, ema50)
            zone_high = max(ema20, ema50)
            
            in_zone = zone_low <= curr['high'] <= zone_high
            rejection = close < prev['close'] and close < ema20
            
            if in_zone and rejection:
                return {
                    'direction': 'short',
                    'sl_price': close + (atr * sl_mult),
                    'reason': 'pullback_rejection'
                }
        
        return None
    
    def _check_breakout_long(self, curr, prev, atr, close, sl_mult) -> Optional[Dict]:
        """Breakout long: Price breaks swing high with volume."""
        swing_high = curr.get('swing_high', close * 2)
        vol = curr.get('volume', 0)
        vol_ma = curr.get('vol_ma', vol)
        bb_width = curr.get('bb_width', 0)
        prev_bb_width = prev.get('bb_width', 0)
        
        # Conditions: break swing high + volume spike + BB expanding
        breakout = close > swing_high
        volume_confirm = vol > vol_ma * 1.5
        bb_expand = bb_width > prev_bb_width
        
        if breakout and volume_confirm and bb_expand:
            return {
                'direction': 'long',
                'sl_price': close - (atr * sl_mult),
                'reason': f'breakout(vol={vol/vol_ma:.1f}x)'
            }
        
        return None
    
    def _check_momentum_short(self, curr, prev, atr, close, sl_mult) -> Optional[Dict]:
        """Momentum short: Price breaks swing low + failed bounce."""
        swing_low = curr.get('swing_low', 0)
        rsi = curr.get('rsi', 50)
        
        # Conditions: break swing low + RSI not oversold yet (momentum continues)
        breakdown = close < swing_low
        not_oversold = rsi > 25  # Still room to fall
        bounce_failed = prev['close'] < prev.get('swing_low', close) and close < prev['close']
        
        if breakdown and not_oversold:
            return {
                'direction': 'short',
                'sl_price': close + (atr * sl_mult),
                'reason': f'momentum_short(rsi={rsi:.1f})'
            }
        
        return None
    
    def calculate_tp_levels(self, entry_price: float, direction: str, 
                           atr: float, settings: dict) -> list:
        """Calculate take profit levels based on regime settings."""
        tps = settings.get('tps', [])
        tp_levels = []
        
        for tp in tps:
            atr_mult = tp.get('atr_mult', 1.0)
            ratio = tp.get('ratio', 0.5)
            
            if direction == 'long':
                tp_price = entry_price + (atr * atr_mult)
            else:
                tp_price = entry_price - (atr * atr_mult)
            
            tp_levels.append({
                'price': tp_price,
                'ratio': ratio,
                'atr_mult': atr_mult
            })
        
        return tp_levels
    
    def check_exit_rules(self, position: dict, curr_row: dict, 
                        settings: dict) -> Optional[Dict]:
        """Check exit rules including shock_rule and exit_on_big_bull_candle."""
        
        # Shock rule: reduce 50% on big bear candle (UPTREND_HIGHVOL)
        if settings.get('shock_rule', {}).get('enabled', False):
            body_pct = curr_row.get('body_pct', 0)
            is_bearish = curr_row.get('is_bearish', False)
            
            if is_bearish and body_pct > 2.0:  # > 2% body = big bear
                return {
                    'action': 'partial_close',
                    'ratio': 0.5,
                    'reason': 'shock_rule_big_bear'
                }
        
        # Exit on big bull candle (DOWNTREND_HIGHVOL shorts)
        if settings.get('exit_on_big_bull_candle', False):
            body_pct = curr_row.get('body_pct', 0)
            is_bullish = curr_row.get('is_bullish', False)
            
            if position.get('side') == 'short' and is_bullish and body_pct > 2.0:
                return {
                    'action': 'full_close',
                    'reason': 'big_bull_candle_exit'
                }
        
        # Time stop
        time_stop = settings.get('time_stop_bars', 0)
        if time_stop > 0:
            bars_held = position.get('bars_held', 0)
            if bars_held >= time_stop:
                return {
                    'action': 'full_close',
                    'reason': f'time_stop({bars_held}_bars)'
                }
        
        return None
    
    def calculate_trailing_stop(self, position: dict, curr_row: dict,
                               settings: dict) -> Optional[float]:
        """Calculate trailing stop price if enabled."""
        trailing_cfg = settings.get('trailing', {})
        if not trailing_cfg.get('enabled', False):
            return None
        
        atr = curr_row.get('atr', 0)
        atr_mult = trailing_cfg.get('atr_mult', 1.2)
        
        side = position.get('side')
        entry = position.get('entry_price')
        highest = position.get('highest_price', entry)
        lowest = position.get('lowest_price', entry)
        close = curr_row['close']
        
        if side == 'long':
            # Update highest
            if close > highest:
                highest = close
            # Calculate trail
            trail_stop = highest - (atr * atr_mult)
            # Only return if better than entry (in profit territory)
            if trail_stop > entry:
                return trail_stop
        else:
            # Update lowest
            if close < lowest:
                lowest = close
            # Calculate trail
            trail_stop = lowest + (atr * atr_mult)
            if trail_stop < entry:
                return trail_stop
        
        return None
    
    def calculate_be_move(self, position: dict, curr_row: dict,
                         settings: dict) -> Optional[float]:
        """Calculate breakeven move if triggered."""
        be_atr = settings.get('be_move_atr', 0)
        if be_atr <= 0:
            return None
        
        atr = curr_row.get('atr', 0)
        entry = position.get('entry_price')
        side = position.get('side')
        close = curr_row['close']
        
        # Check if BE trigger is reached
        if side == 'long':
            trigger_price = entry + (atr * be_atr)
            if close >= trigger_price:
                return entry + (atr * 0.1)  # Small buffer above entry
        else:
            trigger_price = entry - (atr * be_atr)
            if close <= trigger_price:
                return entry - (atr * 0.1)
        
        return None
