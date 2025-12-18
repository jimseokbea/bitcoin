import yaml
import pandas as pd
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.advanced_strategy import AdvancedStrategy, AdvancedStrategyConfig
from core.backtester import Backtester


def load_settings():
    config_path = os.path.join(os.path.dirname(__file__), "config", "settings.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    settings = load_settings()
    bt_cfg = settings["backtest"]

    print(f"Running Backtest on {bt_cfg['csv_path']}...")
    
    try:
        df = pd.read_csv(bt_cfg["csv_path"])
        candles = df.to_dict("records")

        adv_cfg = AdvancedStrategyConfig.from_yaml(settings["strategy_advanced"])
        backtester = Backtester(
            candles=candles,
            strategy_cls=AdvancedStrategy,
            strategy_config=adv_cfg,
            initial_capital=bt_cfg["initial_capital"],
            fee_rate=bt_cfg["fee_rate"],
        )
        result = backtester.run()
        
        print("\n--- Backtest Result ---")
        print(f"Initial Capital: {result['initial']:,.0f}")
        print(f"Final Equity:    {result['final']:,.0f}")
        print(f"Total Return:    {result['total_return']*100:.2f}%")
        print(f"Max Drawdown:    {result['max_drawdown']*100:.2f}%")
        print(f"Total Trades:    {result['num_trades']}")
        print(f"Win Rate:        {result['win_rate']*100:.2f}%")
        
    except FileNotFoundError:
        print(f"Error: CSV file not found at {bt_cfg['csv_path']}")
        print("Please run scripts/upbit_candle_downloader_multi.py first.")
