import yaml
import sys
import os

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.upbit_client import UpbitClient
from core.trader_multi import UpbitTraderMulti
from core.telegram_notifier import TelegramNotifier


def load_settings():
    config_path = os.path.join(os.path.dirname(__file__), "config", "settings.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    settings = load_settings()

    upbit_cfg = settings["upbit"]
    client = UpbitClient(
        access_key=upbit_cfg["access_key"],
        secret_key=upbit_cfg["secret_key"],
    )

    tg_cfg = settings["telegram"]
    notifier = TelegramNotifier(
        bot_token=tg_cfg["bot_token"],
        chat_id=tg_cfg["chat_id"],
        enabled=tg_cfg["enabled"],
    )

    trader = UpbitTraderMulti(client, notifier, settings)
    trader.run()
