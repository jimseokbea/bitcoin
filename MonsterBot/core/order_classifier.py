# order_classifier.py
SL_TYPES = {"STOP_MARKET", "STOP", "STOP_LOSS", "STOP_LOSS_LIMIT"}
TP_TYPES = {"TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT"}

def order_type_upper(o: dict) -> str:
    t = (o.get("type") or "").upper()
    if t:
        return t
    info = o.get("info") or {}
    it = str(info.get("type", "")).upper()
    return it

def is_sl_order(o: dict) -> bool:
    t = order_type_upper(o)
    if t in SL_TYPES:
        return True
    info = o.get("info") or {}
    # stopPrice가 있는데 TP계열이 아니면 SL로 보는 best-effort
    if info.get("stopPrice") is not None and t not in TP_TYPES:
        return True
    return False

def is_tp_order(o: dict) -> bool:
    t = order_type_upper(o)
    if t in TP_TYPES:
        return True
    return False
