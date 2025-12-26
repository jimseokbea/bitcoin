import time
import yaml
import sys
import os
from datetime import datetime, timedelta
from collections import deque

# Core Imports
from core.system_utils import (
    LOGGER, LOCK_FILE, RUNNING, install_signal_handlers, 
    acquire_lock, release_lock, LIMITER
)
from core.wrapper import UpbitAPIWrapper
from core.risk_manager import DailyRiskManager, PositionSizer
from core.strategy_modules import MarketFilter, SignalEngine, RiskEngine
from core.strategy_tuner import StrategyTuner
from core.telegram_notifier import TelegramNotifier

def load_config():
    """Load configuration from settings.yaml"""
    config_path = os.path.join(os.path.dirname(__file__), 'config', 'settings.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

# --- HELPER FUNCTIONS ---
def fee_monitor_triggered(recent_trades_history, max_fee_ratio: float = 0.30):
    """
    recent_trades_history item: {'fee': x, 'gross_pnl': y, 'timestamp': ...}
    """
    if not recent_trades_history:
        return False, {"reason": "no_history"}

    total_fees = sum(float(t.get("fee", 0.0)) for t in recent_trades_history)
    total_gross = sum(float(t.get("gross_pnl", 0.0)) for t in recent_trades_history)

    meta = {"total_fees": total_fees, "total_gross_pnl": total_gross, "window": len(recent_trades_history)}

    # 운영 안전: 최근 윈도우 총손익이 0 이하이면 성과가 깨졌거나 수수료 부담이 과대 → 차단
    if total_gross <= 0.0:
        meta["reason"] = "gross_pnl<=0"
        # Total Gross PnL이 음수여도 수수료 비중 체크가 의미 없을 수 있음 -> 방어적 차단
        # 하지만 단순 손실 구간일 수도 있음.
        # User Logic: "total_gross_pnl <= 0 이면 차단 + 쿨다운"
        return True, meta

    ratio = total_fees / total_gross
    meta["fee_ratio"] = ratio
    meta["max_fee_ratio"] = max_fee_ratio

    if ratio > max_fee_ratio:
        meta["reason"] = "fee_ratio_exceeded"
        return True, meta

    meta["reason"] = "ok"
    return False, meta

def main():
    # 1. System Setup
    install_signal_handlers()
    if not acquire_lock():
        sys.exit(1)
    
    try:
        LOGGER.info("🤖 Bot Initialization (Final Architecture)...")
        config = load_config()
        
        # 2. Module Initialization
        wrapper = UpbitAPIWrapper(config['upbit']['access_key'], config['upbit']['secret_key'])
        notifier = TelegramNotifier(
            bot_token=config['telegram']['bot_token'],
            chat_id=config['telegram']['chat_id']
        )
        
        market_filter = MarketFilter(wrapper)
        signal_engine = SignalEngine(config)
        risk_engine = RiskEngine(config)
        
        # Strategy Tuner (Enabled by default as requested)
        tuner = StrategyTuner(config, enabled=True) 

        daily_risk_mgr = DailyRiskManager(max_loss_pct=config['daily_risk']['max_loss_pct'])
        position_sizer = PositionSizer(
            base_pct=config['position_sizing']['base_pct'],
            min_size=config['position_sizing']['min_size'],
            max_cap=config['position_sizing']['max_cap'],
            cash_buf=config['position_sizing']['cash_buf']
        )

        markets = config['bot']['markets']
        timeframe = config['bot']['timeframe']
        
        # Memory State
        positions = {}
        
        # --- GLOBAL STATE for Gate & Frequency ---
        last_entry_time = None  # Last buy timestamp
        
        # Fee Monitor State
        # Stores dict: { 'gross_pnl': 1000, 'fee': 50, 'timestamp': ... }
        recent_trades_history = deque(maxlen=config['safety_pins']['fee_monitor']['window_trades']) 
        cooldown_until_ts = 0 # Use timestamp(float) or datetime? User code used float time.time()

        
        # Performance Tracking for Tuner
        # { 'trades_last_24h': 0, 'consecutive_losses': 0, 'wins_10': [], 'win_rate_10': 0.5 }
        perf_stats = {
            'trades_last_24h': 0,
            'consecutive_losses': 0,
            'wins_10': [], # Last 10 trades [1, 0, 1, ...]
            'win_rate_10': 0.5
        }
        last_reset_24h = datetime.now()

        # 3. Restore State
        LOGGER.info("Restoring positions from Upbit...")
        try:
            my_balances = wrapper.get_balances()
            for bal in my_balances:
                if bal['currency'] == "KRW": continue
                ticker = f"KRW-{bal['currency']}"
                if ticker in markets:
                    amount = float(bal['balance'])
                    avg = float(bal['avg_buy_price'])
                    if amount * avg < 5000: continue
                    
                    df = wrapper.get_ohlcv(ticker, interval=timeframe, count=20)
                    if df is not None:
                        risk_cfg = config['risk']
                        current_atr = (df['high'] - df['low']).mean()
                        sl_amt = max(avg * risk_cfg['sl_min_pct'], current_atr * risk_cfg['sl_atr_mult'])
                        
                        positions[ticker] = {
                            'entry_price': avg,
                            'sl': avg - sl_amt,
                            'tp': avg * (1 + risk_cfg['tp_target']),
                            'entry_time': datetime.now() 
                        }
                        LOGGER.info(f"Restored {ticker}: Entry {avg}")
        except Exception as e:
            LOGGER.error(f"Restore Failed: {e}")

        # Notify Start
        start_msg = f"🚀 Bot Started (Final Arch + Tuner 🧠)\nTargets: {len(markets)}\nRestored: {len(positions)}"
        notifier.send(start_msg)
        LOGGER.info("✅ Main Loop Started")

        # 4. Main Loop
        while RUNNING:
            try:
                # Stats Auto Reset (24h)
                if (datetime.now() - last_reset_24h).total_seconds() > 86400:
                    perf_stats['trades_last_24h'] = 0
                    last_reset_24h = datetime.now()

                # A-0. Fee Monitor Cooldown Check (Simple Log)
                if time.time() < cooldown_until_ts:
                    if datetime.now().second < 5:
                        remaining = int(cooldown_until_ts - time.time())
                        LOGGER.warning(f"❄️ Fee Monitor Cooldown Active ({remaining//60}m left)")
                    time.sleep(10)
                    continue

                # A. Risk Management (Daily & Equity)
                total_equity = wrapper.compute_total_equity()
                if not daily_risk_mgr.update(total_equity):
                    LOGGER.warning("💤 Daily Risk Limit Hit. Sleeping...")
                    time.sleep(60)
                    continue
                
                if position_sizer: 
                    # Refresh config dynamically if needed or rely on loop
                    pass

                # [VERIFY] Loop Status Log
                if datetime.now().second < 5: # Log periodically
                    open_cnt = len(positions)
                    exp_pct = (exposure / total_equity) * 100 if total_equity > 0 else 0
                    today_cnt = perf_stats['trades_last_24h']
                    # LOGGER.info(f"[STATUS] Open: {open_cnt} | Exposure: {exp_pct:.1f}% | TodayTrades: {today_cnt}/3") # Too spammy? Keep it.
                
                cash = wrapper.get_balance("KRW")
                exposure = total_equity - cash

                # B. Market Filter
                is_market_ok = market_filter.is_market_ok()
                if not is_market_ok:
                    LOGGER.info("📉 Market Bad (BTC Drop). Buys Paused.")

                # C. Strategy Loop
                for ticker in markets:
                    try:
                        has_position = (ticker in positions)
                        
                        # Data Fetch
                        df = wrapper.get_ohlcv(ticker, interval=timeframe, count=60)
                        if df is None: continue
                        current_price = df['close'].iloc[-1]
                        
                        # --- TUNER LOGIC ---
                        # Run tuner using BTC or current ticker data (Representative ticker like BTC is better for regime, but using each ticker helps individuality)
                        # The tuner example uses one DF. Let's use BTC for global regime or current for local.
                        # Using current dataframe for specific tuning might trigger too many changes if config is shared.
                        # Better to use BTC or ETH for 'Regime' if tuning global config.
                        # StrategyTuner updates 'current_cfg' which is global. So we should feed it BTC data or main index.
                        
                        # Let's fetch BTC data for tuning if not current
                        if ticker == "KRW-BTC":
                            tuned_cfg = tuner.tune(df, perf_stats)
                            # Apply to Engine
                            signal_engine.config = tuned_cfg

                        # --- EXIT LOGIC ---
                        if has_position:
                            pos = positions[ticker]
                            current_val = current_price * 0 # placeholder, needed for pnl calc
                            
                            # Check Trailing Stop if Partial Sold
                            is_partial_mode = pos.get('partial_sold', False)
                            
                            if is_partial_mode:
                                # Update Highest
                                if current_price > pos.get('highest_price', 0):
                                    pos['highest_price'] = current_price
                                
                                # Trailing Exit Condition (e.g. drop 1% from high or ATR based)
                                # Simple: 1.5% drop from high
                                trail_gap = pos['highest_price'] * 0.015 
                                if current_price < (pos['highest_price'] - trail_gap):
                                    is_exit = True
                                    reason = f"TrailingStop (High {pos['highest_price']})"
                                
                                # Hard SL (Break Even) check just in case
                                if current_price < pos['sl']:
                                    is_exit = True
                                    reason = "SL_BreakEven"

                            else:
                                # Normal Mode
                                is_exit, reason = risk_engine.check_exit(current_price, pos)
                                
                                # Partial TP Logic
                                # If hit TP and NOT TimeCut, do Partial Sell
                                if is_exit and reason == "TakeProfit":
                                    # Execute Partial Sell (50%)
                                    bal = wrapper.get_balance(ticker)
                                    if bal > 0:
                                        half_bal = bal * 0.5
                                        # Minimum order check (5000 KRW)
                                        if (half_bal * current_price) > 5000:
                                            order = wrapper.sell_market_safe(ticker, half_bal)
                                            if order:
                                                # Update Position State
                                                positions[ticker]['partial_sold'] = True
                                                positions[ticker]['sl'] = positions[ticker]['entry_price'] * 1.002 # Break Even + Fee Buffer
                                                positions[ticker]['highest_price'] = current_price
                                                msg = f"💰 Partial TP {ticker} (50%)\nSL moved to BE: {positions[ticker]['sl']}"
                                                LOGGER.info(msg)
                                                notifier.send(msg)
                                                
                                                # Record Profit for History (approx)
                                                # Fee Monitor needs 'Close' data. We treat partial as a trade?
                                                # For simplicity, we record stat only on FULL exit or just accumulate PnL.
                                                # Let's accumulate to history on full exit to keep logic simple.
                                                is_exit = False # Continue holding remainder
                            
                            # Time Cut (Conditional) - Only for non-partial positions? OR All?
                            # User said "Partial + Trailing" -> usually TimeLimit is relaxed or removed for trailing.
                            # Let's apply TimeLimit only if NOT partial sold (stagnant).
                            if not is_partial_mode and pos.get('entry_time'):
                                elapsed = (datetime.now() - pos['entry_time']).total_seconds() / 60
                                if elapsed > config['risk']['time_limit']:
                                    pnl_ratio = (current_price - pos['entry_price']) / pos['entry_price']
                                    if pnl_ratio > 0.001: 
                                        is_exit = True
                                        reason = f"TimeCut+Profit ({int(elapsed)}m)"
                                    else:
                                        if int(elapsed) % 10 == 0:
                                            LOGGER.debug(f"TimeCut Wait: {ticker} PnL {pnl_ratio*100:.2f}%")

                            if is_exit:
                                bal = wrapper.get_balance(ticker)
                                if bal > 0:
                                    order = wrapper.sell_market_safe(ticker, bal)
                                    if order:
                                        pnl = (current_price - pos['entry_price']) / pos['entry_price']
                                        sell_val = bal * current_price
                                        # Approx Fee (Buy + Sell) -> 0.05% * 2 = 0.1%
                                        # Precise: config['backtest']['fee_rate'] or hardcode 0.0005
                                        fee_val = sell_val * 0.0005 * 2 
                                        pnl_val = sell_val - (bal * pos['entry_price'])
                                        
                                        msg = f"📉 SELL {ticker}\nReason: {reason}\nPnL: {pnl*100:.2f}%"
                                        LOGGER.info(msg)
                                        notifier.send(msg)
                                        del positions[ticker]
                                        
                                        # Update Stats
                                        perf_stats['trades_last_24h'] += 1
                                        if pnl > 0:
                                            perf_stats['consecutive_losses'] = 0
                                            perf_stats['wins_10'].append(1)
                                        else:
                                            perf_stats['consecutive_losses'] += 1
                                            perf_stats['wins_10'].append(0)
                                        
                                        if len(perf_stats['wins_10']) > 10:
                                            perf_stats['wins_10'] = perf_stats['wins_10'][-10:]
                                        
                                        perf_stats['win_rate_10'] = sum(perf_stats['wins_10']) / len(perf_stats['wins_10']) if perf_stats['wins_10'] else 0.5

                                        # --- FEE MONITOR RECORD ---
                                        if config['safety_pins']['fee_monitor']['enabled']:
                                            recent_trades_history.append({
                                                'gross_pnl': pnl_val, # Assuming pnl_val is gross? 
                                                # pnl_val calculated as: sell_val - (bal * entry) -> This IS Gross PnL (before fees)
                                                'fee': fee_val,
                                                'timestamp': datetime.now().isoformat()
                                            })
                                            LOGGER.info(f"[TRADE HISTORY] Saved fee={fee_val:.1f} gross={pnl_val:.1f}")
                                        
                                else:
                                    del positions[ticker]
                            continue

                        # --- ENTRY LOGIC ---
                        if is_market_ok and not has_position:
                            # 1. Gate Check (Frequency & Max Positions)
                            gate_cfg = config['gate']
                            
                            # [NEW] Max Open Positions Check
                            if len(positions) >= gate_cfg.get('max_open_positions', 2):
                                # LOGGER.debug(f"Skipping {ticker}: Max Pos {len(positions)} Reached")
                                continue

                            if perf_stats['trades_last_24h'] >= gate_cfg['max_trades_per_day']:
                                continue
                            
                            # 2. Analyze Strategy
                            is_buy, sl, tp, info = signal_engine.analyze(df, btc_ok=True)
                            
                            if is_buy:
                                score = info['score']
                                adx_val = info.get('adx')
                                
                                # 3. Calculate Base Size
                                buy_amount = position_sizer.get_size(total_equity, cash, exposure)
                                
                                # [NEW] Total Exposure Cap Check
                                max_exp_pct = config['position_sizing'].get('max_total_exposure_pct', 0.35)
                                current_exp_pct = (exposure / total_equity) if total_equity > 0 else 0
                                projected_exp_pct = ((exposure + buy_amount) / total_equity)
                                
                                if projected_exp_pct > max_exp_pct:
                                    LOGGER.warning(f"🚫 Exposure Limit Block: {ticker} (Proj {projected_exp_pct*100:.1f}% > Max {max_exp_pct*100}%)")
                                    continue

                                # [VERIFICATION LOG] Position Size & Max Cap
                                limit_pct = config['position_sizing']['max_cap']
                                base_pct = config['position_sizing']['base_pct']
                                LOGGER.info(f"[VERIFY] Sizer Output: {buy_amount:,.0f} KRW (Equity {total_equity:,.0f} * Base {base_pct}) / MaxCap {limit_pct*100}%")

                                # 4. Cooldown Check (Global)
                                now_ts = time.time()
                                if now_ts < cooldown_until_ts:
                                    # Already checked at loop start, but double check
                                    continue
                                    
                                if last_entry_time is not None:
                                    mins_since = (datetime.now() - last_entry_time).total_seconds() / 60
                                    if mins_since < gate_cfg['min_minutes_between_entries']:
                                        continue

                                # 5. Fee Monitor Check (Trigger)
                                if config['safety_pins']['fee_monitor']['enabled']:
                                    triggered, fm_meta = fee_monitor_triggered(
                                        recent_trades_history, 
                                        max_fee_ratio=config['safety_pins']['fee_monitor']['max_fee_ratio']
                                    )
                                    LOGGER.info(f"[VERIFY] FeeMonitor: Ratio {fm_meta.get('fee_ratio',0):.4f} (Max {fm_meta['max_fee_ratio']}) / history {fm_meta['window']}")
                                    
                                    if triggered:
                                        cooldown_mins = config['safety_pins']['fee_monitor']['cooldown_minutes_on_trigger']
                                        cooldown_until_ts = now_ts + (cooldown_mins * 60)
                                        LOGGER.warning(f"[FEE MONITOR BLOCK] {ticker} meta={fm_meta} -> cooldown {cooldown_mins}m")
                                        continue

                                # 6. Side Mode (ADX)
                                side_cfg = config['safety_pins']['side_mode']
                                if side_cfg['enabled']:
                                    # Handle ADX None/NaN
                                    if adx_val is not None and float(adx_val) < side_cfg['adx_side_threshold']:
                                        original_amount = buy_amount
                                        buy_amount = buy_amount * side_cfg['side_risk_mult']
                                        LOGGER.info(f"[VERIFY] 🛡️ Side Mode Active (ADX {float(adx_val):.2f} < {side_cfg['adx_side_threshold']})")
                                        LOGGER.info(f"[VERIFY] Size Reduced: {original_amount:,.0f} -> {buy_amount:,.0f} (*{side_cfg['side_risk_mult']})")

                                # 7. Min Value Check
                                if buy_amount < 5000:
                                    LOGGER.warning(f"Skipping {ticker}: Size {buy_amount:.0f} < Min")
                                    continue
                                
                                # 9. Order Execution
                                # [NEW] Trend Boost Logic
                                trend_cfg = config['safety_pins'].get('trend_boost', {})
                                final_tp_target = config['risk']['tp_target']
                                
                                is_boosted = False
                                if trend_cfg.get('enabled', False):
                                    # Info 'adx' passed from Analyze
                                    adx_val = info.get('adx', 0)
                                    if adx_val and float(adx_val) >= trend_cfg['adx_threshold']:
                                        # Boost Size
                                        boost_size = total_equity * trend_cfg['boost_size_pct']
                                        # Max Cap Check
                                        max_cap_amt = total_equity * config['position_sizing']['max_cap']
                                        boost_size = min(boost_size, max_cap_amt)
                                        
                                        # If boost is greater than current (and greater than min), apply
                                        if boost_size > buy_amount:
                                            buy_amount = boost_size
                                            is_boosted = True
                                        
                                        # Boost TP
                                        final_tp_target = trend_cfg['boost_tp_target']
                                        LOGGER.info(f"🚀 Trend Boost Activated! (ADX {float(adx_val):.1f}) -> Size {buy_amount:,.0f}, TP {final_tp_target*100}%")

                                order = wrapper.buy_market(ticker, buy_amount)
                                if order:
                                    entry_price = float(order['price']) # or executed price
                                    # Fallback if price is missing
                                    if entry_price == 0: entry_price = current_price
                                    
                                    # SL / TP
                                    sl_min_pct = config['risk']['sl_min_pct']
                                    sl_atr_mult = config['risk']['sl_atr_mult']
                                    atr = info.get('atr', 0) # Assuming ATR is available in info
                                    sl_amt = max(entry_price * sl_min_pct, atr * sl_atr_mult)
                                    
                                    # Calculate TP based on (Boosted or Normal) target
                                    tp_price = entry_price * (1 + final_tp_target)
                                    sl_price = entry_price - sl_amt
                                    
                                    positions[ticker] = {
                                        'entry_price': entry_price,
                                        'sl': sl_price,
                                        'tp': tp_price,
                                        'entry_time': datetime.now(),
                                        'partial_sold': False,
                                        'highest_price': entry_price
                                    }
                                    last_entry_time = datetime.now() # Update Gate
                                    
                                    msg = f"🚀 BUY {ticker}\nPrice: {entry_price}\nScore: {score}\nSize: {buy_amount:.0f}\nADX: {adx_val:.1f if adx_val else 'N/A'}"
                                    LOGGER.info(msg)
                                    notifier.send(msg)
                                    
                    except Exception as e:
                        LOGGER.error(f"Ticker Loop Error ({ticker}): {e}")

                time.sleep(1) # Loop Interval
                
            except Exception as e:
                LOGGER.error(f"Global Loop Error: {e}")
                time.sleep(5)
                
    finally:
        LOGGER.info("👋 Bot Shutdown. Releasing Lock.")
        release_lock()

if __name__ == "__main__":
    main()
