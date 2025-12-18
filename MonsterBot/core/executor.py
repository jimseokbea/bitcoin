import ccxt
import time
import os
import pandas as pd
from .utils import get_logger

logger = get_logger()

class FuturesExecutor:
    def __init__(self, config):
        self.config = config
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.secret_key = os.getenv("BINANCE_SECRET_KEY")
        self.leverage = config['risk']['max_leverage']
        self.dry_run = config['system']['dry_run']
        
        # Init CCXT
        options = {'defaultType': 'future'}
        self.exchange = ccxt.binance({
            'apiKey': self.api_key,
            'secret': self.secret_key,
            'enableRateLimit': True,
            'options': options
        })
        
        # [Testnet Support]
        if config['system'].get('testnet', False):
            self.exchange.set_sandbox_mode(True)
            logger.info("🧪 Connected to Binance Futures TESTNET")
        else:
            logger.info("🔌 Connected to Binance Futures MAINNET")
        # Note: RateLimiter logic simplified/removed for brevity as ccxt handles enableRateLimit=True fairly well, 
        # but manual pacing is good. For now rely on ccxt's internal handling + sleep in main loop.

        # State Store for Position Management
        self.tp_state = {}

    def get_balance(self):
        try:
            balance = self.exchange.fetch_balance()
            return float(balance['total']['USDT'])
        except Exception:
            return 0.0

    def apply_constitution(self):
        """Sets leverage and isolation mode."""
        pass # Simplified for now, invoked per symbol usually

    def fetch_ohlcv(self, symbol, timeframe=None, limit=200):
        try:
            tf = timeframe if timeframe else self.config['strategy']['timeframe']
            # [Optimization] Data Warm-up
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV: {e}")
            return None

    # ---------------------------
    # 1) Market Meta (min amount / step / precision / min cost)
    # ---------------------------
    def _get_market_meta(self, symbol: str) -> dict:
        """
        Returns: min_qty, min_cost, amount_precision, price_precision, step
        """
        m = self.exchange.market(symbol)  # safe: ccxt returns unified market
        limits = (m.get("limits") or {})
        amt_lim = (limits.get("amount") or {})
        cost_lim = (limits.get("cost") or {})

        min_qty = float(amt_lim.get("min") or 0.0)
        min_cost = float(cost_lim.get("min") or 0.0)

        # precision
        prec = (m.get("precision") or {})
        amount_precision = int(prec.get("amount") or 0)
        price_precision = int(prec.get("price") or 0)

        # Step size (Binance filters) best-effort
        step = None
        try:
            info = m.get("info") or {}
            filters = info.get("filters") or []
            # Binance filter example: {"filterType":"LOT_SIZE","stepSize":"0.001","minQty":"0.001"...}
            for f in filters:
                if f.get("filterType") in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                    step_str = f.get("stepSize")
                    if step_str:
                        step = float(step_str)
                        break
            if min_cost == 0.0:
                for f in filters:
                    if f.get("filterType") in ("MIN_NOTIONAL",):
                        mn = f.get("notional") or f.get("minNotional")
                        if mn:
                            min_cost = float(mn)
                            break
        except Exception:
            pass

        return {
            "min_qty": min_qty,
            "min_cost": min_cost,
            "amount_precision": amount_precision,
            "price_precision": price_precision,
            "step": step,
        }

    def _floor_to_step(self, qty: float, step: float) -> float:
        import math
        if step is None or step <= 0:
            return qty
        return math.floor(qty / step) * step

    def _round_amount(self, qty: float, amount_precision: int) -> float:
        from decimal import Decimal, ROUND_DOWN
        if amount_precision is None or amount_precision <= 0:
            return float(int(qty))
        q = Decimal(str(qty))
        quant = Decimal("1e-" + str(amount_precision))
        return float(q.quantize(quant, rounding=ROUND_DOWN))

    def _normalize_qty_or_skip(self, symbol: str, qty: float, price: float) -> tuple:
        """
        qty를 거래소 제약에 맞게 보정하고, min notional/qty 불충족 시 0 반환.
        """
        meta = self._get_market_meta(symbol)

        # 1) Step 기반 내림
        if meta["step"]:
            qty = self._floor_to_step(qty, meta["step"])

        # 2) precision 기반 내림
        qty = self._round_amount(qty, meta["amount_precision"])

        # 3) 최소 수량 체크
        if meta["min_qty"] and qty < meta["min_qty"]:
            return 0.0, f"qty<{meta['min_qty']} (min_qty)"

        # 4) 최소 notional(min_cost) 체크
        notional = qty * float(price)
        if meta["min_cost"] and notional < meta["min_cost"]:
            return 0.0, f"notional<{meta['min_cost']} (min_cost)"

        return qty, "OK"

    def validate_entry_qty_or_skip(self, symbol: str, proposed_qty: float, entry_price: float) -> tuple:
        """
        진입 전에 qty를 거래소 제약에 맞게 조정하거나, min notional 불충족이면 포기.
        """
        qty, reason = self._normalize_qty_or_skip(symbol, proposed_qty, entry_price)
        if qty <= 0:
            logger.warning(f"⛔ [Entry Skip] {symbol} proposed={proposed_qty} -> invalid: {reason}")
            return 0.0, reason
        return qty, "OK"

    # ---------------------------
    # 2) Position real-check & Anti-Ghost
    # ---------------------------
    def fetch_real_position(self, symbol):
        """
        Return (contracts, side_str, entry_price)
        side_str: 'long' / 'short' / None
        """
        try:
            positions = self.exchange.fetch_positions([symbol])
            for p in positions:
                if p.get("symbol") == symbol:
                    contracts = float(p.get("contracts", 0) or 0)
                    side = p.get("side")
                    entry = float(p.get("entryPrice", 0) or 0)
                    return contracts, side, entry
            return 0.0, None, 0.0
        except Exception as e:
            logger.error(f"[fetch_real_position] {e}")
            return 0.0, None, 0.0

    def has_position(self, symbol):
        amt, _, _ = self.fetch_real_position(symbol)
        return abs(amt) > 0

    # ---------------------------
    # 3) Cancel only STOP/TP orders AND Entry
    # ---------------------------
    def _is_stop_order(self, o: dict) -> bool:
        """
        Binance Futures SL Check (Strict).
        TP는 절대 건드리지 않음.
        SL 후보: (STOP 계열 + stopPrice 존재) AND (reduceOnly or closePosition)
        """
        try:
            # 1. Type & Info extraction
            t = str(o.get("type") or "").upper()
            info = o.get("info") or {}
            raw_type = str(info.get("type") or "").upper()
            
            # 2. TP Filter (Never touch TP)
            if "TAKE_PROFIT" in t or "TAKE_PROFIT" in raw_type:
                return False

            # 3. Stop Type Check
            is_stop_type = ("STOP" in t) or ("STOP" in raw_type)
            if not is_stop_type:
                return False

            # 4. StopPrice Check
            stop_price = o.get("stopPrice") or o.get("triggerPrice") or info.get("stopPrice") or info.get("stopPrice")
            if not stop_price:
                return False

            # 5. Safe/ReduceOnly Check
            # Binance: 'reduceOnly': True OR 'closePosition': True
            is_reduce = bool(o.get("reduceOnly")) or bool(info.get("reduceOnly")) or bool(info.get("closePosition"))
            
            if is_reduce:
                return True
            
            # Ambiguous case: Safe default -> False
            return False

        except Exception:
            return False

    def cancel_stop_orders_only(self, symbol: str):
        """TP 유지, SL만 취소"""
        cancelled = 0
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            stop_orders = [o for o in open_orders if self._is_stop_order(o)]
            for o in stop_orders:
                oid = o.get("id")
                if not oid: continue
                try:
                    self.exchange.cancel_order(oid, symbol)
                    cancelled += 1
                except: pass
            if cancelled > 0:
                logger.info(f"🧹 [Cancel SL Only] {symbol} cancelled={cancelled}")
        except Exception as e:
            logger.error(f"[cancel_stop_orders_only] {e}")
        return cancelled

    def close_position_reduce_only(self, symbol):
        """
        Anti-ghost: SL cancel -> check real position -> reduceOnly close
        """
        # 1) SL만 취소 (TP 유지)
        self.cancel_stop_orders_only(symbol)
        
        # 2) REST truth check
        amt, side, _ = self.fetch_real_position(symbol)
        if amt <= 0:
            logger.warning(f"⚠️ [GhostGuard] Already flat: {symbol}")
            return None

        # 3) Normalize
        qty, _ = self._normalize_qty_or_skip(symbol, amt, 0) # price 0 ignored in simple step check
        if qty <= 0: return None

        close_side = "sell" if side == "long" else "buy"
        try:
            order = self.exchange.create_order(symbol, 'market', close_side, qty, None, params={"reduceOnly": True})
            logger.info(f"🗑️ [Close] {symbol} side={side} amt={qty} reduceOnly=True")
            return order
        except Exception as e:
            logger.error(f"[Close Error] {e}")
            return None

    def entry(self, symbol, side, qty, sl_price):
        """
        Enhanced Entry with Brackets (STOP_MARKET)
        """
        if self.dry_run:
            logger.info(f"[DRY] Open {symbol} {side} {qty} SL={sl_price}")
            # Mock State for Dry Run Testing
            self.tp_state[symbol] = {'tp1_done': False, 'last_trail_roi': 0.0, 'opened_at': time.time()}
            return

        # 0) Safety: Check Real Position
        amt, _, _ = self.fetch_real_position(symbol)
        if amt > 0:
             logger.warning(f"⚠️ [EntryBlocked] Real Position exists: {symbol} amt={amt}")
             return

        self.cancel_all_orders(symbol)

        try:
            # [Pre-Flight Correction] Lazy Funding Rate Check
            try:
                fr = self.exchange.fetch_funding_rate(symbol)
                rate = float(fr['fundingRate'])
                if abs(rate) > 0.001: 
                    logger.warning(f"⚠️ High Funding Rate ({rate*100:.4f}%) - Entry Skipped")
                    return
            except: pass
            
            # 2. Market Entry
            order = self.exchange.create_order(symbol, 'market', side, qty)
            logger.info(f"✅ [Entry] {symbol} {side} {qty} (id={order['id']})")

            # 3. Stop Loss (server-side STOP_MARKET)
            sl_side = 'sell' if side == 'buy' else 'buy'
            sl_params = {
                'stopPrice': float(sl_price),
                'reduceOnly': True,
                'workingType': 'MARK_PRICE' 
            }
            
            # Atomic logic not fully needed for INITIAL entry, just standard bracket
            sl_order = self.exchange.create_order(symbol, 'STOP_MARKET', sl_side, qty, None, sl_params)
            logger.info(f"🛑 [SL] {symbol} STOP_MARKET {sl_side} @ {sl_price}")
            
            # Init State
            self.tp_state[symbol] = {
                'tp1_done': False, 
                'last_trail_roi': 0.0,
                'opened_at': time.time()
            }
            
        except Exception as e:
            logger.error(f"Entry Error: {e}")
            pass
            
    # Helper needed for entry cleanup
    def cancel_all_orders(self, symbol):
        try:
            self.exchange.cancel_all_orders(symbol)
        except: pass

    # ---------------------------
    # SL placement / atomic replace
    # ---------------------------
    def place_stop_market(self, symbol: str, side: str, qty: float, stop_price: float):
        """
        side: 'long'/'short' 기준으로 "포지션을 닫는 방향"의 stop order를 건다.
        long 포지션 -> sell STOP_MARKET
        short 포지션 -> buy STOP_MARKET
        """
        # Auto-detect exit side if 'long'/'short' passed
        exit_side = 'sell' if side.lower() == 'long' else 'buy'

        # Check Qty/Filters
        qty_n, _ = self._normalize_qty_or_skip(symbol, qty, 0)
        if qty_n <= 0:
            logger.warning(f"⚠️ [place_stop_market Skip] qty too small {symbol} qty={qty}")
            return None
            
        try:
            params = {
                 'stopPrice': float(stop_price),
                 'reduceOnly': True,
                 'workingType': 'MARK_PRICE',
            }
            o = self.exchange.create_order(symbol, 'STOP_MARKET', exit_side, qty_n, None, params)
            logger.info(f"🧷 [SL Placed] {symbol} side={side} qty={qty_n} stop={stop_price}")
            return o
        except Exception as e:
            logger.error(f"🚨 [place_stop_market] {symbol} err={e}")
            return None

    def replace_sl_only_atomic(self, symbol: str, side: str, new_qty: float, new_stop_price: float):
        """
        Atomic replace:
          1) (REST) 포지션 확인
          2) 새 SL 먼저 생성
          3) 생성 확인(짧게) 후 기존 SL만 취소
        TP는 절대 건드리지 않음.
        """
        # Direction normalization
        pos_side = side.lower()
        if pos_side not in ['long', 'short']:
             if pos_side == 'buy': pos_side = 'long'
             elif pos_side == 'sell': pos_side = 'short'

        amt, real_side, _ = self.fetch_real_position(symbol)
        if amt <= 0:
            logger.info(f"🧼 [Atomic SL] flat -> cancel SL only: {symbol}")
            self.cancel_stop_orders_only(symbol)
            return False

        if real_side != pos_side:
            logger.warning(f"⚠️ [Atomic SL] side mismatch {symbol} bot={pos_side} ex={real_side} -> abort")
            return False

        qty_n, _ = self._normalize_qty_or_skip(symbol, min(float(new_qty), float(amt)), 0)
        if qty_n <= 0:
            logger.warning(f"⚠️ [Atomic SL] qty too small after normalize: {symbol} qty={new_qty}")
            return False

        # 1) 새 SL 생성
        created = self.place_stop_market(symbol, pos_side, qty_n, new_stop_price)
        if not created:
            logger.warning(f"⚠️ [Atomic SL] new SL create failed -> keep old SL: {symbol}")
            return False

        # 2) 짧은 확인 루프
        ok = False
        for _ in range(3):
            try:
                oo = self.exchange.fetch_open_orders(symbol)
                new_id = created.get("id")
                if new_id and any(str(o.get("id")) == str(new_id) for o in oo):
                    ok = True
                    break
            except Exception:
                pass
            time.sleep(0.2)

        if not ok:
            logger.warning(f"⚠️ [Atomic SL] new SL not confirmed fast, abort cancel old SL: {symbol}")
            return False

        # 3) 기존 SL만 취소 (새 SL 제외)
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            stop_orders = [o for o in open_orders if self._is_stop_order(o)]
            for o in stop_orders:
                oid = o.get("id")
                if not oid: continue
                # 새로 만든 SL은 유지
                if str(oid) == str(created.get("id")): continue
                try:
                    self.exchange.cancel_order(oid, symbol)
                    logger.info(f"🧽 [Atomic SL] cancel old SL: {symbol} order_id={oid}")
                except Exception as ce:
                    logger.warning(f"⚠️ [Atomic SL] cancel old SL fail: {symbol} oid={oid} err={ce}")
                    # Failure Recovery Log for User
                    logger.warning(f"🧟 [Zombie SL Risk] {symbol} Failed to cancel old SL {oid}. Duplicate SLs might exist!")
            
            logger.info(f"✅ [Atomic SL] replaced: {symbol} qty={qty_n} stop={new_stop_price}")
            return True
        except Exception as e:
            logger.error(f"🚨 [Atomic SL] post-cancel error: {symbol} err={e}")
            return False

    # Alias for legacy calls
    def update_stop_loss_atomic(self, symbol: str, direction: str, desired_sl_price: float):
         side = 'long' if direction == 'LONG' else 'short'
         return self.replace_sl_only_atomic(symbol, side, 999999, desired_sl_price)


    # ---------------------------
    # 4) Soft TP & Atomic SL Sync
    # ---------------------------
    def close_partial(self, symbol: str, side: str, total_amt: float, ratio: float):
        """
        Soft TP용 부분 청산 (Safety-First).
        1) Pre-check: REST 포지션 확인
        2) Validation: Normalize, MinQty, MinNotional
        3) Execute: reduceOnly
        4) Post: Log remaining qty (actual sync triggers in manage_position)
        """
        # 1) Pre-check (Safety Guard 1)
        # Caller might have checked, but internal double-check is requested.
        real_amt, real_side, _ = self.fetch_real_position(symbol)
        
        if real_amt <= 0:
            logger.warning(f"⚠️ [ClosePartial Skip] Already flat: {symbol}")
            return None
        
        # Verify side match (Optional but safe)
        pos_side = side.lower()
        if pos_side not in ['long', 'short']:
             if pos_side == 'buy': pos_side = 'long'
             elif pos_side == 'sell': pos_side = 'short'
             
        if real_side != pos_side:
            logger.warning(f"⚠️ [ClosePartial Skip] Side mismatch: passed={pos_side} real={real_side}")
            return None

        # 2) Calculate Qty
        qty = float(real_amt) * float(ratio)

        # 3) Normalize with Filters
        from core.binance_filters import BinanceFuturesFilters
        filters = BinanceFuturesFilters(self.exchange)
        meta = filters.parse(symbol)
        
        qty = filters.floor_to_step(qty, meta["step"], meta["amount_precision"])
        
        # Min Qty Check
        if qty < meta["min_qty"]:
            logger.warning(f"⚠️ [ClosePartial Skip] qty too small (dust): {symbol} qty={qty} min={meta['min_qty']}")
            return None

        # Min Notional Check (Strict)
        try:
            last = self.exchange.fetch_ticker(symbol).get("last") or 0
        except: last = 0
        
        if last and not filters.validate_notional(qty, float(last), meta["min_cost"]):
            logger.warning(f"⚠️ [ClosePartial Skip] minNotional fail: {symbol} qty={qty}")
            # User Policy: "부분청산 포기 또는 전량청산" -> We choose SKIP here to avoid unexpected full close.
            return None

        # 4) Execute (reduceOnly)
        close_side = "sell" if side == "long" else "buy"
        try:
            o = self.exchange.create_order(symbol, "market", close_side, qty, None, {"reduceOnly": True})
            logger.info(f"✅ [ClosePartial] {symbol} ratio={ratio} qty={qty} reduceOnly=True")
            return o
        except Exception as e:
            logger.error(f"🚨 [ClosePartial Error] {symbol} {e}")
            return None

    # ---------------------------
    # Helpers for Tight Sniper
    # ---------------------------
    def get_mark_price(self, symbol: str) -> float:
        try:
            # Using fetch_ticker 'last' as proxy for speed
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker['last'])
        except:
            return 0.0

    def close_all(self, symbol: str):
        """
        Safe Full Close: Cancel SL only -> ReduceOnly Market Close
        """
        self.cancel_stop_orders_only(symbol)
        self.close_position_reduce_only(symbol)

    def manage_position(self, symbol, db=None, noti=None, market_data=None, btc_fuse_triggered=False, sl_replacer=None):
        """
        Tight Sniper Manage Position:
        - Real-time Position Check
        - BTC Fuse Defense
        - TP1 + Breakeven (Free Position)
        - Early Defense (Micro-Trail)
        - TP2 + Trailing
        - Time Cut
        """
        # Initialize State
        if symbol not in self.tp_state:
            self.tp_state[symbol] = {
                'tp1_done': False, 
                'last_trail_roi': 0.0,
                'opened_at': time.time() # Estimate
            }
        
        st = self.tp_state[symbol]

        # 0) REST Truth Check (Anti-Ghost)
        pos_qty, pos_side, entry_price_real = self.fetch_real_position(symbol)
        
        if pos_qty <= 0:
            # Position gone
            if st.get('has_position', False):
                logger.info(f"🧹 [Manage] Position closed externally. Reset state: {symbol}")
                st['has_position'] = False
                st['tp1_done'] = False
                st['sl_price'] = 0.0
            return

        # Update State with Truth
        st['has_position'] = True
        st['qty'] = pos_qty
        st['side'] = pos_side
        st['entry_price'] = entry_price_real
        
        # Get Current SL from Exchange to sync state
        current_sl_price = 0.0
        try:
            open_orders = self.exchange.fetch_open_orders(symbol)
            sl_orders = [o for o in open_orders if self._is_stop_order(o)]
            if sl_orders:
                current_sl_price = float(sl_orders[0].get('stopPrice') or sl_orders[0]['info'].get('stopPrice'))
                st['sl_price'] = current_sl_price
        except: pass

        side = pos_side
        entry = entry_price_real
        qty = pos_qty

        # Helper: ROI Calc
        mark = self.get_mark_price(symbol)
        if mark == 0: return # Retry next loop
        
        roi = (mark - entry) / entry if side == 'long' else (entry - mark) / entry

        # Config Shortcuts
        exit_cfg = self.config.get('exit', {})
        
        # 1) BTC Fuse Defense
        if btc_fuse_triggered:
            # Emergency SL tightening
            # 롱이면 현재가 아래 0.3%, 숏이면 위 0.3%
            dist = 0.003
            if side == 'long':
                emergency_sl = mark * (1.0 - dist)
                if current_sl_price == 0 or emergency_sl > current_sl_price:
                    logger.warning(f"🛡️ [BTC FUSE] Tightening SL: {symbol} -> {emergency_sl}")
                    self.replace_sl_only_atomic(symbol, side, qty, emergency_sl)
            else:
                emergency_sl = mark * (1.0 + dist)
                if current_sl_price == 0 or emergency_sl < current_sl_price:
                    logger.warning(f"🛡️ [BTC FUSE] Tightening SL: {symbol} -> {emergency_sl}")
                    self.replace_sl_only_atomic(symbol, side, qty, emergency_sl)
            return

        # 2) TP1 (40% 청산) + Breakeven
        tp1 = exit_cfg.get('tp1', {})
        if tp1.get('enabled', True) and not st.get('tp1_done'):
            target_roi = float(tp1.get('roi', 0.012))
            if roi >= target_roi:
                ratio = float(tp1.get('qty_ratio', 0.4))
                
                order = self.close_partial(symbol, side, qty, ratio)
                if order:
                    st['tp1_done'] = True
                    if noti: noti.send(f"💰 [TP1] {symbol} +{roi*100:.2f}% Hit! (40% 청산)")
                    
                    new_qty, _, _ = self.fetch_real_position(symbol)
                    if new_qty > 0:
                        aft = exit_cfg.get('after_tp1', {})
                        plus = float(aft.get('move_sl_to_entry_plus_pct', 0.0015))
                        be_sl = entry * (1 + plus) if side == 'long' else entry * (1 - plus)
                        logger.info(f"🛡️ [Breakeven] {symbol} SL → {be_sl} (Entry+{plus*100}%)")
                        self.replace_sl_only_atomic(symbol, side, new_qty, be_sl)
                    else:
                        self.cancel_stop_orders_only(symbol)
                return 

        # 3) Early Defense (Micro-Trail) - TP1 전용
        ed = exit_cfg.get('early_defense', {})
        if ed.get('enabled', True) and not st.get('tp1_done'):
             trigger = float(ed.get('trigger_roi', 0.006))
             sl_minus = float(ed.get('sl_to_minus_pct', 0.006))
             
             if roi >= trigger:
                 if side == 'long':
                     new_sl = entry * (1.0 - sl_minus)
                     if current_sl_price == 0 or new_sl > current_sl_price:
                         logger.info(f"🛡️ [EarlyDefense] {symbol} +{roi*100:.1f}% → SL {new_sl}")
                         self.replace_sl_only_atomic(symbol, side, qty, new_sl)
                 else:
                     new_sl = entry * (1.0 + sl_minus)
                     if current_sl_price == 0 or new_sl < current_sl_price:
                         logger.info(f"🛡️ [EarlyDefense] {symbol} +{roi*100:.1f}% → SL {new_sl}")
                         self.replace_sl_only_atomic(symbol, side, qty, new_sl)

        # 4) TP2 (30% 청산) - TP1 이후
        tp2 = exit_cfg.get('tp2', {})
        if tp2.get('enabled', True) and st.get('tp1_done') and not st.get('tp2_done'):
            target_roi = float(tp2.get('roi', 0.025))
            if roi >= target_roi:
                ratio = float(tp2.get('qty_ratio', 0.3))
                # 남은 수량의 비율로 계산 (60% 중 30% = 50%)
                adjusted_ratio = ratio / (1 - float(tp1.get('qty_ratio', 0.4)))
                adjusted_ratio = min(adjusted_ratio, 0.5)  # 안전장치
                
                order = self.close_partial(symbol, side, qty, adjusted_ratio)
                if order:
                    st['tp2_done'] = True
                    if noti: noti.send(f"💰💰 [TP2] {symbol} +{roi*100:.2f}% Hit! (30% 청산, 잔여 트레일링)")
                    logger.info(f"💰💰 [TP2] {symbol} +{roi*100:.2f}% - 30% closed, trailing remaining")
                return

        # 5) TP3 (최종 목표) - 전량 청산
        tp3 = exit_cfg.get('tp3', {})
        if tp3.get('enabled', True):
            target_roi = float(tp3.get('roi', 0.045))
            if roi >= target_roi:
                logger.info(f"🚀 [TP3] {symbol} +{roi*100:.2f}% → 전량 청산!")
                if noti: noti.send(f"🚀 [TP3] {symbol} +{roi*100:.2f}% 졸업!")
                self.close_all(symbol)
                return

        # 6) ATR 기반 트레일링 스탑 (큰 추세 포획기)
        trailing = exit_cfg.get('trailing', {})
        if trailing.get('enabled', True):
            start_roi = float(trailing.get('start_roi', 0.025))
            if roi >= start_roi:
                step = float(trailing.get('step_roi', 0.002))
                last_tr = float(st.get('last_trail_roi', 0.0))
                
                # ATR 기반 동적 거리 계산
                if trailing.get('use_atr', True) and market_data and market_data.get('atr'):
                    atr = float(market_data.get('atr', 0))
                    if roi < 0.02:
                        trail_mult = float(trailing.get('atr_mult_small', 1.5))
                    else:
                        trail_mult = float(trailing.get('atr_mult_large', 2.5))
                    dist = (atr * trail_mult) / mark  # ATR를 비율로 변환
                else:
                    dist = float(trailing.get('fallback_dist', 0.015))
                
                # Step Check
                if (roi - last_tr) >= step:
                    should_update = False
                    new_sl = 0.0
                    
                    if side == 'long':
                        new_sl = mark * (1.0 - dist)
                        if current_sl_price == 0 or new_sl > current_sl_price:
                            should_update = True
                    else:
                        new_sl = mark * (1.0 + dist)
                        if current_sl_price == 0 or new_sl < current_sl_price:
                             should_update = True
                    
                    if should_update:
                        logger.info(f"🧗 [Trailing] {symbol} +{roi*100:.2f}% → SL {new_sl} (ATR dist={dist*100:.2f}%)")
                        ok = self.replace_sl_only_atomic(symbol, side, qty, new_sl)
                        if ok:
                            st['last_trail_roi'] = roi

        # 5) Time Cut (Enhanced: Minutes or Candles mode)
        tc = exit_cfg.get('time_cut', {})
        if tc.get('enabled', False):
             # Check min profit - don't cut if already profitable
             min_profit = float(tc.get('min_profit_pct', 0.005))
             if roi >= min_profit:
                 return  # Already profitable, let it ride
             
             mode = tc.get('mode', 'minutes')
             should_cut = False
             
             if mode == 'candles':
                 # Candle-based: Count candles since entry
                 max_candles = int(tc.get('max_candles', 10))
                 if 'entry_candle_count' not in st:
                     st['entry_candle_count'] = 0
                 st['entry_candle_count'] += 1  # Incremented each manage_position call
                 
                 if st['entry_candle_count'] >= max_candles:
                     logger.info(f"✂️ [TimeCut] {symbol} {st['entry_candle_count']} candles >= {max_candles}. Closing.")
                     should_cut = True
             else:
                 # Minutes-based (legacy)
                 max_min = tc.get('max_hold_minutes', 240)
                 if 'timestamp' in self.tp_state[symbol] and self.tp_state[symbol]['timestamp'] > 0:
                     start_ts = self.tp_state[symbol]['timestamp']
                 elif 'opened_at' in st:
                     start_ts = st['opened_at']
                 else:
                     start_ts = time.time()
                     
                 elapsed_min = (time.time() - start_ts) / 60
                 if elapsed_min > max_min:
                     logger.info(f"✂️ [TimeCut] {symbol} {int(elapsed_min)}m > {max_min}m. Closing.")
                     should_cut = True
             
             if should_cut:
                 self.close_all(symbol)

    def replace_stop_loss(self, symbol, side, qty, new_stop_price):
        """
        [Safety] Atomically replaces Stop Loss order.
        Cancels old SL and sets new one immediately.
        """
        try:
            # 1. Fetch Open Orders
            open_orders = self.exchange.fetch_open_orders(symbol)
            stop_orders = [o for o in open_orders if o['type'] in ['STOP', 'STOP_MARKET', 'stop_market']]
            
            # 2. Cancel Old
            for order in stop_orders:
                try:
                    self.exchange.cancel_order(order['id'], symbol)
                except: pass
                
            # 3. Create New
            sl_side = 'sell' if side == 'long' or side == 'LONG' else 'buy'
            params = {'stopPrice': new_stop_price, 'reduceOnly': True}
            
            self.exchange.create_order(symbol, 'STOP_MARKET', sl_side, qty, None, params)
            logger.info(f"🛡️ SL Replaced for {symbol}: {new_stop_price}")
            
        except Exception as e:
            logger.error(f"❌ Failed to replace SL for {symbol}: {e}")
            # If fail, maybe just market close? For now just log.
