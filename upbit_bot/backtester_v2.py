import yaml
import pandas as pd
import sys
import os
import time
# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.strategy_modules import SignalEngine

def run_backtest():
    # 1. Load Config
    config_path = os.path.join(os.path.dirname(__file__), "config", "settings.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 2. Initialize Engines
    # SignalEngine expects config dict
    signal_engine = SignalEngine(config)
    
    # 3. Load Data
    # Use the CSV path from config or default to found candles
    # config['backtest']['csv_path'] might be outdated, let's use the one found earlier
    csv_path = "upbit_bot/data/candles/btc-5m-202206-luna-crash.csv" 
    # Or try to load specific ticker data if available?
    # User had btc-5m data. Let's use that for testing BTC strategy or assume logic holds.
    # But settings says ticker: KRW-DOGE. We only have BTC data in the context.
    # Let's use the BTC data for verification.
    
    print(f"Loading data from {csv_path}...")
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found.")
        return

    df = pd.read_csv(csv_path)
    # Ensure columns match (pyupbit/ccxt standard)
    # File has: timestamp,date_kst,open,high,low,close,volume
    df.rename(columns={'timestamp': 'date'}, inplace=True) # or keep index
    # We rely on indices mainly or SignalEngine might expect specific cols.
    # SignalEngine uses: close, high, low, volume, open. All present (lowercase).
    
    # 4. Analyze Pre-calculation
    print("Calculating indicators...")
    # SignalEngine._add_indicators returns a new DF with indicators
    df_analyzed = signal_engine._add_indicators(df.copy())
    
    # 5. Iteration
    trades = []
    # Position: { 'entry_price': ..., 'sl': ..., 'tp': ..., 'size': ..., 'partial_sold': False, 'highest_price': ... }
    position = None 
    
    initial_balance = 100000000 # 100M KRW start
    balance = initial_balance
    
    print("Running Loop...")
    # Full data for crash stress test
    start_idx = 50 
    print(f"Running full LUNA crash data (~1 month).")
    
    for i in range(start_idx, len(df_analyzed) - 1):
        curr_row = df_analyzed.iloc[i]   # Signal Time
        next_row = df_analyzed.iloc[i+1] # Execution Time (Open) / Monitor Time (High/Low)
        
        current_price = curr_row['close']
        
        # --- EXIT LOGIC (simulated on next candle OHLC) ---
        if position:
            # Update Highest Logic (Trailing Check)
            if position.get('partial_sold'):
                if next_row['high'] > position['highest_price']:
                    position['highest_price'] = next_row['high']
            
            pnl_amt = 0
            fee_amt = 0
            exit_type = None
            
            # Order of checks: Low (SL) -> High (TP) assumption or vice versa?
            # Conservative: Check Low first (Stop Loss)
            
            # 1. SL Check
            if next_row['low'] <= position['sl']:
                exit_price = position['sl']
                # But if Open was already below SL, we slipped.
                if next_row['open'] < position['sl']:
                    exit_price = next_row['open']
                
                exit_type = "SL_BreakEven" if position['partial_sold'] else "StopLoss"
                
                # Execute Sell
                size = position['size']
                sell_val = size * exit_price
                fee = sell_val * 0.0005
                pnl = sell_val - (size * position['entry_price']) # Gross PnL
                
                balance += (sell_val - fee)
                trades.append({
                    'type': exit_type,
                    'entry': position['entry_price'],
                    'exit': exit_price,
                    'pnl_pct': (exit_price - position['entry_price'])/position['entry_price'],
                    'realized_pnl': (sell_val - fee) - (size * position['entry_price']) # Net PnL (approx)
                })
                position = None
                continue

            # 2. TP Check (Partial 50%)
            if not position['partial_sold'] and next_row['high'] >= position['tp']:
                tp_price = position['tp']
                # If Open > TP, we might get open price
                if next_row['open'] > position['tp']:
                    tp_price = next_row['open']
                
                # Sell 50%
                sold_size = position['size'] * 0.5
                position['size'] -= sold_size
                position['partial_sold'] = True
                position['highest_price'] = tp_price
                # Move SL to Entry (Break Even) + Buffer
                position['sl'] = position['entry_price'] * 1.002
                
                sell_val = sold_size * tp_price
                fee = sell_val * 0.0005
                balance += (sell_val - fee)
                
                trades.append({
                    'type': "Partial_TP",
                    'entry': position['entry_price'],
                    'exit': tp_price,
                    'pnl_pct': (tp_price - position['entry_price'])/position['entry_price'],
                    'realized_pnl': (sell_val - fee) - (sold_size * position['entry_price'])
                })
                # Don't continue, keep holding remainder

            # 3. Trailing Stop Check (Only if partial sold)
            if position.get('partial_sold'):
                # 1.5% drop from highest
                trail_price = position['highest_price'] * (1 - 0.015)
                
                # if High reached new max, we updated highest_price.
                # Check if Low hit trail_price
                if next_row['low'] <= trail_price:
                    exit_price = trail_price
                    if next_row['open'] < trail_price: 
                        exit_price = next_row['open']
                        
                    exit_type = "TrailingStop"
                    
                    size = position['size']
                    sell_val = size * exit_price
                    fee = sell_val * 0.0005
                    balance += (sell_val - fee)
                    
                    trades.append({
                        'type': exit_type,
                        'entry': position['entry_price'],
                        'exit': exit_price,
                        'pnl_pct': (exit_price - position['entry_price'])/position['entry_price'],
                        'realized_pnl': (sell_val - fee) - (size * position['entry_price'])
                    })
                    position = None
                    continue

        # --- ENTRY LOGIC ---
        if position is None:
            # OPTIMIZATION: Do not call signal_engine.analyze() which recalculates indicators!
            # Use pre-calculated curr_row.
            
            # 1. Min Volatility Filter
            min_vol_pct = config['risk'].get('min_volatility_pct', 0.0)
            atr = curr_row['atr']
            if (atr / current_price) < min_vol_pct:
                continue

            # 2. BTC Gate Check (Simulated Always True or passed)
            btc_ok = True 
            
            score, details = signal_engine.calculate_score(curr_row, btc_ok=btc_ok)
            
            if score >= config['entry_threshold']:
                # ENTER at Next Open
                entry_price = next_row['open']
                
                # SL/TP Calculation
                risk_cfg = config['risk']
                sl_min_pct = risk_cfg['sl_min_pct']
                sl_atr_mult = risk_cfg['sl_atr_mult']
                sl_amt = max(entry_price * sl_min_pct, atr * sl_atr_mult)
                sl_price = entry_price - sl_amt
                
                # TP = 1.2% (Initial Target)
                tp_target = risk_cfg['tp_target']
                tp_price = entry_price * (1 + tp_target)
                
                # Side Mode Size Adjustment
                base_pct = config['position_sizing']['base_pct']
                allocation = balance * base_pct
                
                adx = curr_row.get('adx', 25) # Use pre-calc ADX
                
                # "Side Mode" Logic
                side_cfg = config['safety_pins']['side_mode']
                # Manual Check
                if side_cfg['enabled'] and (adx < side_cfg['adx_side_threshold']):
                     allocation *= side_cfg['side_risk_mult']
                
                # [NEW] Trend Boost Logic
                trend_cfg = config['safety_pins'].get('trend_boost', {})
                if trend_cfg.get('enabled', False) and (adx >= trend_cfg['adx_threshold']):
                     # Boost Size
                     boost_size = balance * trend_cfg['boost_size_pct']
                     # Max Cap Check
                     max_cap_amt = balance * config['position_sizing']['max_cap']
                     boost_size = min(boost_size, max_cap_amt)
                     if boost_size > allocation:
                         allocation = boost_size
                         # Boost TP
                         tp_target = trend_cfg['boost_tp_target']
                         tp_price = entry_price * (1 + tp_target)
                         # print(f"🚀 Trend Boost: ADX {adx:.1f} Size {allocation:,.0f} TP {tp_target*100}%")

                if allocation < 5000: continue # Min limit
                
                buy_size = allocation / entry_price
                fee = allocation * 0.0005
                balance -= (allocation + fee)
                
                position = {
                    'entry_price': entry_price,
                    'sl': sl_price,
                    'tp': tp_price,
                    'size': buy_size,
                    'partial_sold': False,
                    'highest_price': entry_price
                }
                # print(f"[{next_row['date']}] BUY {entry_price:.0f} (Score:{score} ADX:{adx:.1f})")

    # 6. Report
    print(f"\n=== Backtest Report ===")
    print(f"Period: {df_analyzed['date'].iloc[0]} ~ {df_analyzed['date'].iloc[-1]}")
    print(f"Start Balance: {initial_balance:,.0f}")
    print(f"End Balance:   {balance:,.0f}")
    
    # Positions still open?
    if position:
        val = position['size'] * df_analyzed.iloc[-1]['close']
        balance += val
        print(f"End Equity:    {balance:,.0f} (Open Pos Valued)")
    
    total_ret = (balance - initial_balance) / initial_balance * 100
    print(f"Total Return:  {total_ret:.2f}%")
    print(f"Total Trades:  {len(trades)}")
    
    wins = [t for t in trades if t['pnl_pct'] > 0]
    if trades:
        wr = len(wins) / len(trades) * 100
        print(f"Win Rate:      {wr:.2f}%")
        
        # Breakdown by exit type
        types = set(t['type'] for t in trades)
        for t in types:
            cnt = len([x for x in trades if x['type'] == t])
            print(f" - {t}: {cnt}")
            
    # Save trades to csv
    pd.DataFrame(trades).to_csv("upbit_bot/backtest_trades.csv", index=False)
    print("Trades saved to upbit_bot/backtest_trades.csv")

if __name__ == "__main__":
    run_backtest()
