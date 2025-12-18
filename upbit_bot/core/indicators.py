import numpy as np
from typing import List, Optional, Dict, Any


def sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    arr = np.array(values[-period:])
    return float(arr.mean())


def rsi(values: List[float], period: int = 14) -> Optional[float]:
    if len(values) < period + 1:
        return None
    arr = np.array(values)
    deltas = np.diff(arr)
    ups = deltas.clip(min=0)
    downs = -deltas.clip(max=0)
    gain = np.mean(ups[-period:])
    loss = np.mean(downs[-period:])
    if loss == 0:
        return 100.0
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def vwap(candles: List[Dict[str, Any]]) -> float:
    closes = np.array([float(c["trade_price"]) for c in candles])
    vols = np.array([float(c["candle_acc_trade_volume"]) for c in candles])
    if vols.sum() == 0:
        return closes[-1]
    return float((closes * vols).sum() / vols.sum())
