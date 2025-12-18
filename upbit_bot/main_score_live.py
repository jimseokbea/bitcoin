import time
import yaml
import sys
from datetime import datetime

# Core Imports
from core.system_utils import (
    LOGGER, LOCK_FILE, RUNNING, install_signal_handlers, 
    acquire_lock, release_lock, LIMITER
)
from core.wrapper import UpbitAPIWrapper
from core.risk_manager import DailyRiskManager, PositionSizer
from core.strategy_modules import MarketFilter, SignalEngine, RiskEngine
from core.strategy_tuner import StrategyTuner

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

                # A. Risk Management (Daily & Equity)
                total_equity = wrapper.compute_total_equity()
                if not daily_risk_mgr.update(total_equity):
                    LOGGER.warning("💤 Daily Risk Limit Hit. Sleeping...")
                    time.sleep(60)
                    continue
                
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
                            is_exit, reason = risk_engine.check_exit(current_price, pos)
                            
                            # Time Cut (Conditional)
                            if pos.get('entry_time'):
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
                                        
                                else:
                                    del positions[ticker]
                            continue

                        # --- ENTRY LOGIC ---
                        if is_market_ok and not has_position:
                            # Use tuned engine
                            is_buy, sl, tp, info = signal_engine.analyze(df, btc_ok=True)
                            
                            if is_buy:
                                score = info['score']
                                LOGGER.info(f"Signal {ticker} Score {score}")
                                
                                buy_amount = position_sizer.get_size(total_equity, cash, exposure)
                                if buy_amount < 5000:
                                    LOGGER.warning(f"Skipping {ticker}: Size {buy_amount} < Min")
                                    continue
                                
                                order = wrapper.buy_market(ticker, buy_amount)
                                if order:
                                    real_price = current_price
                                    positions[ticker] = {
                                        'entry_price': real_price,
                                        'sl': sl,
                                        'tp': tp,
                                        'entry_time': datetime.now()
                                    }
                                    msg = f"🚀 BUY {ticker}\nPrice: {real_price}\nScore: {score}\nSL: {sl} / TP: {tp}"
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
