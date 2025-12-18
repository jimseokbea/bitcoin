import telegram
import asyncio
from datetime import datetime
from .utils import get_logger

logger = get_logger()

class TelegramBot:
    def __init__(self, config):
        self.token = config['telegram']['token']
        self.chat_id = config['telegram']['chat_id']
        self.bot = None
        if self.token and self.chat_id:
            try:
                self.bot = telegram.Bot(token=self.token)
            except Exception as e:
                logger.error(f"Telegram Init Error: {e}")

    async def send_msg(self, message):
        if not self.bot: return
        try:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            text = f"[{timestamp}]\n{message}"
            # Safety: Timeout added
            await self.bot.send_message(chat_id=self.chat_id, text=text, read_timeout=2, write_timeout=2)
        except Exception as e:
            # Non-blocking error logging
            print(f"텔레그램 전송 실패 (무시함): {e}")

    def send(self, message):
        # 비동기 함수를 동기적으로 실행하기 위한 래퍼
        if not self.bot: return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send_msg(message))
            else:
                loop.run_until_complete(self.send_msg(message))
        except:
            asyncio.run(self.send_msg(message))
