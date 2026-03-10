"""거래소 인터페이스 베이스 클래스"""

from abc import ABC, abstractmethod
from typing import Optional

from ..core.models import Candle, Order


class BaseExchange(ABC):
    """모든 거래소 어댑터의 베이스 클래스"""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def fetch_candles(self, symbol: str, interval: str,
                            limit: int = 200) -> list[Candle]:
        pass

    @abstractmethod
    async def place_order(self, order: Order) -> Order:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        pass

    @abstractmethod
    async def get_balance(self) -> dict:
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        pass
