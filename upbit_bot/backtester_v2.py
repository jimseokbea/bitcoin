import yaml
import pandas as pd
import pyupbit
import time
from core.strategy_modules import SignalEngine, RiskEngine

def run_backtest():
    # 1. Load Config
    with open("config/settings.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 2. Initialize Engines
    signal_engine = SignalEngine(config)
    
    # 3. Load Data
    ticker = config['bot']['ticker']
    interval = config['bot']['timeframe']
    print(f"Fetching data for {ticker} ({interval})...")
    
    # Backtest with recent 4000 candles (approx 14 days)
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=4000)
    
    if df is None:
        print("Failed to fetch data.")
        return

    # 4. Analyze
    # For backtest, we need to iterate row by row or apply rolling functions.
    # SignalEngine accepts a dataframe and analyzes the *last* row usually.
    # But for backtest, we modify it to analyze historical rows.
    # We will simulate the loop.
    
    trades = []
    position = None # { 'entry_price': ..., 'sl': ..., 'tp': ..., 'entry_time': ... }
    
    # Pre-calculate indicators for speed (SignalEngine internal method does this but for single use)
    # We will use the engines _add_indicators on the full dataframe first.
    df_analyzed = signal_engine._add_indicators(df.copy())
    
    # Iterate
    # Start from index 30 to have enough data for indicators
    for i in range(30, len(df_analyzed) - 1):
        curr_row = df_analyzed.iloc[i]   # "Analysis time"
        next_row = df_analyzed.iloc[i+1] # "Execution time" (open of next candle)
        
        # Current time price (Close of analysis candle)
        current_price = curr_row['close']
        current_time = curr_row.name
        
        # --- EXIT LOGIC ---
        if position:
            # Check Low/High of NEXT candle for SL/TP hits??
            # Usually we check if price moved against us in the holding period.
            # Simplified: Check against next candle's High/Low
            
            # 1. SL check (Low)
            if next_row['low'] <= position['sl']:
                pnl = (position['sl'] - position['entry_price']) / position['entry_price']
                trades.append({'type': 'SL', 'pnl': pnl, 'days': next_row.name})
                position = None
                continue # Trade ended

            # 2. TP check (High)
            if next_row['high'] >= position['tp']:
                pnl = (position['tp'] - position['entry_price']) / position['entry_price']
                trades.append({'type': 'TP', 'pnl': pnl, 'days': next_row.name})
                position = None
                continue

            # 3. Time limit check (using index or time)
            # if (next_row.name - position['entry_time']) ...
            pass
        
        # --- ENTRY LOGIC ---
        # Analyze current row to see if we should enter at next open
        if position is None:
            # We need to construct a "row" that looks like what SignalEngine expects
            # SignalEngine expects a row with 'prev_*' columns.
            # Our df_analyzed ALREADY has prev_* columns shifted.
            # So passing curr_row to calculate_score should work if inputs match.
            
            score, details = signal_engine.calculate_score(curr_row, btc_ok=True)
            
            if score >= config['entry_threshold']:
                # ENTER at Next Open
                entry_price = next_row['open']
                atr = curr_row['atr']
                
                # SL/TP Calculation
                # Re-using logic from SignalEngine/RiskEngine or calculating here manually based on config
                risk_cfg = config['risk']
                sl_amt = max(entry_price * risk_cfg['sl_min_pct'], atr * risk_cfg['sl_atr_mult'])
                sl_price = entry_price - sl_amt
                tp_price = entry_price * (1 + risk_cfg['tp_target'])
                
                position = {
                    'entry_price': entry_price,
                    'sl': sl_price,
                    'tp': tp_price,
                    'entry_time': next_row.name
                }
                # print(f"[{next_row.name}] BUY @ {entry_price} (Score: {score:.1f} {details})")

    # 5. Results
    if trades:
        win = len([t for t in trades if t['pnl'] > 0])
        total = len(trades)
        acc = win / total * 100
        cum_pnl = sum([t['pnl'] for t in trades]) * 100
        
        print(f"=== Backtest Results ({ticker}) ===")
        print(f"Total Trades: {total}")
        print(f"Win Rate: {acc:.2f}%")
        print(f"Cumulative PnL: {cum_pnl:.2f}% (No Compounding)")
        print("Last 5 Trades:", trades[-5:])
    else:
        print("No trades generated.")

if __name__ == "__main__":
    run_backtest()
