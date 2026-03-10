"""볼린저 밴드 기반 트레이딩 전략"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class BollingerStrategy(BaseStrategy):
    """
    볼린저 밴드 수축/확장 + 밴드 터치 전략
    - 하단 밴드 터치 + 반등: 매수
    - 상단 밴드 터치 + 하락: 매도
    - 밴드 수축 후 확장(스퀴즈) 감지
    - 밴드 폭 기반 변동성 측정
    """

    def __init__(self, config: dict = None):
        super().__init__("Bollinger Band Strategy", config)
        self.period = self.config.get("bb_period", 20)
        self.std_dev = self.config.get("bb_std", 2.0)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.period + 5:
            return None

        closes = [c.close for c in candles]
        upper, middle, lower = self.bollinger_bands(closes, self.period, self.std_dev)

        if upper[-1] is None or lower[-1] is None:
            return None

        current_price = candles[-1].close
        prev_price = candles[-2].close

        # 밴드 폭 (변동성 지표)
        band_width = (upper[-1] - lower[-1]) / middle[-1] if middle[-1] else 0
        prev_band_width = (upper[-2] - lower[-2]) / middle[-2] if middle[-2] else 0

        # %B 지표 (밴드 내 위치)
        percent_b = (current_price - lower[-1]) / (upper[-1] - lower[-1]) \
            if (upper[-1] - lower[-1]) != 0 else 0.5

        # 스퀴즈 감지 (밴드 수축)
        is_squeeze = band_width < prev_band_width * 0.8

        # 하단 밴드 터치 또는 이탈 → 매수
        if current_price <= lower[-1] or (prev_price < lower[-2] and current_price > lower[-1]):
            strength = 0.5
            if current_price > prev_price:  # 반등 확인
                strength += 0.2
            if percent_b < 0.05:  # 밴드 아래로 강하게 이탈
                strength += 0.15
            if is_squeeze:  # 스퀴즈 상태에서 터치
                strength += 0.15
            return Signal(
                type=SignalType.BUY,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"percent_b": percent_b, "band_width": band_width, "squeeze": is_squeeze},
            )

        # 상단 밴드 터치 또는 이탈 → 매도
        if current_price >= upper[-1] or (prev_price > upper[-2] and current_price < upper[-1]):
            strength = 0.5
            if current_price < prev_price:  # 하락 확인
                strength += 0.2
            if percent_b > 0.95:
                strength += 0.15
            if is_squeeze:
                strength += 0.15
            return Signal(
                type=SignalType.SELL,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={"percent_b": percent_b, "band_width": band_width, "squeeze": is_squeeze},
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())
