import traceback
import sys

try:
    from backtest import run_backtest

    # Test basic run
    print("Testing Backtest directly...")
    stats = run_backtest(days=1)
    print(f"Stats Default: {stats}")

    # Test Override
    print("\nTesting Override...")
    override = {
        'strategy': {'adx_threshold': 35},
        'fee_rate': 0.0015
    }
    stats2 = run_backtest(days=1, config_override=override)
    print(f"Stats Override: {stats2}")
except Exception:
    traceback.print_exc()
