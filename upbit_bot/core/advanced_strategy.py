from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from .indicators import vwap as vwap_calc
from .sentiment_client import SentimentClient


@dataclass
class TrendConfig:
    enabled: bool
    ema_span: int
    rsi_period: int
    min_rsi: float

@dataclass
class VolumeConfig:
    enabled: bool
    spike_ratio: float

@dataclass
class VolBreakoutConfig:
    enabled: bool
    k: float
    use_daily_candle: bool
    min_range_ratio: float


@dataclass
class VWAPConfig:
    enabled: bool
    session_minutes: int
    max_deviation: float
    trend_filter: str


@dataclass
class PatternConfig:
    enabled: bool
    use_bullish_engulfing: bool
    use_hammer: bool
    min_body_ratio: float
    min_tail_ratio: float


@dataclass
class SentimentConfig:
    enabled: bool
    min_score: float
    use_global_news: bool
    cache_minutes: int


@dataclass
class AdvancedStrategyConfig:
    trend: TrendConfig
    volume: VolumeConfig
    vol_breakout: VolBreakoutConfig
    vwap: VWAPConfig
    patterns: PatternConfig
    sentiment: SentimentConfig
    take_profit: float
    stop_loss: float
    hard_stop_loss: float
    trailing_start: float
    trailing_gap: float
    
    # New Optimization Fields
    use_split_tp: bool
    use_atr_sl: bool
    atr_period: int
    atr_multiplier: float

    @staticmethod
    def from_yaml(cfg: Dict[str, Any]) -> "AdvancedStrategyConfig":
        return AdvancedStrategyConfig(
            trend=TrendConfig(**cfg.get("trend", {"enabled": False, "ema_span": 20, "rsi_period": 14, "min_rsi": 50})),
            volume=VolumeConfig(**cfg.get("volume", {"enabled": False, "spike_ratio": 2.0})),
            vol_breakout=VolBreakoutConfig(**cfg["vol_breakout"]),
            vwap=VWAPConfig(**cfg["vwap"]),
            patterns=PatternConfig(**cfg["patterns"]),
            sentiment=SentimentConfig(**cfg["sentiment"]),
            take_profit=cfg["take_profit"],
            stop_loss=cfg["stop_loss"],
            hard_stop_loss=cfg["hard_stop_loss"],
            trailing_start=cfg["trailing_start"],
            trailing_gap=cfg["trailing_gap"],
            use_split_tp=cfg.get("use_split_tp", False),
            use_atr_sl=cfg.get("use_atr_sl", False),
            atr_period=cfg.get("atr_period", 14),
            atr_multiplier=cfg.get("atr_multiplier", 0.8),
        )


