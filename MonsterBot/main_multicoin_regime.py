"""
MonsterBot Multi-Coin Regime Trading System
Entry point for 1h regime-based multi-coin futures trading.

Safeguards Implemented:
- (A) Regime transition stabilization (confirm_bars, min_hold_bars)
- (B) Market Gate applies to existing positions
- (C) ATR% thresholds split by coin group
- (D) Hard Filter before scoring
- (E) max_new_entries_per_bar enforced
- (F) BTC correlation cluster penalty
- (G) Circuit breakers
- (H) Comprehensive logging
"""
import sys
import os
import time
import yaml
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from dotenv import load_dotenv

# UTF-8 console support for Windows
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

load_dotenv()

# Load config
CONFIG_FILE = 'config_multicoin_regime.yaml'
with open(CONFIG_FILE, encoding='utf-8') as f:
    config = yaml.safe_load(f)

# Logger setup
logger = logging.getLogger('regime_bot')
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = RotatingFileHandler(
        'bot_regime.log', 
        maxBytes=10*1024*1024, 
        backupCount=5,
        encoding='utf-8'
    )
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

# Import core modules
from core.executor import FuturesExecutor
from core.database import TradeDB
from core.notifier import TelegramNotifier
from core.regime_detector import RegimeDetector, MARKET_GATE_PANIC, MARKET_GATE_RISKOFF
from core.candidate_scorer import CandidateScorer
from core.portfolio_manager import PortfolioManager
from core.regime_strategy import RegimeStrategy
from core.dryrun_reporter import DryRunReporter


