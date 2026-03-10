"""RSI 기반 트레이딩 전략"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class RSIStrategy(BaseStrategy):
    """
    RSI 과매수/과매도 기반 역추세 전략
    - RSI < 30: 과매도 → 매수 시그널
    - RSI > 70: 과매수 → 매도 시그널
    - RSI 다이버전스 감지로 시그널 강화
    """

    def __init__(self, config: dict = None):
        super().__init__("RSI Strategy", config)
        self.period = self.config.get("rsi_period", 14)
        self.oversold = self.config.get("rsi_oversold", 30)
        self.overbought = self.config.get("rsi_overbought", 70)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.period + 5:
            return None

        closes = [c.close for c in candles]
        rsi_values = self.rsi(closes, self.period)

        current_rsi = rsi_values[-1]
        prev_rsi = rsi_values[-2]
        current_price = candles[-1].close

        # RSI 다이버전스 체크
        divergence = self._check_divergence(candles[-20:], rsi_values[-20:])

        if current_rsi < self.oversold:
            strength = (self.oversold - current_rsi) / self.oversold
            if prev_rsi < current_rsi:  # RSI 반등 시작
                strength += 0.2
            if divergence == "bullish":
                strength += 0.3
            return Signal(
                type=SignalType.BUY,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"rsi": current_rsi, "divergence": divergence},
            )

        elif current_rsi > self.overbought:
            strength = (current_rsi - self.overbought) / (100 - self.overbought)
            if prev_rsi > current_rsi:  # RSI 하락 시작
                strength += 0.2
            if divergence == "bearish":
                strength += 0.3
            return Signal(
                type=SignalType.SELL,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"rsi": current_rsi, "divergence": divergence},
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _check_divergence(self, candles: list[Candle], rsi_vals: list[float]) -> Optional[str]:
        """가격과 RSI 사이의 다이버전스 감지"""
        if len(candles) < 10:
            return None

        prices = [c.close for c in candles]

        # 최근 저점들 비교
        if prices[-1] < prices[-5] and rsi_vals[-1] > rsi_vals[-5]:
            return "bullish"  # 가격은 하락, RSI는 상승

        # 최근 고점들 비교
        if prices[-1] > prices[-5] and rsi_vals[-1] < rsi_vals[-5]:
            return "bearish"  # 가격은 상승, RSI는 하락

        return None
