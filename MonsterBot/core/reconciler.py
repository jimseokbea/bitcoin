import time
from .order_classifier import is_sl_order, is_tp_order

class RestReconciler:
    def __init__(self, executor, sl_replacer, state_store, logger):
        self.executor = executor
        self.sl = sl_replacer
        self.state = state_store
        self.logger = logger

    def reconcile_symbol(self, symbol: str):
        # 1) 포지션 스냅샷
        amt, side, entry = self.executor.fetch_real_position(symbol)

        # 2) 오픈오더 스냅샷
        orders = self.executor.exchange.fetch_open_orders(symbol)
        self.state.set_orders_snapshot(symbol, orders)

        sls = [o for o in orders if is_sl_order(o)]
        tps = [o for o in orders if is_tp_order(o)]

        # 케이스 A: 포지션 없음인데 SL/TP 남아있음 → SL만이라도 제거(요청사항: TP 유지가 원칙이지만 포지션 0이면 TP도 의미없음)
        if amt <= 0 and (sls or tps):
            if sls:
                self.logger.warning(f"🧟 [Zombie SL] {symbol} flat but SL exists. Cancel SL only.")
                self.sl.cancel_only_sl(symbol, sls)
            # TP까지 지우고 싶으면 여기서 별도 옵션으로 처리
            return

        # 케이스 B: 포지션 있는데 SL이 없음 → 최악(청산 리스크)
        if amt > 0 and not sls:
            self.logger.critical(f"🚨 [NO SL DETECTED] {symbol} position open but SL missing. Reinstall required.")
            # 원하는 정책: 즉시 “entry 기반”으로 SL 재설치하거나, 보수적으로 close_all
            # 여기서는 즉시 재설치 대신, 호출자에게 이벤트로 전달하도록 로깅만
            return

    def loop(self, symbols: list, interval_sec=15):
        while True:
            for sym in symbols:
                try:
                    self.reconcile_symbol(sym)
                except Exception as e:
                    self.logger.error(f"[Reconcile error] {sym} {e}")
            time.sleep(interval_sec)
