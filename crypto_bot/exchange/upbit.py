"""업비트 거래소 어댑터"""

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, unquote

from ..core.models import Candle, Order, OrderStatus, OrderType, Side
from .base import BaseExchange

logger = logging.getLogger("crypto_bot.upbit")

INTERVAL_MAP = {
    "1m": "minutes/1", "3m": "minutes/3", "5m": "minutes/5",
    "15m": "minutes/15", "30m": "minutes/30", "1h": "minutes/60",
    "4h": "minutes/240", "1d": "days", "1w": "weeks", "1M": "months",
}


class UpbitExchange(BaseExchange):
    """업비트 REST API 어댑터"""

    BASE_URL = "https://api.upbit.com/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        self.access_key = config.get("access_key", "")
        self.secret_key = config.get("secret_key", "")
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession()
            except ImportError:
                raise ImportError("aiohttp 패키지가 필요합니다: pip install aiohttp")
        return self._session

    def _get_auth_header(self, query_params: dict = None) -> dict:
        """JWT 토큰 생성"""
        try:
            import jwt
        except ImportError:
            raise ImportError("PyJWT 패키지가 필요합니다: pip install PyJWT")

        payload = {
            "access_key": self.access_key,
            "nonce": str(uuid.uuid4()),
        }

        if query_params:
            query_string = unquote(urlencode(query_params, doseq=True)).encode()
            m = hashlib.sha512()
            m.update(query_string)
            payload["query_hash"] = m.hexdigest()
            payload["query_hash_alg"] = "SHA512"

        token = jwt.encode(payload, self.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    def _convert_symbol(self, symbol: str) -> str:
        """BTC/KRW → KRW-BTC 변환"""
        parts = symbol.split("/")
        if len(parts) == 2:
            return f"{parts[1]}-{parts[0]}"
        return symbol

    async def fetch_candles(self, symbol: str, interval: str,
                            limit: int = 200) -> list[Candle]:
        market = self._convert_symbol(symbol)
        interval_path = INTERVAL_MAP.get(interval, "minutes/60")
        endpoint = f"/candles/{interval_path}"

        params = {"market": market, "count": min(limit, 200)}
        session = self._get_session()

        async with session.get(f"{self.BASE_URL}{endpoint}", params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"캔들 조회 실패: {resp.status} - {text}")
                return []

            data = await resp.json()
            candles = []
            for item in reversed(data):  # 업비트는 최신순으로 반환
                candles.append(Candle(
                    timestamp=datetime.fromisoformat(
                        item["candle_date_time_kst"].replace("T", " ")
                    ),
                    open=float(item["opening_price"]),
                    high=float(item["high_price"]),
                    low=float(item["low_price"]),
                    close=float(item["trade_price"]),
                    volume=float(item["candle_acc_trade_volume"]),
                ))
            return candles

    async def place_order(self, order: Order) -> Order:
        market = self._convert_symbol(order.symbol)

        params = {
            "market": market,
            "side": "bid" if order.side == Side.BUY else "ask",
            "volume": str(order.quantity),
            "ord_type": "price" if order.order_type == OrderType.MARKET and order.side == Side.BUY
                else "market" if order.order_type == OrderType.MARKET
                else "limit",
        }

        if order.order_type == OrderType.LIMIT:
            params["price"] = str(order.price)
        elif order.order_type == OrderType.MARKET and order.side == Side.BUY:
            params["price"] = str(order.quantity * (order.price or 0))
            del params["volume"]

        headers = self._get_auth_header(params)
        session = self._get_session()

        async with session.post(
            f"{self.BASE_URL}/orders", json=params, headers=headers,
        ) as resp:
            data = await resp.json()

            if resp.status not in (200, 201):
                logger.error(f"주문 실패: {data}")
                order.status = OrderStatus.REJECTED
                return order

            order.status = OrderStatus.FILLED
            order.filled_price = float(data.get("avg_price", order.price or 0))
            order.filled_quantity = float(data.get("executed_volume", order.quantity))
            return order

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        params = {"uuid": order_id}
        headers = self._get_auth_header(params)
        session = self._get_session()

        async with session.delete(
            f"{self.BASE_URL}/order", params=params, headers=headers,
        ) as resp:
            return resp.status == 200

    async def get_balance(self) -> dict:
        headers = self._get_auth_header()
        session = self._get_session()

        async with session.get(
            f"{self.BASE_URL}/accounts", headers=headers,
        ) as resp:
            data = await resp.json()
            return {
                item["currency"]: float(item["balance"])
                for item in data
                if float(item["balance"]) > 0
            }

    async def get_ticker(self, symbol: str) -> dict:
        market = self._convert_symbol(symbol)
        session = self._get_session()

        async with session.get(
            f"{self.BASE_URL}/ticker", params={"markets": market},
        ) as resp:
            data = await resp.json()
            return {"symbol": symbol, "price": float(data[0]["trade_price"])}

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
