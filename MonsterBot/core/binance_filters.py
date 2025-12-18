import math
from decimal import Decimal, ROUND_DOWN

class BinanceFuturesFilters:
    """
    Binance Futures market.info.filters 기반 필터 파서
    """
    def __init__(self, ccxt_exchange):
        self.ex = ccxt_exchange

    def parse(self, symbol: str) -> dict:
        try:
            m = self.ex.market(symbol)
        except Exception:
            # Fallback if market not found (should be loaded)
            return {
                "step": None, "min_qty": 0.0, "tick": None,
                "min_cost": 0.0, "amount_precision": 0, "price_precision": 0
            }

        info = m.get("info") or {}
        filters = info.get("filters") or []

        lot = {}
        price = {}
        min_notional = {}

        for f in filters:
            t = f.get("filterType")
            if t in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                lot = f
            elif t == "PRICE_FILTER":
                price = f
            elif t in ("MIN_NOTIONAL",):
                min_notional = f

        step = float(lot.get("stepSize") or 0) or None
        min_qty = float(lot.get("minQty") or 0) or 0.0
        tick = float(price.get("tickSize") or 0) or None

        # Futures는 minNotional이 항상 제공되지 않을 수 있음 (best-effort)
        min_cost = float(min_notional.get("notional") or min_notional.get("minNotional") or 0) or 0.0

        precision = m.get("precision") or {}
        amount_prec = int(precision.get("amount") or 0)
        price_prec = int(precision.get("price") or 0)

        return {
            "step": step,
            "min_qty": min_qty,
            "tick": tick,
            "min_cost": min_cost,
            "amount_precision": amount_prec,
            "price_precision": price_prec,
        }

    def floor_to_step(self, qty: float, step: float | None, amount_precision: int) -> float:
        if step and step > 0:
            qty = math.floor(qty / step) * step

        if amount_precision and amount_precision > 0:
            q = Decimal(str(qty))
            quant = Decimal("1e-" + str(amount_precision))
            qty = float(q.quantize(quant, rounding=ROUND_DOWN))
        else:
            qty = float(int(qty))
        return max(qty, 0.0)

    def floor_to_tick(self, price: float, tick: float | None, price_precision: int) -> float:
        if tick and tick > 0:
            price = math.floor(price / tick) * tick

        if price_precision and price_precision > 0:
            p = Decimal(str(price))
            quant = Decimal("1e-" + str(price_precision))
            price = float(p.quantize(quant, rounding=ROUND_DOWN))
        return max(price, 0.0)

    def validate_notional(self, qty: float, price: float, min_cost: float) -> bool:
        if min_cost and min_cost > 0:
            return (qty * price) >= min_cost
        return True
