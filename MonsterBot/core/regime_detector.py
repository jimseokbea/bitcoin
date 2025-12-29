"""
Multi-Coin Regime Detector (1h)
2-Tier Structure: Market Regime (BTC) + Symbol Regime (per coin)

Safeguards:
- (A) Regime transition stabilization (confirm_bars, min_hold_bars)
- (C) ATR% thresholds split by coin group
- (H) Comprehensive logging
"""
import pandas as pd
import numpy as np
from datetime import datetime
from .utils import get_logger

logger = get_logger()

# Regime Constants
REGIME_RANGE_LOWVOL = "RANGE_LOWVOL"
REGIME_RANGE_HIGHVOL = "RANGE_HIGHVOL"
REGIME_UPTREND_LOWVOL = "UPTREND_LOWVOL"
REGIME_UPTREND_HIGHVOL = "UPTREND_HIGHVOL"
REGIME_DOWNTREND_LOWVOL = "DOWNTREND_LOWVOL"
REGIME_DOWNTREND_HIGHVOL = "DOWNTREND_HIGHVOL"

# Market Gate Status
MARKET_GATE_NORMAL = "NORMAL"
MARKET_GATE_RISKOFF = "RISKOFF"
MARKET_GATE_PANIC = "PANIC"


class RegimeDetector:
    """
    2-Tier regime detection:
    1. Market Regime: BTC-based overall market state
    2. Symbol Regime: Per-coin regime detection
    """
    
    def __init__(self, config: dict):
        self.config = config
        rd_cfg = config.get('regime_detector', {})
        
        # ADX params
        adx_cfg = rd_cfg.get('adx', {})
        self.adx_period = adx_cfg.get('period', 14)
        self.adx_trend_on = adx_cfg.get('trend_on', 23)
        self.adx_trend_off = adx_cfg.get('trend_off', 19)
        
        # ATR params (split by coin group - Safeguard C)
        atr_cfg = rd_cfg.get('atr', {})
        self.atr_period = atr_cfg.get('period', 14)
        # Default thresholds
        self.atr_high_vol_on = atr_cfg.get('high_vol_on', 1.6)
        self.atr_high_vol_off = atr_cfg.get('high_vol_off', 1.2)
        # Major coin thresholds (BTC/ETH - lower variance)
        self.atr_major_high_vol_on = atr_cfg.get('major_high_vol_on', 1.4)
        self.atr_major_high_vol_off = atr_cfg.get('major_high_vol_off', 1.0)
        # Alt coin thresholds (higher variance expected)
        self.atr_alt_high_vol_on = atr_cfg.get('alt_high_vol_on', 2.0)
        self.atr_alt_high_vol_off = atr_cfg.get('alt_high_vol_off', 1.5)
        
        # EMA params
        trend_cfg = rd_cfg.get('trend_direction', {})
        self.ema_fast = trend_cfg.get('ema_fast', 50)
        self.ema_slow = trend_cfg.get('ema_slow', 200)
        self.slope_lookback = trend_cfg.get('slope_lookback', 5)
        
        # Stabilization params (Safeguard A)
        stab_cfg = rd_cfg.get('stabilization', {})
        self.confirm_bars = stab_cfg.get('confirm_bars', 2)
        self.min_hold_bars = stab_cfg.get('min_hold_bars', 6)
        
        # Panic override
        panic_cfg = stab_cfg.get('panic_override', {})
        self.panic_override_enabled = panic_cfg.get('enable', True)
        self.panic_regime = panic_cfg.get('regime', REGIME_DOWNTREND_HIGHVOL)
        self.panic_confirm_bars = panic_cfg.get('confirm_bars', 1)
        
        # Major coins list (for ATR threshold split)
        self.major_coins = ['BTC/USDT', 'ETH/USDT', 'BTCUSDT', 'ETHUSDT']
        
        # State tracking per symbol
        self._regime_state = {}  # symbol -> {current_regime, pending_regime, confirm_count, hold_count}
        
        # Market Gate state
        self._market_gate = MARKET_GATE_NORMAL
        self._market_gate_hold_count = 0
        
        logger.info(f"🎯 RegimeDetector initialized (ADX: {self.adx_trend_on}/{self.adx_trend_off}, "
                   f"ATR%: {self.atr_high_vol_on}/{self.atr_high_vol_off}, "
                   f"Confirm: {self.confirm_bars}, Hold: {self.min_hold_bars})")
    
    def _get_atr_thresholds(self, symbol: str) -> tuple:
        """Get ATR% thresholds based on coin group (Safeguard C)"""
        is_major = any(m in symbol.upper() for m in ['BTC', 'ETH'])
        if is_major:
            return self.atr_major_high_vol_on, self.atr_major_high_vol_off
        else:
            return self.atr_alt_high_vol_on, self.atr_alt_high_vol_off
    
    def add_regime_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all indicators needed for regime detection"""
        if df is None or len(df) < max(self.ema_slow, 50):
            return df
        
        try:
            close = df['close']
            high = df['high']
            low = df['low']
            
            # 1. EMA50 and EMA200
            df['ema_fast'] = close.ewm(span=self.ema_fast, adjust=False).mean()
            df['ema_slow'] = close.ewm(span=self.ema_slow, adjust=False).mean()
            
            # 2. EMA50 Slope (5-bar lookback)
            df['ema_fast_slope'] = df['ema_fast'].diff(self.slope_lookback)
            
            # 3. ATR
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            df['atr'] = tr.rolling(window=self.atr_period).mean()
            
            # 4. ATR% (Safeguard C - normalized by close)
            df['atr_pct'] = (df['atr'] / close) * 100
            
            # 5. ADX
            plus_dm = high.diff()
            minus_dm = low.diff()
            plus_dm = plus_dm.where(plus_dm > 0, 0)
            minus_dm = minus_dm.where(minus_dm < 0, 0).abs()
            
            tr_sum = tr.rolling(window=self.adx_period).sum()
            plus_di = 100 * (plus_dm.rolling(window=self.adx_period).sum() / tr_sum)
            minus_di = 100 * (minus_dm.rolling(window=self.adx_period).sum() / tr_sum)
            
            dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
            df['adx'] = dx.rolling(window=self.adx_period).mean()
            
            return df
            
        except Exception as e:
            logger.error(f"Regime indicator error: {e}")
            return df
    
    def _detect_raw_regime(self, df: pd.DataFrame, symbol: str) -> str:
        """Detect raw regime without stabilization"""
        if df is None or len(df) < 2:
            return REGIME_RANGE_LOWVOL
        
        curr = df.iloc[-1]
        
        # Get values
        adx = curr.get('adx', 0)
        atr_pct = curr.get('atr_pct', 0)
        ema_fast = curr.get('ema_fast', 0)
        ema_slow = curr.get('ema_slow', 0)
        ema_slope = curr.get('ema_fast_slope', 0)
        
        # Get ATR thresholds for this symbol
        high_vol_on, high_vol_off = self._get_atr_thresholds(symbol)
        
        # Determine trend state (with hysteresis)
        state = self._regime_state.get(symbol, {})
        prev_trend = state.get('trend_flag', False)
        
        if adx >= self.adx_trend_on:
            trend_on = True
        elif adx <= self.adx_trend_off:
            trend_on = False
        else:
            trend_on = prev_trend  # Hysteresis
        
        # Determine volatility state (with hysteresis)
        prev_high_vol = state.get('high_vol_flag', False)
        
        if atr_pct >= high_vol_on:
            high_vol = True
        elif atr_pct <= high_vol_off:
            high_vol = False
        else:
            high_vol = prev_high_vol  # Hysteresis
        
        # Determine direction
        if ema_fast > ema_slow and ema_slope > 0:
            direction = 'up'
        elif ema_fast < ema_slow and ema_slope < 0:
            direction = 'down'
        else:
            direction = 'range'
        
        # Save flags for hysteresis
        if symbol not in self._regime_state:
            self._regime_state[symbol] = {}
        self._regime_state[symbol]['trend_flag'] = trend_on
        self._regime_state[symbol]['high_vol_flag'] = high_vol
        
        # Determine regime
        if not trend_on:
            # Range
            if high_vol:
                return REGIME_RANGE_HIGHVOL
            else:
                return REGIME_RANGE_LOWVOL
        else:
            # Trending
            if direction == 'up':
                if high_vol:
                    return REGIME_UPTREND_HIGHVOL
                else:
                    return REGIME_UPTREND_LOWVOL
            elif direction == 'down':
                if high_vol:
                    return REGIME_DOWNTREND_HIGHVOL
                else:
                    return REGIME_DOWNTREND_LOWVOL
            else:
                # Trending but no clear direction
                if high_vol:
                    return REGIME_RANGE_HIGHVOL
                else:
                    return REGIME_RANGE_LOWVOL
    
    def detect_symbol_regime(self, df: pd.DataFrame, symbol: str) -> tuple:
        """
        Detect regime for a symbol with stabilization (Safeguard A).
        Returns: (regime, is_confirmed, indicator_values)
        """
        raw_regime = self._detect_raw_regime(df, symbol)
        
        if symbol not in self._regime_state:
            self._regime_state[symbol] = {
                'current_regime': raw_regime,
                'pending_regime': None,
                'confirm_count': 0,
                'hold_count': 0,
                'trend_flag': False,
                'high_vol_flag': False
            }
        
        state = self._regime_state[symbol]
        current = state['current_regime']
        
        # Get current indicator values for logging (Safeguard H)
        curr = df.iloc[-1] if df is not None and len(df) > 0 else {}
        indicators = {
            'adx': curr.get('adx', 0),
            'atr_pct': curr.get('atr_pct', 0),
            'ema_fast': curr.get('ema_fast', 0),
            'ema_slow': curr.get('ema_slow', 0),
            'ema_slope': curr.get('ema_fast_slope', 0)
        }
        
        # Check if regime changed
        if raw_regime != current:
            # Determine confirm bars needed
            if self.panic_override_enabled and raw_regime == self.panic_regime:
                required_confirm = self.panic_confirm_bars  # Fast transition for panic
            else:
                required_confirm = self.confirm_bars
            
            # Check if this is a new pending regime or continuation
            if state['pending_regime'] == raw_regime:
                state['confirm_count'] += 1
            else:
                state['pending_regime'] = raw_regime
                state['confirm_count'] = 1
            
            # Check if confirmed
            if state['confirm_count'] >= required_confirm:
                # Check min_hold constraint
                if state['hold_count'] >= self.min_hold_bars:
                    old_regime = current
                    state['current_regime'] = raw_regime
                    state['pending_regime'] = None
                    state['confirm_count'] = 0
                    state['hold_count'] = 0
                    
                    logger.info(f"🔄 [Regime] {symbol}: {old_regime} → {raw_regime} "
                               f"(ADX:{indicators['adx']:.1f}, ATR%:{indicators['atr_pct']:.2f})")
                    return raw_regime, True, indicators
                else:
                    # Still in hold period
                    state['hold_count'] += 1
                    return current, False, indicators
            else:
                state['hold_count'] += 1
                return current, False, indicators
        else:
            # Same regime, reset pending
            state['pending_regime'] = None
            state['confirm_count'] = 0
            state['hold_count'] += 1
            return current, True, indicators
    
    def detect_market_regime(self, btc_df: pd.DataFrame) -> tuple:
        """
        Detect BTC-based Market Regime (Market Gate).
        Returns: (gate_status, btc_regime, indicators)
        """
        btc_regime, is_confirmed, indicators = self.detect_symbol_regime(btc_df, 'BTC/USDT')
        
        # Determine market gate status
        if btc_regime == REGIME_DOWNTREND_HIGHVOL:
            new_gate = MARKET_GATE_PANIC
        elif btc_regime == REGIME_RANGE_HIGHVOL:
            new_gate = MARKET_GATE_RISKOFF
        elif btc_regime == REGIME_DOWNTREND_LOWVOL:
            new_gate = MARKET_GATE_RISKOFF
        else:
            new_gate = MARKET_GATE_NORMAL
        
        # Gate transition with hold (prevent flapping)
        if new_gate != self._market_gate:
            self._market_gate_hold_count += 1
            if self._market_gate_hold_count >= 2:  # Require 2 bars for gate change
                old_gate = self._market_gate
                self._market_gate = new_gate
                self._market_gate_hold_count = 0
                
                logger.warning(f"🚨 [Market Gate] {old_gate} → {new_gate} "
                              f"(BTC: {btc_regime}, ADX:{indicators['adx']:.1f})")
        else:
            self._market_gate_hold_count = 0
        
        return self._market_gate, btc_regime, indicators
    
    def get_regime_settings(self, regime: str, market_gate: str) -> dict:
        """
        Get trading settings for a regime, modified by market gate.
        Safeguard B: Market gate affects existing positions too.
        """
        settings_cfg = self.config.get('settings_by_regime', {})
        base_settings = settings_cfg.get(regime, {}).copy()
        
        if not base_settings:
            # Default safe settings
            base_settings = {
                'trade_enabled': False,
                'mode': 'protect',
                'position_scale': 0.35,
                'leverage_cap': 3
            }
        
        # Apply Market Gate modifications (Safeguard B)
        gate_cfg = self.config.get('market_gate', {})
        
        if market_gate == MARKET_GATE_PANIC:
            panic_actions = gate_cfg.get('panic_actions', {})
            base_settings['position_scale'] *= panic_actions.get('position_scale_mult', 0.5)
            base_settings['leverage_cap'] = min(
                base_settings.get('leverage_cap', 5),
                panic_actions.get('leverage_cap', 2)
            )
            # Disable new longs in panic
            if base_settings.get('mode', '').endswith('_short') is False:
                base_settings['new_long_enabled'] = panic_actions.get('new_long_enabled', False)
            # Force protective actions on existing positions
            base_settings['force_trailing_tighten'] = True
            base_settings['force_sl_tighten_mult'] = 0.8
            
        elif market_gate == MARKET_GATE_RISKOFF:
            riskoff_actions = gate_cfg.get('riskoff_actions', {})
            base_settings['position_scale'] *= riskoff_actions.get('position_scale_mult', 0.7)
        
        return base_settings
    
    def get_state_summary(self) -> dict:
        """Get current state for logging/debugging (Safeguard H)"""
        return {
            'market_gate': self._market_gate,
            'symbol_regimes': {
                sym: {
                    'regime': state.get('current_regime'),
                    'pending': state.get('pending_regime'),
                    'hold_count': state.get('hold_count', 0)
                }
                for sym, state in self._regime_state.items()
            }
        }
