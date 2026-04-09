"""MACD 기반 트레이딩 전략"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class MACDStrategy(BaseStrategy):
    """
    MACD 크로스오버 + 히스토그램 모멘텀 전략
    - MACD 골든크로스 (MACD > Signal): 매수
    - MACD 데드크로스 (MACD < Signal): 매도
    - 히스토그램 강도로 시그널 품질 판단
    - 제로라인 크로스 추가 확인
    """

    def __init__(self, config: dict = None):
        super().__init__("MACD Strategy", config)
        self.fast = self.config.get("macd_fast", 12)
        self.slow = self.config.get("macd_slow", 26)
        self.signal_period = self.config.get("macd_signal", 9)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.slow + self.signal_period + 5:
            return None

        closes = [c.close for c in candles]
        macd_line, signal_line, histogram = self.macd(closes, self.fast, self.slow, self.signal_period)

        current_macd = macd_line[-1]
        prev_macd = macd_line[-2]
        current_signal = signal_line[-1]
        prev_signal = signal_line[-2]
        current_hist = histogram[-1]
        prev_hist = histogram[-2]
        current_price = candles[-1].close

        # 골든크로스: MACD가 Signal을 상향 돌파
        if prev_macd <= prev_signal and current_macd > current_signal:
            strength = 0.5
            if current_macd < 0:  # 제로라인 아래에서 크로스 (더 강력)
                strength += 0.2
            if current_hist > prev_hist:  # 히스토그램 증가
                strength += 0.15
            # 히스토그램 기울기로 모멘텀 판단
            if len(histogram) >= 3 and histogram[-3] < histogram[-2] < histogram[-1]:
                strength += 0.15
            return Signal(
                type=SignalType.BUY,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"macd": current_macd, "signal": current_signal, "histogram": current_hist},
            )

        # 데드크로스: MACD가 Signal을 하향 돌파
        if prev_macd >= prev_signal and current_macd < current_signal:
            strength = 0.5
            if current_macd > 0:  # 제로라인 위에서 크로스
                strength += 0.2
            if current_hist < prev_hist:
                strength += 0.15
            if len(histogram) >= 3 and histogram[-3] > histogram[-2] > histogram[-1]:
                strength += 0.15
            return Signal(
                type=SignalType.SELL,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"macd": current_macd, "signal": current_signal, "histogram": current_hist},
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())
