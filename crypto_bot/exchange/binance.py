"""바이낸스 거래소 어댑터"""

import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from ..core.models import Candle, Order, OrderStatus, OrderType, Side
from .base import BaseExchange

logger = logging.getLogger("crypto_bot.binance")

# 바이낸스 인터벌 매핑
INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1d", "3d": "3d", "1w": "1w", "1M": "1M",
}


class BinanceExchange(BaseExchange):
    """바이낸스 REST API 어댑터"""

    BASE_URL = "https://api.binance.com"
    FUTURES_URL = "https://fapi.binance.com"

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_key = config.get("api_key", "")
        self.api_secret = config.get("api_secret", "")
        self.use_futures = config.get("use_futures", False)
        self.base_url = self.FUTURES_URL if self.use_futures else self.BASE_URL
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:
                import aiohttp
                self._session = aiohttp.ClientSession()
            except ImportError:
                raise ImportError("aiohttp 패키지가 필요합니다: pip install aiohttp")
        return self._session

    def _sign(self, params: dict) -> dict:
        """HMAC SHA256 서명 생성"""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    @property
    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    async def fetch_candles(self, symbol: str, interval: str,
                            limit: int = 200) -> list[Candle]:
        """캔들스틱 데이터 조회"""
        symbol_formatted = symbol.replace("/", "")
        endpoint = "/fapi/v1/klines" if self.use_futures else "/api/v3/klines"

        params = {
            "symbol": symbol_formatted,
            "interval": INTERVAL_MAP.get(interval, interval),
            "limit": limit,
        }

        session = self._get_session()
        async with session.get(f"{self.base_url}{endpoint}", params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"캔들 조회 실패: {resp.status} - {text}")
                return []

            data = await resp.json()
            candles = []
            for item in data:
                candles.append(Candle(
                    timestamp=datetime.fromtimestamp(item[0] / 1000),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                ))
            return candles

    async def place_order(self, order: Order) -> Order:
        """주문 실행"""
        symbol_formatted = order.symbol.replace("/", "")

        if self.use_futures:
            endpoint = "/fapi/v1/order"
        else:
            endpoint = "/api/v3/order"

        params = {
            "symbol": symbol_formatted,
            "side": order.side.value,
            "type": order.order_type.value,
            "quantity": f"{order.quantity:.8f}",
        }

        if order.order_type == OrderType.LIMIT:
            params["timeInForce"] = "GTC"
            params["price"] = f"{order.price:.8f}"
        elif order.order_type == OrderType.STOP_LOSS:
            params["stopPrice"] = f"{order.stop_price:.8f}"

        signed_params = self._sign(params)
        session = self._get_session()

        async with session.post(
            f"{self.base_url}{endpoint}",
            params=signed_params,
            headers=self._headers,
        ) as resp:
            data = await resp.json()

            if resp.status != 200:
                logger.error(f"주문 실패: {data}")
                order.status = OrderStatus.REJECTED
                return order

            order.status = OrderStatus.FILLED
            order.filled_price = float(data.get("avgPrice", data.get("price", order.price)))
            order.filled_quantity = float(data.get("executedQty", order.quantity))
            order.commission = float(data.get("commission", 0))

            logger.info(f"주문 체결: {order.side.value} {order.filled_quantity} {order.symbol} "
                        f"@ {order.filled_price}")
            return order

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """주문 취소"""
        symbol_formatted = symbol.replace("/", "")
        endpoint = "/fapi/v1/order" if self.use_futures else "/api/v3/order"

        params = self._sign({"symbol": symbol_formatted, "orderId": order_id})
        session = self._get_session()

        async with session.delete(
            f"{self.base_url}{endpoint}", params=params, headers=self._headers,
        ) as resp:
            return resp.status == 200

    async def get_balance(self) -> dict:
        """잔고 조회"""
        if self.use_futures:
            endpoint = "/fapi/v2/balance"
        else:
            endpoint = "/api/v3/account"

        params = self._sign({})
        session = self._get_session()

        async with session.get(
            f"{self.base_url}{endpoint}", params=params, headers=self._headers,
        ) as resp:
            data = await resp.json()
            if self.use_futures:
                return {item["asset"]: float(item["balance"]) for item in data}
            else:
                return {
                    b["asset"]: float(b["free"])
                    for b in data.get("balances", [])
                    if float(b["free"]) > 0
                }

    async def get_ticker(self, symbol: str) -> dict:
        """현재 가격 조회"""
        symbol_formatted = symbol.replace("/", "")
        endpoint = "/api/v3/ticker/price"

        session = self._get_session()
        async with session.get(
            f"{self.base_url}{endpoint}", params={"symbol": symbol_formatted}
        ) as resp:
            data = await resp.json()
            return {"symbol": symbol, "price": float(data["price"])}

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
