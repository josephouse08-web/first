"""알림 시스템 - Telegram, Discord, 콘솔"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger("crypto_bot.notify")


class BaseNotifier(ABC):
    @abstractmethod
    async def send(self, message: str):
        pass


class ConsoleNotifier(BaseNotifier):
    """콘솔 출력 알림"""

    async def send(self, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{timestamp}] {message}\n")


class TelegramNotifier(BaseNotifier):
    """텔레그램 봇 알림"""

    def __init__(self, config: dict):
        self.bot_token = config.get("telegram_bot_token", "")
        self.chat_id = config.get("telegram_chat_id", "")
        self._session = None

    def _get_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str):
        if not self.bot_token or not self.chat_id:
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            session = self._get_session()
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"텔레그램 전송 실패: {text}")
        except Exception as e:
            logger.error(f"텔레그램 전송 에러: {e}")

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


class DiscordNotifier(BaseNotifier):
    """디스코드 웹훅 알림"""

    def __init__(self, config: dict):
        self.webhook_url = config.get("discord_webhook_url", "")
        self._session = None

    def _get_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str):
        if not self.webhook_url:
            return

        payload = {"content": message}

        try:
            session = self._get_session()
            async with session.post(self.webhook_url, json=payload) as resp:
                if resp.status not in (200, 204):
                    text = await resp.text()
                    logger.error(f"디스코드 전송 실패: {text}")
        except Exception as e:
            logger.error(f"디스코드 전송 에러: {e}")

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
