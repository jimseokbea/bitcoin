import sys
import os

# [Windows Console Fix] Force UTF-8 for emoji support
# Must run before any other output or logger init
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import time
import yaml
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta, timezone

# Ensure core modules can be imported
sys.path.append(os.getcwd())

from core.executor import FuturesExecutor
from core.scanner import MarketScanner
from core.strategy import HybridStrategy
from core.risk_manager import RiskManager
from core.sizer import PositionSizer
from core.notifier import TelegramBot
from core.database import TradeDB
from core.signal_gate import SignalGate
from core.setup_id import make_setup_fingerprint_from_df

# 설정 로드
with open('config.yaml', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 로거 설정 (RotatingFileHandler 적용)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 10MB마다 파일 교체, 최대 5개 보관 (총 50MB 제한)
if not logger.handlers:
    handler = RotatingFileHandler('bot_final.log', maxBytes=10*1024*1024, backupCount=5)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

from core.binance_filters import BinanceFuturesFilters
from core.state_store import StateStore
from core.executor_sl_atomic import SLAtomicReplacer
from core.reconciler import RestReconciler
from core.binance_user_stream import BinanceFuturesUserStream
import threading

def main():
    load_dotenv()
    logger.info("🦖 Monster Hunter Bot Initializing...")

    # 모듈 초기화
    executor = FuturesExecutor(config)
    
    # [2-Phase Truth Architecture]
    state_store = StateStore()
    filters = BinanceFuturesFilters(executor.exchange)
    sl_atomic = SLAtomicReplacer(executor, filters, state_store, logger)
    reconciler = RestReconciler(executor, sl_atomic, state_store, logger)
    
    # Start WS Stream (if API key present)
    if os.getenv("BINANCE_API_KEY"):
        # [Safety Pin C] WS Stream needs DB to log REALIZED trades for Fee Monitor
        ws_stream = BinanceFuturesUserStream(os.getenv("BINANCE_API_KEY"), state_store, logger, db=TradeDB())
        ws_stream.start()
    
    gate = SignalGate(config, logger) # Initialize Gate
    scanner = MarketScanner(executor, config)
    strategy = HybridStrategy(config)
    risk_mgr = RiskManager(config)
    sizer = PositionSizer(config)
    noti = TelegramBot(config)
    db = TradeDB()

    # 텔레그램 시작 알림
    noti.send("🦖 Monster Hunter Bot이 깨어났습니다.")

    # 헌법 체크 (격리모드, 레버리지 등)
    executor.apply_constitution()

    # Using a mutable container for current_symbol so worker can see updates
    context = {"symbol": "SOL/USDT"} 
    current_symbol = context["symbol"] # For local ref

    # Dynamic Reconciler Worker
    def reconciler_worker():
        while True:
            try:
                # Reconcile current target
                reconciler.reconcile_symbol(context["symbol"])
                time.sleep(15) 
            except: 
                time.sleep(15)
                
    threading.Thread(target=reconciler_worker, daemon=True).start()

    # [Reconciliation] 장부 대조
    logger.info("[Init] Checking Exchange Consistency...")
    amt, side, entry = executor.fetch_real_position(context["symbol"])
    if amt != 0:
        real_pos = {'amt': amt, 'entryPrice': entry}
        logger.warning(f"[Warning] 불일치 감지! 바이낸스에 포지션이 있습니다: {real_pos}")
        noti.send(f"[재시작 경고] 봇이 켜졌는데 이미 포지션을 들고 있습니다!\n종목: {context['symbol']}\n진입가: {real_pos.get('entryPrice')}")
    
    last_heartbeat = datetime.now() - timedelta(hours=1) 

    while True:
        try:
            # Sync back to context if current_symbol changes logic is implemented
            # But wait, scanner.get_top_symbol() might change current_symbol
            # So we should update context["symbol"] whenever current_symbol changes
            
            # ... (inside loop where current_symbol matches scanner result) ...
            
            # Example:
            # new_symbol = scanner.get_best_symbol(...)
            # if new_symbol != current_symbol:
            #     current_symbol = new_symbol
            #     context["symbol"] = current_symbol 
            
            # Since the original code had:
            # current_symbol = "SOL/USDT" (or dynamic)
            # We need to find where current_symbol is updated.
            # Looking at previous main.py, it seems it sticks to one symbol or changes?
            # Let's check where `current_symbol` is assigned.
            pass
            # 0. Kill Switch Check
            if os.path.exists("stop.signal"):
                logger.critical("[STOP] 텔레그램 명령으로 봇을 긴급 정지합니다!")
                noti.send("[STOP] 봇이 긴급 정지되었습니다.")
                try: os.remove("stop.signal")
                except: pass
                break

            # 0.5 Heartbeat
            now = datetime.now()
            if (now - last_heartbeat).total_seconds() > 43200: # 12 hours
                balance = executor.get_balance()
                noti.send(f"[Heartbeat] 봇 정상 작동 중!\n💰 현재 잔고: {balance:.2f} USDT")
                last_heartbeat = now
                logger.info("[Heartbeat] sent.")

            # 1. 일일 리스크 체크 (손실 한도 - Dead limit)
            if not risk_mgr.check_daily_limit(executor):
                logger.warning("[Risk] Daily Limit Reached. Sleeping...")
                time.sleep(3600)
                continue

            # [Safety Pin C] Fee Monitor
            if not risk_mgr.check_fee_ratio(db):
                logger.warning("[Risk] High Fee Ratio Detected! Cooldown 1 hour...")
                time.sleep(3600)
                continue

            # 1.5 BTC Fuse Check
            btc_crash = risk_mgr.check_btc_crash(executor)
            if btc_crash:
                logger.warning("[Fuse] BTC Crash Detected! Tightening SLs & Pausing...")
                try:
                    positions = executor.exchange.fetch_positions()
                    active_pos = [p for p in positions if float(p['contracts']) > 0]
                    for p in active_pos:
                        sym = p['symbol']
                        side = p['side']
                        amt = float(p['contracts'])
                        # Delegate defense update to manage_position
                        # We pass btc_fuse_triggered=True to force emergency tightening
                        executor.manage_position(sym, db, noti, btc_fuse_triggered=True, sl_replacer=sl_atomic)
                except Exception as e:
                    logger.error(f"Fuse Update Error: {e}")

                time.sleep(300) # Wait 5 mins
                continue

            # 2. 포지션 확인
            has_pos = executor.has_position(current_symbol)

            # 3. [사냥 모드] 포지션이 없을 때만 스캔
            if not has_pos and config['scanner']['active']:
                targets = scanner.find_best_targets()
                if targets:
                    new_target = targets[0] 
                    if new_target != current_symbol:
                        logger.info(f"[Target] Switched: {current_symbol} -> {new_target}")
                        current_symbol = new_target
                        context["symbol"] = new_target # Sync for reconciler thread

            # 4. 데이터 수집 및 분석
            ohlcv = executor.fetch_ohlcv(current_symbol)
            signal, sl_price = strategy.analyze(ohlcv)
 

            # 5. 진입 로직 (BTC Fuse 발동 시 신규 진입 차단)
            if signal and not has_pos and not btc_crash:
                # [Fingerprint] Generate Unique Setup ID
                # We need the dataframe to get confirmed bar details.
                # ohlcv is already fetched in step 4.
                setup_id = make_setup_fingerprint_from_df(
                    symbol=current_symbol,
                    tf=config['strategy']['timeframe'],
                    signal_type=signal,
                    df=ohlcv,
                    extras={"sl_price": sl_price}
                )
                
                # [Gate] Check Permission with Setup ID
                allowed, reason = gate.allow_entry(current_symbol, setup_fingerprint=setup_id)
                if not allowed:
                    if "same_symbol" in reason:
                        # Log less verbosely for same-symbol blocks unless it's a new setup
                        pass 
                    else:
                        logger.info(f"🚫 [Gate] Entry Blocked: {current_symbol} ({reason})")
                    time.sleep(10) 
                    continue

                # [Pre-Flight Correction] Golden Time SL Adjust
                utc_now = datetime.now(timezone.utc)
                h = utc_now.hour
                is_golden = (13 <= h <= 16) or (7 <= h <= 10)
                
                curr_p = ohlcv['close'].iloc[-1]
                if is_golden:
                    sl_dist = abs(curr_p - sl_price)
                    new_dist = sl_dist * 1.2
                    sl_price = curr_p - new_dist if signal == 'buy' else curr_p + new_dist
                    logger.info(f"[Golden Time] SL Widened: {sl_dist:.4f} -> {new_dist:.4f}")

                # 리스크 계산
                balance = executor.get_balance()
                
                # [Safety Pin B] Pass ADX for Side Mode Risk
                current_adx = ohlcv['adx'].iloc[-1] if 'adx' in ohlcv else None
                raw_qty = sizer.calc_qty(balance, ohlcv['close'].iloc[-1], sl_price, adx=current_adx)
                
                # [Robust] Validate Qty
                entry_price = ohlcv['close'].iloc[-1]
                qty, reason = executor.validate_entry_qty_or_skip(current_symbol, raw_qty, entry_price)

                if qty > 0:
                    msg = f"[Signal] {signal} on {current_symbol}\nPrice: {entry_price}\nStop Loss: {sl_price}\nQty: {qty}\nID: {setup_id}"
                    logger.info(msg)
                    noti.send(msg)
                    executor.entry(current_symbol, signal, qty, sl_price)
                    
                    # [Gate] Mark Entry with ID
                    gate.mark_entry(current_symbol, setup_fingerprint=setup_id)

            # 6. 관리 로직 (청산/트레일링)
            if has_pos:
                # Prepare Market Data
                curr_data = {
                    'close': ohlcv['close'].iloc[-1],
                    'atr': ohlcv['atr'].iloc[-1] if 'atr' in ohlcv else 0
                }
                executor.manage_position(current_symbol, db, noti, market_data=curr_data, sl_replacer=sl_atomic)

            time.sleep(3) # API 보호를 위한 대기

        except Exception as e:
            logger.error(f"Error in Main Loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
