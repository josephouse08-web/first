"""페이퍼 트레이딩 (모의 거래소) - 실제 API 없이 테스트용"""

import logging
import random
from datetime import datetime, timedelta

from ..core.models import Candle, Order, OrderStatus, OrderType
from .base import BaseExchange

logger = logging.getLogger("crypto_bot.paper")


class PaperExchange(BaseExchange):
    """
    실제 거래소 연결 없이 모의 거래를 수행하는 페이퍼 트레이딩 어댑터.
    바이낸스 공개 API로 실시간 시세를 가져오되, 주문은 가상으로 체결.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.balance = config.get("initial_balance", 10000.0)
        self.commission_rate = config.get("commission_rate", 0.001)  # 0.1%
        self.holdings: dict[str, float] = {}
        self.order_history: list[Order] = []
        self._session = None
        self._use_live_data = config.get("use_live_data", True)

    def _get_session(self):
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession()
            except ImportError:
                raise ImportError("aiohttp 패키지가 필요합니다: pip install aiohttp")
        return self._session

    async def fetch_candles(self, symbol: str, interval: str,
                            limit: int = 200) -> list[Candle]:
        """바이낸스 공개 API에서 실시간 캔들 데이터 조회 (API 키 불필요)"""
        if self._use_live_data:
            try:
                return await self._fetch_live_candles(symbol, interval, limit)
            except Exception as e:
                logger.warning(f"실시간 데이터 조회 실패, 시뮬레이션 모드: {e}")

        return self._generate_simulated_candles(symbol, limit)

    async def _fetch_live_candles(self, symbol: str, interval: str,
                                  limit: int) -> list[Candle]:
        symbol_fmt = symbol.replace("/", "")
        session = self._get_session()

        async with session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol_fmt, "interval": interval, "limit": limit},
        ) as resp:
            data = await resp.json()
            return [
                Candle(
                    timestamp=datetime.fromtimestamp(item[0] / 1000),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                )
                for item in data
            ]

    def _generate_simulated_candles(self, symbol: str, limit: int) -> list[Candle]:
        """시뮬레이션 캔들 데이터 생성"""
        candles = []
        base_price = {"BTC/USDT": 65000, "ETH/USDT": 3500, "BNB/USDT": 600}.get(symbol, 100)
        price = base_price

        for i in range(limit):
            change = random.gauss(0, base_price * 0.01)
            open_price = price
            close_price = price + change
            high_price = max(open_price, close_price) + abs(random.gauss(0, base_price * 0.005))
            low_price = min(open_price, close_price) - abs(random.gauss(0, base_price * 0.005))

            candles.append(Candle(
                timestamp=datetime.now() - timedelta(hours=limit - i),
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=random.uniform(100, 10000),
            ))
            price = close_price

        return candles

    async def place_order(self, order: Order) -> Order:
        """모의 주문 체결"""
        commission = order.quantity * (order.price or 0) * self.commission_rate

        order.status = OrderStatus.FILLED
        order.filled_price = order.price
        order.filled_quantity = order.quantity
        order.commission = commission

        self.order_history.append(order)
        logger.info(f"[페이퍼] 주문 체결: {order.side.value} {order.quantity:.6f} "
                     f"{order.symbol} @ {order.price:,.2f} (수수료: {commission:.4f})")
        return order

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        return True

    async def get_balance(self) -> dict:
        result = {"USDT": self.balance}
        result.update(self.holdings)
        return result

    async def get_ticker(self, symbol: str) -> dict:
        if self._use_live_data:
            try:
                symbol_fmt = symbol.replace("/", "")
                session = self._get_session()
                async with session.get(
                    "https://api.binance.com/api/v3/ticker/price",
                    params={"symbol": symbol_fmt},
                ) as resp:
                    data = await resp.json()
                    return {"symbol": symbol, "price": float(data["price"])}
            except Exception:
                pass
        return {"symbol": symbol, "price": 65000.0}

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
