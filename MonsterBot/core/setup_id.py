import hashlib
import json
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class SetupContext:
    symbol: str
    tf: str               # e.g. "5m"
    signal_type: str      # e.g. "LONG_SQUEEZE", "SHORT_RSI_REJECT"
    bar_ts_ms: int        # "confirmed bar" timestamp in ms (closed candle)
    entry_ref: float      # usually close of confirmed bar (entry at next open in backtest/real)
    adx: float
    rsi: float
    bb_width: float
    vol_spike_mult: float
    extras: Optional[dict] = None  # e.g. {"pattern":"hammer", "btc_fuse":False}

def _round(x: float, nd=6) -> float:
    try:
        return round(float(x), nd)
    except Exception:
        return 0.0

def make_setup_fingerprint(ctx: SetupContext) -> str:
    """
    Stable setup fingerprint for:
    - de-duplicating repeated signals
    - allowing re-entry only on NEW setups
    """
    payload = {
        "symbol": ctx.symbol,
        "tf": ctx.tf,
        "signal_type": ctx.signal_type,
        # bar timestamp anchors the setup to the "confirmed candle"
        "bar_ts_ms": int(ctx.bar_ts_ms),
        # quantize to avoid micro noise differences
        "entry_ref": _round(ctx.entry_ref, 6),
        "adx": _round(ctx.adx, 3),
        "rsi": _round(ctx.rsi, 3),
        "bb_width": _round(ctx.bb_width, 6),
        "vol_spike_mult": _round(ctx.vol_spike_mult, 3),
        "extras": ctx.extras or {},
    }
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    # shorter is easier in logs
    return f"{ctx.signal_type}:{h[:16]}"

def make_setup_fingerprint_from_df(symbol: str, tf: str, signal_type: str, df, extras=None) -> str:
    """
    df must contain latest closed candle info. Use iloc[-2] if df includes forming candle.
    Recommended: generate from the "confirmed candle" not the in-progress candle.
    """
    # Choose the "confirmed bar" row
    # If your df is OHLCV with last row potentially still forming:
    row = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]

    # timestamp column naming differs by your implementation
    # Support 'timestamp' (ms) OR index being datetime-like is up to you
    # Assuming 'timestamp' column exists based on previous code
    bar_ts_ms = int(row.get("timestamp", 0)) if "timestamp" in row else 0
    
    close = float(row["close"])
    adx = float(row.get("adx", 20.0)) if "adx" in df.columns else 20.0
    rsi = float(row.get("rsi", 50.0)) if "rsi" in df.columns else 50.0

    # bb_width computed from bb_upper/lower/middle if present; else fallback to 0
    if all(c in df.columns for c in ("bb_upper", "bb_lower", "bb_middle")):
        mid = float(row["bb_middle"]) if float(row["bb_middle"]) != 0 else 1e-9
        bb_width = (float(row["bb_upper"]) - float(row["bb_lower"])) / mid
    else:
        bb_width = 0.0

    vol_spike_mult = float(row.get("vol_spike_mult", 0.0))  # if you store it, else 0

    ctx = SetupContext(
        symbol=symbol,
        tf=tf,
        signal_type=signal_type,
        bar_ts_ms=bar_ts_ms,
        entry_ref=close,
        adx=adx,
        rsi=rsi,
        bb_width=bb_width,
        vol_spike_mult=vol_spike_mult,
        extras=extras or {},
    )
    return make_setup_fingerprint(ctx)
