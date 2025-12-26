import time
import json
import threading
import requests
import websocket

class BinanceFuturesUserStream:
    def __init__(self, api_key, state_store, logger, db=None, base_url="https://fapi.binance.com"):
        self.api_key = api_key
        self.state = state_store
        self.logger = logger
        self.db = db
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
                symbol = o.get("s")
                # Detect Clean Fill with Realized Profit
                # x=TRADE, X=FILLED (or PARTIALLY_FILLED if you want detailed splits)
                # rp (Realized Profit) is key for PnL logging
                execution_type = o.get("x")
                order_status = o.get("X")
                rp = float(o.get("rp", 0))
                
                if execution_type == "TRADE" and rp != 0:
                    # Closing trade triggered (Partial or Full)
                    side = o.get("S") # SELL/BUY
                    last_price = float(o.get("L", 0)) # Last Trade Price
                    avg_price = float(o.get("ap", 0)) # Average Price
                    
                    # Commission
                    commission = 0.0
                    try:
                        n_asset = o.get("N") # Commission Asset (USDT, BNB)
                        n_amt = float(o.get("n", 0)) # Commission Amount
                        # Convert to USDT if needed (Assuming USDT or BNB)
                        # Simplified: Just trust raw amount if USDT, if BNB maybe roughly ignore or 1:1 for simplicity in this safety check
                        commission = n_amt 
                    except: pass
                    
                    if self.db:
                        # log_trade(symbol, side, entry, exit_price, pnl, commission, strategy)
                        # We don't have exact entry price or strategy readily available in WS event without state lookup
                        # But we can log "exit" and "pnl" which is what we need for Fee Monitor
                        # Entry can be avg_price for now or fetched from state.
                        # Important: PnL from binance is absolute amount? Yes 'rp'.
                        # DB expects maybe ROI? risk_manager checks ratio. Ratio = Fee / PnL. Absolute works fine.
                        self.db.log_trade(symbol, side, 0, last_price, rp, commission, "WS_FILL")
                        
                self.state.last_ws_ts = time.time()
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
