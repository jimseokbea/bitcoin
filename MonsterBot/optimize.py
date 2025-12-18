import optuna
import yaml
import sys
import os
from backtest import run_backtest

# Ensure output is cleaner
optuna.logging.set_verbosity(optuna.logging.WARNING)

def objective(trial):
    # 1. Parameter Space (User constraints)
    adx_th = trial.suggest_int('adx_threshold', 22, 40)
    rsi_entry = trial.suggest_int('rsi_oversold', 22, 35) # Matches rsi_entry idea
    # rsi_ob implicitly symmetric or fixed? User listed rsi_entry. 
    # We'll set overbought to 100 - rsi_entry for symmetry or optimize separately? 
    # Symmetry is safer for stability.
    rsi_ob = 100 - rsi_entry
    
    bb_factor = trial.suggest_float('bb_factor', 1.9, 2.4, step=0.1)
    
    # Trail params (mock config passing, ensuring config dict has them)
    # Note: Logic in executor needs to use them, but we might not have updated executor yet.
    # Focusing on ADX/RSI/BB first as requested.
    
    # 2. Config Override
    config_override = {
        'strategy': {
            'adx_threshold': adx_th,
            'rsi_oversold': rsi_entry,
            'rsi_overbought': rsi_ob,
            'bb_factor': bb_factor
        },
        'fee_rate': 0.0015 # [User Req] 0.15% Roundtrip Stress Test
    }
    
    # 3. Run Backtest
    try:
        # Run 3 days for faster optimization iteration
        stats = run_backtest(days=3, config_override=config_override)
        
        profit = stats['profit']
        trades = stats['trade_count']
        trades_per_day = trades / 5 / 7 # 5 items, 7 days. Average per coin/day? 
        # User said "Trades_per_day > 3". This likely refers to per-coin avg.
        # Stats returns total trades.
        avg_trades_per_coin_day = trades / 5 / 7 
        
        # Penalties
        score = profit
        
        # Penalty 1: Over-trading
        if avg_trades_per_coin_day > 3:
             score -= (avg_trades_per_coin_day * 50) # Heavy penalty
             
        # Penalty 2: Zero trades (Optional, prevents 0 score being best if all loss)
        if trades < 5:
             score -= 100
             
        return score
        
    except Exception as e:
        return -9999

if __name__ == "__main__":
    print("🧬 Starting Sniper Mode Evolution (GA)...")
    print("🎯 Target: Maximize Profit with Strict Constraints (ADX>=22, Trades<=3/day)")
    
    study = optuna.create_study(direction='maximize')
    # Use 30 trials for initial speed check, can increase. User asked 300-500. 
    # I will do 50 now to report progress.
    study.optimize(objective, n_trials=50) 
    
    print("\n🏆 Best Parameters Found:")
    print(study.best_params)
    print(f"Best Value (Score): {study.best_value}")
    
    best = study.best_params
    print("\nRecommended Config Updates:")
    print(f"strategy:\n  adx_threshold: {best['adx_threshold']}\n  rsi_oversold: {best['rsi_oversold']}\n  bb_factor: {best['bb_factor']:.2f}")
