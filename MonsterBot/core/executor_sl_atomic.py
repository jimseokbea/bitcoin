import time
from .order_classifier import is_sl_order

class SLAtomicReplacer:
    def __init__(self, executor, filters_parser, state_store, logger):
        self.executor = executor          # ccxt wrapper
        self.filters = filters_parser     # BinanceFuturesFilters
        self.state = state_store
        self.logger = logger

    def _fetch_position_rest(self, symbol):
        # executor는 ccxt 기반 fetch_positions를 제공한다고 가정
        amt, side, entry = self.executor.fetch_real_position(symbol)
        return float(amt), side, float(entry)

    def _fetch_open_orders_rest(self, symbol):
        orders = self.executor.exchange.fetch_open_orders(symbol)
        self.state.set_orders_snapshot(symbol, orders)
        return orders

    def cancel_only_sl(self, symbol, sl_orders: list):
        cancelled = 0
        for o in sl_orders:
            oid = o.get("id")
            if not oid:
                continue
            try:
                self.executor.exchange.cancel_order(oid, symbol)
                cancelled += 1
            except Exception as e:
                self.logger.error(f"[Cancel SL Error] {symbol} id={oid} {e}")
        if cancelled:
            self.logger.info(f"🧹 [Cancel SL Only] {symbol} cancelled={cancelled}")
        return cancelled

    def place_new_sl(self, symbol, direction: str, qty: float, stop_price: float):
        # 방향: LONG이면 sell 스탑, SHORT면 buy 스탑
        side = "sell" if direction == "LONG" else "buy"

        # 필터/틱 적용
        meta = self.filters.parse(symbol)
        stop_price = self.filters.floor_to_tick(stop_price, meta["tick"], meta["price_precision"])
        qty = self.filters.floor_to_step(qty, meta["step"], meta["amount_precision"])

        if qty <= 0:
            return None, "qty<=0 after normalize"

        # 노셔널 최소값 검사(가능하면)
        # 여기서는 best-effort: 현재가를 executor에서 얻었다고 가정
        try:
             last = self.executor.exchange.fetch_ticker(symbol).get("last") or 0
        except: 
             last = 0
             
        if last and not self.filters.validate_notional(qty, float(last), meta["min_cost"]):
            return None, "min_notional fail"

        params = {
            "stopPrice": float(stop_price),
            "reduceOnly": True,
            "workingType": "MARK_PRICE",
        }
        try:
            o = self.executor.exchange.create_order(symbol, "STOP_MARKET", side, qty, None, params)
            return o, "OK"
        except Exception as e:
            return None, str(e)

    def replace_sl_only_atomic(self, symbol: str, direction: str, desired_sl_price: float):
        """
        핵심 함수: TP 유지 + SL만 교체
        """
        # 0) REST로 포지션 재확인(anti-ghost)
        amt, side, _ = self._fetch_position_rest(symbol)
        if amt <= 0:
            # 포지션 없으면, 혹시 남아있는 SL만 정리 (TP는 원칙상 포지션 없으면 의미 없음)
            self.logger.warning(f"⚠️ [SL Replace Skip] flat position: {symbol}")
            orders = self._fetch_open_orders_rest(symbol)
            sls = [o for o in orders if is_sl_order(o)]
            if sls:
                self.cancel_only_sl(symbol, sls)
            return None

        actual_dir = "LONG" if side == "long" else "SHORT"
        if direction != actual_dir:
            self.logger.warning(f"⚠️ [DirMismatch] req={direction}, actual={actual_dir}. Use actual.")
            direction = actual_dir

        # 1) REST로 오픈오더 스냅샷
        orders = self._fetch_open_orders_rest(symbol)
        existing_sls = [o for o in orders if is_sl_order(o)]

        # 2) 새 SL 먼저 발행 (보호 공백 최소화)
        new_sl, msg = self.place_new_sl(symbol, direction, amt, desired_sl_price)
        if not new_sl:
            self.logger.error(f"🚨 [SL Replace Fail] {symbol} cannot place new SL: {msg}")
            return None

        new_id = str(new_sl.get("id"))
        self.logger.info(f"🧷 [SL Placed First] {symbol} newSL={desired_sl_price} id={new_id}")

        # 3) 새 SL이 실제로 오픈오더에 잡혔는지 확인 (REST 1~2회 재확인)
        confirmed = False
        for _ in range(2):
            time.sleep(0.2)
            chk = self._fetch_open_orders_rest(symbol)
            if any(str(o.get("id")) == new_id for o in chk):
                confirmed = True
                break
        if not confirmed:
            self.logger.warning(f"⚠️ [SL Not Confirmed Yet] {symbol} id={new_id} (continue anyway)")

        # 4) 기존 SL만 취소 (TP 유지)
        if existing_sls:
            self.cancel_only_sl(symbol, existing_sls)

        self.logger.info(f"✅ [SL Atomic Replace Done] {symbol} kept_TP=True oldSL={len(existing_sls)}")
        return new_sl