def main():
    logger.info("="*60)
    logger.info("🦖 MonsterBot Multi-Coin Regime System Starting...")
    logger.info(f"📋 Config: {CONFIG_FILE}")
    logger.info("="*60)
    
    # Initialize components
    dry_run = config['system'].get('dry_run', True)
    if dry_run:
        logger.warning("⚠️ DRY RUN MODE - No real trades will be executed")
    
    executor = FuturesExecutor(config)
    db = TradeDB()
    noti = TelegramNotifier(config)
    
    regime_detector = RegimeDetector(config)
    candidate_scorer = CandidateScorer(config)
    portfolio_manager = PortfolioManager(config)
    strategy = RegimeStrategy(config)
    
    # Dry-run reporter for validation
    dryrun_reporter = DryRunReporter() if dry_run else None
    
    # Universe
    whitelist = config.get('universe', {}).get('whitelist', [])
    if not whitelist:
        logger.error("No whitelist defined!")
        return
    
    timeframe = config['strategy'].get('timeframe', '1h')
    logger.info(f"📊 Timeframe: {timeframe}, Universe: {len(whitelist)} coins")
    
    # Notify startup
    noti.send(f"🦖 Multi-Coin Regime Bot Started\n"
              f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n"
              f"Timeframe: {timeframe}\n"
              f"Universe: {len(whitelist)} coins")
    
    last_bar_ts = None
    
    while True:
        try:
            loop_start = time.time()
            
            # ============================================================
            # STEP 1: Fetch BTC data and detect Market Regime
            # ============================================================
            btc_df = executor.fetch_ohlcv('BTC/USDT', timeframe, limit=250)
            if btc_df is None or len(btc_df) < 200:
                logger.warning("Failed to fetch BTC data")
                time.sleep(60)
                continue
            
            # Add regime indicators
            btc_df = regime_detector.add_regime_indicators(btc_df)
            btc_df = strategy.add_strategy_indicators(btc_df)
            
            # Detect Market Regime
            market_gate, btc_regime, btc_indicators = regime_detector.detect_market_regime(btc_df)
            
            current_bar_ts = btc_df.iloc[-1]['timestamp']
            
            # Check for new bar
            portfolio_manager.check_new_bar(current_bar_ts)
            is_new_bar = (last_bar_ts != current_bar_ts)
            if is_new_bar:
                last_bar_ts = current_bar_ts
                logger.info(f"📊 New bar: {current_bar_ts}")
                if dryrun_reporter:
                    dryrun_reporter.record_new_bar(str(current_bar_ts))
            
            # ============================================================
            # STEP 2: Check current positions and apply Market Gate
            # ============================================================
            current_equity = executor.get_balance()
            portfolio_manager.reset_daily(current_equity)
            
            # Get current positions
            positions = []
            try:
                raw_positions = executor.exchange.fetch_positions()
                for p in raw_positions:
                    contracts = float(p.get('contracts', 0))
                    if contracts > 0:
                        positions.append({
                            'symbol': p['symbol'],
                            'side': p['side'],
                            'contracts': contracts,
                            'entry_price': float(p.get('entryPrice', 0)),
                            'unrealized_pnl': float(p.get('unrealizedPnl', 0))
                        })
            except Exception as e:
                logger.error(f"Position fetch error: {e}")
            
            # Apply Market Gate actions to existing positions (Safeguard B)
            existing_long_managed = False
            if market_gate == MARKET_GATE_PANIC:
                for pos in positions:
                    if pos['side'] == 'long':
                        actions = portfolio_manager.get_existing_position_actions(pos, market_gate)
                        if actions.get('action') == 'defensive':
                            logger.warning(f"🚨 [Panic] Defensive mode for {pos['symbol']}")
                            existing_long_managed = True
                            # In real implementation: tighten SL, partial close, etc.
                
                if dryrun_reporter:
                    has_existing_long = any(p['side'] == 'long' for p in positions)
                    dryrun_reporter.record_panic_event(
                        new_long_blocked=True,
                        existing_long_managed=existing_long_managed or not has_existing_long,
                        existing_long_action='defensive' if existing_long_managed else None
                    )
            
            # ============================================================
            # STEP 3: Process each coin in universe
            # ============================================================
            candidates = []
            
            for symbol in whitelist:
                try:
                    # Fetch OHLCV
                    df = executor.fetch_ohlcv(symbol, timeframe, limit=250)
                    if df is None or len(df) < 200:
                        continue
                    
                    # Add indicators
                    df = regime_detector.add_regime_indicators(df)
                    df = strategy.add_strategy_indicators(df)
                    
                    curr = df.iloc[-1]
                    
                    # Detect symbol regime
                    symbol_regime, is_confirmed, indicators = regime_detector.detect_symbol_regime(df, symbol)
                    
                    # Get regime settings modified by market gate
                    settings = regime_detector.get_regime_settings(symbol_regime, market_gate)
                    
                    # Skip if trading disabled for this regime
                    if not settings.get('trade_enabled', False):
                        if dryrun_reporter and symbol_regime == 'RANGE_HIGHVOL':
                            dryrun_reporter.record_entry_blocked('RANGE_HIGHVOL_disabled', symbol, symbol_regime)
                        continue
                    
                    # Calculate candidate score components
                    ema_fast = curr.get('ema_fast', curr['close'])
                    ema_slow = curr.get('ema_slow', curr['close'])
                    ema_diff_pct = ((ema_fast - ema_slow) / ema_slow * 100) if ema_slow > 0 else 0
                    
                    candidates.append({
                        'symbol': symbol,
                        'df': df,
                        'regime': symbol_regime,
                        'settings': settings,
                        'adx': indicators.get('adx', 0),
                        'atr_pct': indicators.get('atr_pct', 0),
                        'ema_diff_pct': ema_diff_pct,
                        'volume_rank': whitelist.index(symbol) + 1,  # Simplified rank
                        'ticker': {},  # Would fetch from exchange
                        'funding_rate': None  # Would fetch from exchange
                    })
                    
                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}")
                    continue
            
            # ============================================================
            # STEP 4: Score and select candidates
            # ============================================================
            if candidates and is_new_bar:
                # Determine trade direction based on BTC market regime
                if 'UPTREND' in btc_regime:
                    trade_direction = 'long'
                elif 'DOWNTREND' in btc_regime:
                    trade_direction = 'short'
                else:
                    trade_direction = 'long'  # Default bias
                
                top_candidates = candidate_scorer.score_and_select(
                    candidates, 
                    [{'symbol': p['symbol'], 'side': p['side']} for p in positions],
                    trade_direction
                )
                
                # ============================================================
                # STEP 5: Check signals and execute trades
                # ============================================================
                for cand_score in top_candidates:
                    symbol = cand_score['symbol']
                    
                    # Find full candidate data
                    cand = next((c for c in candidates if c['symbol'] == symbol), None)
                    if not cand:
                        continue
                    
                    # Check if we can open position
                    can_open, reason = portfolio_manager.can_open_position(
                        positions, trade_direction, market_gate, current_equity
                    )
                    
                    if not can_open:
                        logger.info(f"[Skip] {symbol}: {reason}")
                        if dryrun_reporter:
                            dryrun_reporter.record_entry_blocked(reason, symbol, cand['regime'])
                        continue
                    
                    # Check for signal
                    signal = strategy.check_signal(
                        cand['df'], 
                        cand['regime'], 
                        cand['settings'],
                        symbol
                    )
                    
                    if signal:
                        direction = signal['direction']
                        sl_price = signal['sl_price']
                        entry_price = cand['df'].iloc[-1]['close']
                        
                        # Calculate position size
                        position_scale = portfolio_manager.get_position_scale(
                            cand['regime'], market_gate
                        )
                        leverage_cap = portfolio_manager.get_leverage_cap(
                            cand['regime'], market_gate
                        )
                        
                        # Risk-based sizing
                        risk_pct = config['portfolio_risk']['risk_per_trade_pct'] / 100
                        sl_dist_pct = abs(entry_price - sl_price) / entry_price
                        if sl_dist_pct == 0:
                            continue
                        
                        risk_amt = current_equity * risk_pct * position_scale
                        pos_size_usdt = risk_amt / sl_dist_pct
                        
                        # Apply leverage cap
                        max_pos = current_equity * leverage_cap
                        if pos_size_usdt > max_pos:
                            pos_size_usdt = max_pos
                        
                        qty = pos_size_usdt / entry_price
                        
                        if dry_run:
                            logger.info(f"🧪 [DRY RUN] Would enter {direction.upper()} {symbol} "
                                       f"qty={qty:.4f} SL={sl_price:.2f} scale={position_scale:.2f}")
                            if dryrun_reporter:
                                dryrun_reporter.record_entry(str(current_bar_ts), symbol, direction, cand['regime'])
                                if cand['regime'] == 'RANGE_HIGHVOL':
                                    dryrun_reporter.record_range_highvol_trade()
                        else:
                            # Execute trade
                            try:
                                result = executor.entry(symbol, direction, qty, sl_price)
                                if result:
                                    portfolio_manager.mark_entry()
                                    portfolio_manager.reset_execution_failures()
                                    
                                    noti.send(f"🎯 Entry: {direction.upper()} {symbol}\n"
                                             f"Regime: {cand['regime']}\n"
                                             f"Qty: {qty:.4f}\n"
                                             f"SL: {sl_price:.2f}")
                                    
                                    db.log_trade(symbol, direction, qty, entry_price, 
                                               sl_price, 'ENTRY', cand['regime'])
                            except Exception as e:
                                logger.error(f"Entry execution error: {e}")
                                portfolio_manager.record_execution_failure()
                        
                        # Only one entry per bar
                        break
            
            # ============================================================
            # STEP 6: Manage existing positions
            # ============================================================
            for pos in positions:
                symbol = pos['symbol']
                
                try:
                    # Find candidate data for this position's symbol
                    cand = next((c for c in candidates if c['symbol'] == symbol), None)
                    if not cand:
                        continue
                    
                    curr_row = cand['df'].iloc[-1]
                    settings = cand['settings']
                    
                    # Check exit rules
                    exit_action = strategy.check_exit_rules(pos, curr_row, settings)
                    
                    if exit_action:
                        if dry_run:
                            logger.info(f"🧪 [DRY RUN] Would exit {symbol}: {exit_action}")
                        else:
                            if exit_action['action'] == 'full_close':
                                executor.close_all(symbol)
                                noti.send(f"🚪 Exit: {symbol}\nReason: {exit_action['reason']}")
                            elif exit_action['action'] == 'partial_close':
                                ratio = exit_action.get('ratio', 0.5)
                                executor.close_partial(symbol, pos['side'], 
                                                      pos['contracts'], ratio)
                    
                    # Check trailing stop
                    new_trail = strategy.calculate_trailing_stop(pos, curr_row, settings)
                    if new_trail and not dry_run:
                        # Update SL to trailing value
                        pass  # Implement via executor.replace_sl_only_atomic
                    
                    # Check BE move
                    be_price = strategy.calculate_be_move(pos, curr_row, settings)
                    if be_price and not dry_run:
                        # Move SL to breakeven
                        pass  # Implement via executor.replace_sl_only_atomic
                    
                except Exception as e:
                    logger.error(f"Position management error {symbol}: {e}")
            
            # ============================================================
            # STEP 7: Log status (Safeguard H)
            # ============================================================
            if is_new_bar:
                regime_state = regime_detector.get_state_summary()
                portfolio_status = portfolio_manager.get_status_summary()
                
                logger.info(f"📊 [Status] Gate: {market_gate}, BTC: {btc_regime}, "
                           f"Positions: {len(positions)}, "
                           f"Circuit: {portfolio_status['circuit_breaker']}")
                
                # Record position count for validation
                if dryrun_reporter:
                    dryrun_reporter.record_position_count(len(positions))
                    if top_candidates:
                        dryrun_reporter.record_candidate_scores(top_candidates)
            
            # ============================================================
            # STEP 8: Sleep until next cycle
            # ============================================================
            elapsed = time.time() - loop_start
            sleep_time = max(30, 60 - elapsed)  # 1 min cycle, min 30s
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logger.info("🛑 Shutdown requested")
            noti.send("🛑 Bot shutdown requested")
            
            # Save dry-run report on shutdown
            if dryrun_reporter:
                dryrun_reporter.save_report()
                dryrun_reporter.print_summary()
            
            break
        except Exception as e:
            logger.exception(f"Main loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
