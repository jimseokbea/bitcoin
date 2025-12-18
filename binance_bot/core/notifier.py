import requests
from .system_utils import LOGGER

class TelegramNotifier:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"
        
    def send(self, message):
        """
        Sends a message to Telegram.
        Non-blocking (timeout=5s) to avoid freezing the bot.
        """
        if not self.token or not self.chat_id:
            return

        try:
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': 'Markdown'
            }
            # Timeout is critical. Don't let Telegram lag freeze the trading loop.
            requests.post(self.base_url, json=payload, timeout=5)
            
        except Exception as e:
            LOGGER.error(f"Telegram 전송 실패: {e}")
