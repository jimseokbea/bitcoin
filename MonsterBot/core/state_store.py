import time
import threading

class StateStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.positions = {}   # symbol -> {"contracts": float, "side": "long"/"short"/None, "ts":...}
        self.open_orders = {} # symbol -> {orderId -> order_dict}
        self.last_ws_ts = 0
        self.last_rest_ts = 0

    def upsert_position(self, symbol, contracts, side):
        with self.lock:
            self.positions[symbol] = {"contracts": float(contracts), "side": side, "ts": time.time()}

    def set_orders_snapshot(self, symbol, orders: list):
        with self.lock:
            self.open_orders[symbol] = {str(o.get("id")): o for o in orders}
            self.last_rest_ts = time.time()

    def update_order_event(self, symbol, order_id, order_payload):
        with self.lock:
            if symbol not in self.open_orders:
                self.open_orders[symbol] = {}
            self.open_orders[symbol][str(order_id)] = order_payload
            self.last_ws_ts = time.time()

    def remove_order(self, symbol, order_id):
        with self.lock:
            if symbol in self.open_orders:
                self.open_orders[symbol].pop(str(order_id), None)

    def get_position(self, symbol):
        with self.lock:
            return self.positions.get(symbol, {"contracts": 0.0, "side": None})

    def get_open_orders(self, symbol):
        with self.lock:
            return list((self.open_orders.get(symbol) or {}).values())
