import time
import json
import threading
import requests
import websocket

class BinanceFuturesUserStream:
    def __init__(self, api_key, state_store, logger, base_url="https://fapi.binance.com"):
        self.api_key = api_key
        self.state = state_store
        self.logger = logger
        self.base_url = base_url
        self.listen_key = None
        self.ws = None
        self.stop_flag = False

    def _headers(self):
        return {"X-MBX-APIKEY": self.api_key}

    def _create_listen_key(self):
        try:
            r = requests.post(f"{self.base_url}/fapi/v1/listenKey", headers=self._headers(), timeout=10)
            r.raise_for_status()
            self.listen_key = r.json()["listenKey"]
            self.logger.info("🔌 [WS] listenKey created")
            return self.listen_key
        except Exception as e:
            self.logger.error(f"[WS Create ListenKey Error] {e}")
            return None

    def _keepalive_loop(self):
        while not self.stop_flag:
            try:
                if self.listen_key:
                    requests.put(f"{self.base_url}/fapi/v1/listenKey", headers=self._headers(), timeout=10)
            except Exception as e:
                self.logger.error(f"[WS keepalive error] {e}")
            time.sleep(30 * 60)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            et = data.get("e")
            if et == "ORDER_TRADE_UPDATE":
                o = data.get("o", {})
                symbol = o.get("s")  # e.g. BTCUSDT (binance raw)
                # 필요 시 심볼 매핑(BTCUSDT -> BTC/USDT)은 별도 map 필요
                self.state.last_ws_ts = time.time()
                # 최소: orderId/상태를 저장(정교화는 본인 로직에 맞게)
            elif et == "ACCOUNT_UPDATE":
                # positions update
                a = data.get("a", {})
                ps = a.get("P", []) or []
                # 여기서도 symbol raw -> unified 매핑 필요
                self.state.last_ws_ts = time.time()
        except Exception as e:
            self.logger.error(f"[WS parse error] {e}")

    def _on_error(self, ws, error):
        self.logger.error(f"[WS error] {error}")

    def _on_close(self, ws, status_code, msg):
        self.logger.warning(f"[WS closed] {status_code} {msg}")

    def _on_open(self, ws):
        self.logger.info("✅ [WS opened] user stream connected")

    def start(self):
        key = self._create_listen_key()
        if not key:
            self.logger.error("❌ Failed to start WS: ListenKey creation failed")
            return

        threading.Thread(target=self._keepalive_loop, daemon=True).start()

        url = f"wss://fstream.binance.com/ws/{self.listen_key}"
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )

        def run():
            while not self.stop_flag:
                try:
                    self.ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    self.logger.error(f"[WS run_forever error] {e}")
                time.sleep(3)

        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