class AdvancedStrategy:
    """
    Refactored Strategy (Optimized):
    1. Market Regime (1H Trend): EMA20 & RSI > 50
    2. Volume Spike: Current Vol > Avg Vol * 2.0
    3. Relaxed VWAP: Price > VWAP (within 3%)
    4. Breakout: Price > Recent High (or relaxed Volatility Breakout)
    
    Optimizations:
    - Dynamic SL (ATR based)
    - Split TP (TP1 50%, TP2 Trailing)
    """

    def __init__(self, config: AdvancedStrategyConfig, sentiment_client: SentimentClient = None):
        self.cfg = config
        self.sentiment_client = sentiment_client
        self.position: Optional[Dict[str, Any]] = None
        self.highest_price_after_entry: Optional[float] = None
        self.tp1_hit: bool = False # Track if TP1 has been triggered

    # ---------- Indicators Helper ----------
    def _calc_ema(self, prices: List[float], span: int) -> float:
        if not prices:
            return 0.0
        alpha = 2 / (span + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = (p * alpha) + (ema * (1 - alpha))
        return ema

    def _calc_rsi(self, prices: List[float], period: int) -> float:
        if len(prices) < period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            if diff >= 0:
                gains.append(diff)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(diff))
        recent_gains = gains[-period:]
        recent_losses = losses[-period:]
        avg_gain = sum(recent_gains) / period
        avg_loss = sum(recent_losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calc_atr(self, candles: List[Dict[str, Any]], period: int) -> float:
        if len(candles) < period + 1:
            return 0.0
        
        tr_list = []
        for i in range(1, len(candles)):
            h = float(candles[i]["high_price"])
            l = float(candles[i]["low_price"])
            prev_c = float(candles[i-1]["trade_price"])
            
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)
            
        if not tr_list:
            return 0.0
            
        # Simple Average TR for now (Wilder's is better but this is sufficient for 5m)
        recent_tr = tr_list[-period:]
        return sum(recent_tr) / len(recent_tr)

    # ---------- 1. Market Regime (1H Trend) ----------
    def _check_trend(self, hourly_candles: List[Dict[str, Any]]) -> bool:
        if not self.cfg.trend.enabled or not hourly_candles:
            return True
        closes = [float(c["trade_price"]) for c in hourly_candles]
        if len(closes) < self.cfg.trend.ema_span:
            return True 
        current_close = closes[-1]
        ema = self._calc_ema(closes, self.cfg.trend.ema_span)
        if current_close < ema:
            return False
        rsi = self._calc_rsi(closes, self.cfg.trend.rsi_period)
        if rsi < self.cfg.trend.min_rsi:
            return False
        return True

    # ---------- 2. Volume Spike ----------
    def _check_volume(self, candles: List[Dict[str, Any]]) -> bool:
        if not self.cfg.volume.enabled or len(candles) < 20:
            return True
        current_vol = float(candles[-1]["candle_acc_trade_volume"])
        prev_vols = [float(c["candle_acc_trade_volume"]) for c in candles[-21:-1]]
        if not prev_vols:
            return True
        avg_vol = sum(prev_vols) / len(prev_vols)
        if avg_vol == 0:
            return True
        return current_vol >= avg_vol * self.cfg.volume.spike_ratio

    # ---------- 3. VWAP Filter (Relaxed) ----------
    def _vwap_filter(self, intraday_candles: Optional[List[Dict[str, Any]]], current_price: float) -> bool:
        if not self.cfg.vwap.enabled or not intraday_candles:
            return True
        v = vwap_calc(intraday_candles)
        dev = abs(current_price - v) / v
        if dev > self.cfg.vwap.max_deviation:
            return False
        if self.cfg.vwap.trend_filter == "above":
            return current_price >= v
        elif self.cfg.vwap.trend_filter == "below":
            return current_price <= v
        return True

    # ---------- 4. Breakout (Relaxed) ----------
    def _volatility_breakout_long(self, daily_prev: Optional[Dict[str, Any]], current_price: float) -> bool:
        if not self.cfg.vol_breakout.enabled or daily_prev is None:
            return True
        prev_close = float(daily_prev["trade_price"])
        prev_high = float(daily_prev["high_price"])
        prev_low = float(daily_prev["low_price"])
        rng = prev_high - prev_low
        if rng <= 0:
            return False
        range_ratio = rng / prev_close
        if range_ratio < self.cfg.vol_breakout.min_range_ratio:
            return False
        breakout_price = prev_close + self.cfg.vol_breakout.k * rng
        return current_price > breakout_price

    # ---------- 5. Candle Patterns (Optional) ----------
    def _is_bullish_engulfing(self, prev: Dict[str, Any], curr: Dict[str, Any]) -> bool:
        o1, c1 = float(prev["opening_price"]), float(prev["trade_price"])
        o2, c2 = float(curr["opening_price"]), float(curr["trade_price"])
        return (c1 < o1 and c2 > o2 and o2 <= c1 and c2 > o1)

    def _is_hammer(self, curr: Dict[str, Any]) -> bool:
        o = float(curr["opening_price"])
        h = float(curr["high_price"])
        l = float(curr["low_price"])
        c = float(curr["trade_price"])
        body = abs(c - o)
        total = h - l
        lower_tail = min(o, c) - l
        if total == 0: return False
        body_ratio = body / max(c, 1)
        tail_ratio = lower_tail / max(body, 1e-9)
        return (body_ratio >= self.cfg.patterns.min_body_ratio and tail_ratio >= self.cfg.patterns.min_tail_ratio and c > o)

    def _pattern_ok(self, prev: Dict[str, Any], curr: Dict[str, Any]) -> bool:
        if not self.cfg.patterns.enabled:
            return True
        ok = False
        if self.cfg.patterns.use_bullish_engulfing:
            ok |= self._is_bullish_engulfing(prev, curr)
        if self.cfg.patterns.use_hammer:
            ok |= self._is_hammer(curr)
        return ok

    # ---------- Main Signal ----------
    def generate_signal(
        self,
        closes: List[float],
        current_candle: Dict[str, Any],
        prev_candle: Dict[str, Any],
        daily_prev: Optional[Dict[str, Any]] = None,
        intraday_candles: Optional[List[Dict[str, Any]]] = None,
        hourly_candles: Optional[List[Dict[str, Any]]] = None,
    ) -> (str, str): 
        price = float(current_candle["trade_price"])

        # ---- ENTRY ----
        if self.position is None:
            self.tp1_hit = False # Reset TP1 state

            # 1. Market Regime (1H Trend)
            if not self._check_trend(hourly_candles):
                return "HOLD", "Weak Trend (1H EMA/RSI)"
            
            # 2. VWAP Filter
            if not self._vwap_filter(intraday_candles, price):
                return "HOLD", "Below VWAP"
                
            # 3. Volume Spike
            if not self._check_volume(intraday_candles):
                 return "HOLD", "Low Volume"

            # 4. Breakout
            if not self._volatility_breakout_long(daily_prev, price):
                return "HOLD", "No Breakout"
                
            self.highest_price_after_entry = price
            return "BUY", "Signal Generated"

        # ---- EXIT ----
        entry_price = self.position["entry_price"]
        pnl = (price - entry_price) / entry_price

        # 1. Hard Stop Loss
        if pnl <= -self.cfg.hard_stop_loss: 
            return "SELL", "Hard Stop Loss"

        # 2. Dynamic Stop Loss (ATR)
        stop_loss_pct = self.cfg.stop_loss
        if self.cfg.use_atr_sl and intraday_candles:
            # Calculate ATR based SL
            atr = self._calc_atr(intraday_candles, self.cfg.atr_period)
            if atr > 0:
                atr_pct = atr / price
                # SL = max(1.8%, 0.8 * ATR%)
                stop_loss_pct = max(self.cfg.stop_loss, self.cfg.atr_multiplier * atr_pct)
        
        if pnl <= -stop_loss_pct:
            return "SELL", f"Stop Loss (Dynamic: {stop_loss_pct*100:.2f}%)"

        # 3. Split Take Profit (TP1)
        if self.cfg.use_split_tp and not self.tp1_hit:
            if pnl >= self.cfg.take_profit:
                self.tp1_hit = True
                return "SELL_PARTIAL", "TP1 Hit (50%)"

        # 4. Trailing Stop (TP2) - Active after TP1 or if Trailing Start reached
        # If Split TP is enabled, we only trail AFTER TP1 (or if price goes very high)
        # But user wants "TP2 = Trailing stop". So after TP1, we just trail.
        # Or if we haven't hit TP1 yet but price shoots up to trailing_start (3.5%), we should also trail?
        # Let's simple logic: If PnL >= Trailing Start, activate trailing.
        
        if pnl >= self.cfg.trailing_start:
            self.highest_price_after_entry = max(self.highest_price_after_entry or price, price)
            drawdown = (price - self.highest_price_after_entry) / self.highest_price_after_entry
            if drawdown <= -self.cfg.trailing_gap:
                return "SELL", "Trailing Stop"

        # If Split TP is NOT enabled, behave like old logic
        if not self.cfg.use_split_tp:
             if pnl >= self.cfg.take_profit: return "SELL", "Take Profit"

        return "HOLD", "Holding Position"
