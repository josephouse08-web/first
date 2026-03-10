"""FVG (Fair Value Gap) 전략 - ICT 기반 가격 비효율 매매"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class FVGStrategy(BaseStrategy):
    """
    ICT Fair Value Gap 전략

    FVG = 같은 방향 3연속 캔들에서 1번째와 3번째 캔들의 꼬리가
    겹치지 않는 구간 (가격 비효율/유동성 공백)

    원리: 시장은 FVG를 채우러 되돌아오는 경향이 있음
    - Bullish FVG: 3연속 상승 캔들의 갭 → 가격이 되돌아오면 매수 (지지)
    - Bearish FVG: 3연속 하락 캔들의 갭 → 가격이 되돌아오면 매도 (저항)

    CE (Consequent Encroachment) = FVG의 중간값 → 특히 강력한 반응 구간
    """

    def __init__(self, config: dict = None):
        super().__init__("FVG", config)
        self.lookback = self.config.get("fvg_lookback", 50)
        self.min_gap_atr_ratio = self.config.get("min_gap_atr_ratio", 0.3)
        self.fvg_max_age = self.config.get("fvg_max_age", 30)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.lookback:
            return None

        recent = candles[-self.lookback:]
        current_price = candles[-1].close
        atr_vals = self.atr(
            [c.high for c in recent],
            [c.low for c in recent],
            [c.close for c in recent],
            period=14,
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        # 유효한 FVG 탐색
        bullish_fvg = self._find_bullish_fvg(recent, current_atr)
        bearish_fvg = self._find_bearish_fvg(recent, current_atr)

        # Bullish FVG: 가격이 갭 영역으로 되돌아왔을 때 매수
        if bullish_fvg:
            gap_high, gap_low, ce, age, gap_size = bullish_fvg
            if gap_low <= current_price <= gap_high:
                # CE(중간값)에 가까울수록 강한 시그널
                ce_distance = abs(current_price - ce) / (gap_high - gap_low) if gap_high != gap_low else 0.5
                strength = 0.5 + (1 - ce_distance) * 0.3
                # 갭 크기 보너스
                gap_ratio = gap_size / current_atr
                strength += min(gap_ratio * 0.1, 0.2)
                strength = min(strength, 1.0)

                return Signal(
                    type=SignalType.BUY,
                    strength=strength,
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "fvg_type": "bullish",
                        "gap_high": gap_high,
                        "gap_low": gap_low,
                        "ce": ce,
                        "age": age,
                        "near_ce": ce_distance < 0.2,
                    },
                )

        # Bearish FVG: 가격이 갭 영역으로 올라왔을 때 매도
        if bearish_fvg:
            gap_high, gap_low, ce, age, gap_size = bearish_fvg
            if gap_low <= current_price <= gap_high:
                ce_distance = abs(current_price - ce) / (gap_high - gap_low) if gap_high != gap_low else 0.5
                strength = 0.5 + (1 - ce_distance) * 0.3
                gap_ratio = gap_size / current_atr
                strength += min(gap_ratio * 0.1, 0.2)
                strength = min(strength, 1.0)

                return Signal(
                    type=SignalType.SELL,
                    strength=strength,
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "fvg_type": "bearish",
                        "gap_high": gap_high,
                        "gap_low": gap_low,
                        "ce": ce,
                        "age": age,
                        "near_ce": ce_distance < 0.2,
                    },
                )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _find_bullish_fvg(self, candles: list[Candle], atr: float):
        """
        Bullish FVG: 3연속 상승 캔들에서
        candle[0].high < candle[2].low → 그 사이가 FVG
        """
        n = len(candles)
        for i in range(n - 4, max(n - self.fvg_max_age - 3, 1), -1):
            c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]

            # 3연속 상승 또는 강한 상승 임펄스
            if not (c2.close > c2.open):  # 중간 캔들은 반드시 양봉
                continue

            # FVG 조건: 1번째 캔들의 고가 < 3번째 캔들의 저가
            gap_low = c1.high
            gap_high = c3.low

            if gap_high <= gap_low:
                continue

            gap_size = gap_high - gap_low
            if gap_size < atr * self.min_gap_atr_ratio:
                continue

            # Unmitigated: 이후 캔들이 갭을 완전히 채우지 않았는지
            filled = False
            for j in range(i + 3, n - 1):
                if candles[j].low <= gap_low:
                    filled = True
                    break

            if filled:
                continue

            ce = (gap_high + gap_low) / 2
            age = n - 1 - (i + 2)
            return (gap_high, gap_low, ce, age, gap_size)

        return None

    def _find_bearish_fvg(self, candles: list[Candle], atr: float):
        """
        Bearish FVG: 3연속 하락 캔들에서
        candle[0].low > candle[2].high → 그 사이가 FVG
        """
        n = len(candles)
        for i in range(n - 4, max(n - self.fvg_max_age - 3, 1), -1):
            c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]

            if not (c2.close < c2.open):  # 중간 캔들은 반드시 음봉
                continue

            gap_high = c1.low
            gap_low = c3.high

            if gap_high <= gap_low:
                continue

            gap_size = gap_high - gap_low
            if gap_size < atr * self.min_gap_atr_ratio:
                continue

            filled = False
            for j in range(i + 3, n - 1):
                if candles[j].high >= gap_high:
                    filled = True
                    break

            if filled:
                continue

            ce = (gap_high + gap_low) / 2
            age = n - 1 - (i + 2)
            return (gap_high, gap_low, ce, age, gap_size)

        return None
