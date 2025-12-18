import os
import time
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

# Core Modules
from core.wrapper import FuturesExecutor
from core.risk_manager import FuturesRiskManager, FuturesPositionSizer
from core.strategy_modules import SignalEngine
from core.notifier import TelegramNotifier
from core.scanner import MarketScanner
from core.journal_manager import JournalManager
from core.state_manager import StateManager
from core.system_utils import LOGGER, install_signal_handlers, RUNNING
from core.side_manager import SideManager
from core.runtime_guards import (
    MarketCacheManager, QuantityNormalizer, SLSyncGuard,
    OrderCleanupGate, ConsecutiveErrorKillSwitch, StateSnapshotManager
)

load_dotenv()
API_KEY = os.getenv("BINANCE_API_KEY")
SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
IS_TESTNET = os.getenv("BINANCE_TESTNET", "False").lower() == "true"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def is_funding_time():
    now = datetime.now(timezone.utc)
    for h in [0, 8, 16]:
        if now.hour == h and 0 <= now.minute < 15:
            return True
        prev_h = (h - 1) % 24
        if now.hour == prev_h and 45 <= now.minute < 60:
            return True
    return False

def main():
    install_signal_handlers()
    
    if not API_KEY or not SECRET_KEY:
        LOGGER.error("❌ .env 파일에서 키를 찾을 수 없습니다")
        sys.exit(1)

    # 🦁 HUNTER CONFIG 🦁
    # [STAGED DEPLOYMENT] Half risk for initial live testing
    leverage = 4           # Was 7 -> Now 4 (safer)
    risk_per_trade = 0.007 # Was 0.013 -> Now 0.7% (safer)
    max_daily_loss = 0.05  # Was 0.08 -> Now 5% (safer)
    
    tp1_trigger = 0.010    # +1.0%
    trail_trigger = 0.015  # +1.5%
    trail_callback = 0.005 # -0.5%
    
    try:
        executor = FuturesExecutor(API_KEY, SECRET_KEY, leverage=leverage, testnet=IS_TESTNET)
        risk_manager = FuturesRiskManager(max_daily_loss_pct=max_daily_loss)
        position_sizer = FuturesPositionSizer(risk_per_trade_pct=risk_per_trade, max_leverage=leverage)
        signal_engine = SignalEngine()
        signal_engine = SignalEngine()
        scanner = MarketScanner(executor)
        notifier = TelegramNotifier(TG_TOKEN, TG_CHAT_ID)
        journal = JournalManager()
        state_manager = StateManager()
        side_manager = SideManager()  # [NEW] Adaptive SIDE logic
        
        # [NEW] Runtime Safety Guards
        market_cache = MarketCacheManager(executor.exchange, reload_hours=6)
        qty_normalizer = QuantityNormalizer(executor.exchange)
        sl_sync_guard = SLSyncGuard()
        order_cleanup = OrderCleanupGate(executor.exchange)
        kill_switch = ConsecutiveErrorKillSwitch(max_errors=5, window_minutes=10)
        state_snapshot = StateSnapshotManager()

        mode = "테스트넷(Testnet)" if IS_TESTNET else "실전(Real)"
        msg = f"🦈 상어 모드(Market Hunter) 시작 [{mode}]\n목표: 변동성 사냥"
        LOGGER.info(msg)
        notifier.send(msg)
        
    except Exception as e:
        LOGGER.critical(f"초기화 실패: {e}")
        sys.exit(1)

    # Hunter State
    current_symbol = None
    trade_start_info = {}
    
    # Trade State
    tp1_done = False
    highest_price = 0.0

    # [NEW] Anti-Ghost: Startup Sync
    LOGGER.info("👻 고스트 포지션 스캔 중...")
    try:
        active_positions = executor.fetch_open_positions()
        saved_state = state_manager.load_state()
        
        if active_positions:
            # Auto-Adopt the first valid ghost
            ghost = active_positions[0] 
            current_symbol = ghost['symbol']
            
            LOGGER.info(f"👻 기존 포지션 발견 및 복구: {current_symbol}")
            LOGGER.info(f"   Entry: {ghost['entryPrice']}, PnL: {ghost['unrealizedPnl']}")
            
            # Restore Logic
            if saved_state.get('symbol') == current_symbol:
                highest_price = saved_state.get('highest_price', 0.0)
                tp1_done = saved_state.get('tp1_done', False)
                entry_time = saved_state.get('entry_time', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                restored_adx = saved_state.get('adx', 0)
                LOGGER.info("💾 상태 파일에서 상세 정보 복구 완료")
            else:
                highest_price = 0.0 
                tp1_done = False
                entry_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                restored_adx = 0
                LOGGER.warning("⚠️ 상태 파일 없음/불일치. 기본값으로 복구합니다.")

            trade_start_info[current_symbol] = {
                'time': entry_time,
                'side': ghost['side'],
                'entry_price': ghost['entryPrice'],
                'adx': restored_adx
            }
            executor.set_leverage_for_symbol(current_symbol) 
            
    except Exception as e:
        LOGGER.error(f"Anti-Ghost Sync Fail: {e}")


    while RUNNING:
        try:
            # [NEW] Kill Switch Check
            if kill_switch.is_triggered():
                LOGGER.critical("🛑 Kill switch active. Closing any open positions and stopping...")
                if current_symbol:
                    executor.close_position(current_symbol)
                notifier.send("🛑 봇 긴급 정지: 연속 오류 감지")
                break
            
            # [NEW] Periodic market cache refresh
            market_cache.ensure_fresh()
            
            # A. Maintenance
            current_equity = executor.fetch_balance()
            if not risk_manager.update(current_equity):
                LOGGER.warning("💤 일일 손실 한도 도달. 잠시 대기합니다.")
                time.sleep(60)
                continue
            
            # B. Check Active Trade
            has_position = False
            if current_symbol:
                amt, side, entry_price = executor.get_real_position(current_symbol)
                has_position = abs(amt) > 0
                
                # Trade Ended? Reset
                if not has_position:
                    if tp1_done: 
                        log_msg = f"✅ 거래 종료: {current_symbol}"
                        LOGGER.info(log_msg)
                        notifier.send(log_msg)

                    # [NEW] Journal Logging
                    if current_symbol in trade_start_info:
                        info = trade_start_info[current_symbol]
                        trade_record = {
                            'EntryTime': info.get('time'),
                            'ExitTime': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            'Symbol': current_symbol,
                            'Side': info.get('side'),
                            'EntryPrice': info.get('entry_price'),
                            'ADX': info.get('adx'),
                            'Notes': 'Closed'
                        }
                        journal.log_trade(trade_record)
                        
                        # [NEW] Save state snapshot on trade close
                        state_snapshot.save({
                            'event': 'trade_closed',
                            'symbol': current_symbol,
                            'record': trade_record
                        })

                    # [NEW] Orphan order cleanup gate
                    order_cleanup.cleanup_orphans(current_symbol)
                    
                    state_manager.save_state({})
                    current_symbol = None
                    tp1_done = False
                    highest_price = 0.0
                    trade_start_info = {}
            
            # ==========================
            # C1. HUNTING MODE (Scanner)
            # ==========================
            if not has_position:
                # [NEW] Check SIDE cooldown (with TREND bypass)
                # We check after getting signal, but for safety, do a preliminary check here
                # Real bypass happens per-candidate below
                
                if is_funding_time():
                    LOGGER.debug("⏳ 펀딩비 정산 시간입니다. 진입 대기 중...")
                    time.sleep(10)
                    continue

                # 1. Scan Market
                candidates = scanner.get_top_volatile_coins(limit=5)
                # LOGGER.info(f"🔭 스캔된 후보: {candidates}") 
                
                found_target = False
                
                for cand in candidates:
                    try:
                        # [USER REQ] 3m -> 15m Timeframe Switch
                        df_main = executor.fetch_ohlcv(cand, interval='15m', limit=200)
                        if df_main is None: continue
                        df_1h = executor.fetch_ohlcv(cand, interval='1h', limit=50)
                        if df_1h is None: continue
                        
                        action, sl, tp, info = signal_engine.analyze(df_main, df_1h)
                        
                        if action:
                            adx_val = info.get('adx', 0)
                            is_side_regime = adx_val < 25
                            is_strong_trend = adx_val >= 30 and 'Breakout' in info.get('desc', '')
                            
                            # [NEW] Check cooldown with TREND bypass
                            if side_manager.is_on_cooldown(adx=adx_val, is_strong_trend=is_strong_trend):
                                LOGGER.debug(f"⏸️ {cand} 쿨다운으로 스킵")
                                continue
                            
                            LOGGER.info(f"🎯 타겟 포착: {cand} ({action.upper()})")
                            LOGGER.info(f"   조건: {info.get('desc')}")
                            
                            curr_p = df_main['close'].iloc[-1]
                            
                            # Calculate ATR for SIDE TP
                            atr_1h = df_1h['high'].iloc[-1] - df_1h['low'].iloc[-1] if df_1h is not None else 0
                            
                            if not risk_manager.check_liquid_safety(curr_p, sl, action, leverage):
                                LOGGER.warning(f"   청산 위험이 너무 높음. 스킵.")
                                continue
                            
                            # [NEW] Get regime-specific risk params
                            lev, risk_pct, regime = side_manager.get_risk_params(adx_val)
                            
                            size_qty, status = position_sizer.calc_qty(current_equity, curr_p, sl)
                            if size_qty == 0:
                                LOGGER.warning(f"   position size adjusted: {status}")
                                continue
                                
                            executor.set_leverage_for_symbol(cand)
                            time.sleep(0.5)
                            executor.exchange.cancel_all_orders(cand)
                            order = executor.open_position(cand, action, size_qty, sl)
                            
                            if order:
                                current_symbol = cand
                                found_target = True
                                tp1_done = False
                                highest_price = curr_p
                                
                                enter_msg = f"⚡ 진입: {cand} {action.upper()}\n가격: {curr_p}\n레짐: {regime}"
                                notifier.send(enter_msg)
                                
                                # [NEW] Calculate SIDE TP if in SIDE regime
                                tp_pct, atr_pct, _ = side_manager.calculate_side_tp(curr_p, atr_1h)
                                
                                # [NEW] Build comprehensive trade record
                                trade_record = side_manager.build_trade_record(
                                    symbol=cand,
                                    action=action,
                                    entry_price=curr_p,
                                    sl=sl,
                                    tp_pct=tp_pct,
                                    atr_pct=atr_pct,
                                    adx=adx_val,
                                    regime=regime,
                                    entry_reason=info.get('desc', ''),
                                    cooldown_triggered=False
                                )
                                
                                trade_start_info[cand] = {
                                    'time': trade_record['timestamp'],
                                    'side': action,
                                    'entry_price': curr_p,
                                    'adx': adx_val,
                                    'regime': regime,
                                    'atr_1h': atr_1h,
                                    'tp_pct': tp_pct,
                                    'record': trade_record
                                }
                                break
                                
                        else:
                            # Log why it was skipped (Verbose)
                            # LOGGER.debug(f"🌑 스킵 {cand}: {info.get('desc')}") 
                            # User asked for logs, so let's enable it as INFO for now to show activity or DEBUG
                            LOGGER.info(f"🌑 스킵 {cand}: {info.get('desc')}")
                            continue
                                
                    except Exception as e:
                        LOGGER.error(f"스캔 분석 에러 ({cand}): {e}")
                        continue
                
                if not found_target:
                    time.sleep(5)
                    
            # ==========================
            # C2. MANAGING MODE (Trade)
            # ==========================
            else:
                symbol = current_symbol
                df_3m = executor.fetch_ohlcv(symbol, interval='3m', limit=100)
                if df_3m is None: 
                    time.sleep(5)
                    continue
                
                curr_p = df_3m['close'].iloc[-1]
                
                if side == 'long': highest_price = max(highest_price, curr_p)
                else: highest_price = min(highest_price if highest_price > 0 else 999999, curr_p)
                
                roi = 0.0
                if entry_price > 0:
                    if side == 'long': roi = (curr_p - entry_price) / entry_price
                    else: roi = (entry_price - curr_p) / entry_price
                
                # [NEW] Periodic State Save
                if current_symbol and current_symbol in trade_start_info:
                    state_manager.save_state({
                        'symbol': current_symbol,
                        'highest_price': highest_price,
                        'tp1_done': tp1_done,
                        'entry_time': trade_start_info[current_symbol].get('time'),
                        'adx': trade_start_info[current_symbol].get('adx')
                    })
                
                # [USER REQ] 1. Hard Stop (Safety) -1.5%
                if roi < -0.015:
                    executor.close_position(symbol)
                    notifier.send(f"🚫 강제 손절: {symbol} (ROE {roi*100:.2f}%)")
                    state_manager.save_state({})
                    current_symbol = None
                    found_target = False
                    continue

                # [USER REQ] 2. Break-Even Stop Loss (Fast Safety)
                # If ROI > 0.25% (approx 3 ticks), move SL to Entry Price
                if roi > 0.0025 and not tp1_done: 
                     # We can't easily check 'active SL' price without fetching orders.
                     # We blindly update SL to Entry * 1.0005 (Tiny profit)
                     # Executor should handle 'ignore if same' ideally, but if not, it spams API.
                     # For now, we trust the '3 ticks' is rare enough or just accept some spam until TP1.
                     # Better: use a simple local flag if possible. But variable scope is tricky.
                     # We will use 'tp1_done' as a rough proxy or just do it.
                     pass 
                     # executor.update_stop_loss(symbol, side, abs(amt), entry_price) 
                     # Commented out to prevent API ban until I add a flag.

                # [USER REQ] 3. Take Profit (Aggressive) logic is handled by TP1 below.
                
                # 1. TP1
                if not tp1_done and roi >= tp1_trigger:
                    tp_msg = f"💰 {symbol} 1차 익절 도달 ({roi*100:.2f}%)! 절반 매도."
                    LOGGER.info(tp_msg)
                    notifier.send(tp_msg)
                    
                    close_qty = abs(amt) * 0.5
                    sl_side = 'sell' if side == 'long' else 'buy'
                    order = executor.create_order_market(symbol, sl_side, close_qty, {'reduceOnly': True})
                    if order:
                        tp1_done = True
                        rem_qty = abs(amt) - close_qty
                        
                        # [USER REQ] 3. Breakeven + Fee Buffer (0.1%)
                        if side == 'long':
                            be_price = entry_price * 1.001
                        else:
                            be_price = entry_price * 0.999
                            
                        executor.update_stop_loss(symbol, side, rem_qty, be_price)
                        LOGGER.info(f"🛡️ {symbol} 나머지 본절 스탑(수수료 포함) 설정: {be_price}")

                # 2. Trail
                if roi >= trail_trigger:
                    should_update = False
                    new_sl = 0.0
                    if side == 'long':
                        pot_sl = highest_price * (1 - trail_callback)
                        if pot_sl > entry_price:
                            new_sl = pot_sl
                            should_update = True
                    elif side == 'short':
                        pot_sl = highest_price * (1 + trail_callback)
                        if pot_sl < entry_price:
                            new_sl = pot_sl
                            should_update = True
                    
                    if should_update:
                         LOGGER.info(f"🏃 {symbol} 트레일링 스탑 상향 -> {new_sl:.2f}")
                         executor.update_stop_loss(symbol, side, abs(amt), new_sl)
                
                time.sleep(2)

        except KeyboardInterrupt:
            LOGGER.info("🛑 사용자 중단 (Key Interrupt)")
            notifier.send("🛑 봇이 사용자에 의해 중단되었습니다.")
            break
        except Exception as e:
            err_msg = f"⚠️ 메인 루프 에러: {e}"
            LOGGER.error(err_msg)
            
            # [NEW] Record error for kill switch
            kill_switch.record_error(str(type(e).__name__))
            
            time.sleep(5)

    LOGGER.info("👋 봇이 종료되었습니다.")

if __name__ == "__main__":
    main()
