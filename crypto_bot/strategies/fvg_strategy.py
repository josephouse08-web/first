"""FVG (Fair Value Gap) 전략 - ICT 기반 가격 비효율 매매

쉽알남 매매법:
- CISD (Change in State of Delivery) 확인 후 진입
- FVG의 CE(50%) 리테스트 = 최적 진입
- FVG를 만든 제1 캔들 저점 아래 손절
- 여러 번 터치 시 약화 처리
"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class FVGStrategy(BaseStrategy):

    def __init__(self, config: dict = None):
        super().__init__("FVG", config)
        self.lookback = self.config.get("fvg_lookback", 60)
        self.min_gap_atr_ratio = self.config.get("min_gap_atr_ratio", 0.2)
        self.fvg_max_age = self.config.get("fvg_max_age", 40)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.lookback:
            return None

        recent = candles[-self.lookback:]
        current_price = candles[-1].close
        current_candle = candles[-1]
        atr_vals = self.atr(
            [c.high for c in recent],
            [c.low for c in recent],
            [c.close for c in recent],
            period=14,
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        # 추세 판단 (EMA 방향)
        closes = [c.close for c in recent]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, 50)
        trend_bullish = ema20[-1] > ema50[-1]
        trend_bearish = ema20[-1] < ema50[-1]

        # 유효한 FVG 탐색
        bullish_fvgs = self._find_bullish_fvgs(recent, current_atr)
        bearish_fvgs = self._find_bearish_fvgs(recent, current_atr)

        # Bullish FVG + 추세 확인
        for fvg in bullish_fvgs:
            gap_high, gap_low, ce, age, gap_size, c1_low, touch_count = fvg
            if gap_low <= current_price <= gap_high:
                # CISD 확인: 가격이 갭으로 내려왔다가 양봉 전환
                cisd = current_candle.is_bullish and current_candle.low <= gap_high

                strength = 0.35
                # CE 근처 보너스
                ce_dist = abs(current_price - ce) / (gap_high - gap_low) if gap_high != gap_low else 0.5
                strength += (1 - ce_dist) * 0.2
                # 갭 크기 보너스
                strength += min((gap_size / current_atr) * 0.08, 0.15)
                # 추세 방향 일치 보너스
                if trend_bullish:
                    strength += 0.15
                # CISD 보너스
                if cisd:
                    strength += 0.1
                # 터치 횟수에 따른 감쇄 (여러 번 터치 시 약화)
                strength -= touch_count * 0.1

                if strength < 0.3:
                    continue

                return Signal(
                    type=SignalType.BUY,
                    strength=min(strength, 1.0),
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "fvg_type": "bullish",
                        "gap_high": gap_high,
                        "gap_low": gap_low,
                        "ce": ce,
                        "age": age,
                        "cisd": cisd,
                        "trend_aligned": trend_bullish,
                        "sl_price": c1_low,
                        "touches": touch_count,
                    },
                )

        # Bearish FVG + 추세 확인
        for fvg in bearish_fvgs:
            gap_high, gap_low, ce, age, gap_size, c1_high, touch_count = fvg
            if gap_low <= current_price <= gap_high:
                cisd = not current_candle.is_bullish and current_candle.high >= gap_low

                strength = 0.35
                ce_dist = abs(current_price - ce) / (gap_high - gap_low) if gap_high != gap_low else 0.5
                strength += (1 - ce_dist) * 0.2
                strength += min((gap_size / current_atr) * 0.08, 0.15)
                if trend_bearish:
                    strength += 0.15
                if cisd:
                    strength += 0.1
                strength -= touch_count * 0.1

                if strength < 0.3:
                    continue

                return Signal(
                    type=SignalType.SELL,
                    strength=min(strength, 1.0),
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "fvg_type": "bearish",
                        "gap_high": gap_high,
                        "gap_low": gap_low,
                        "ce": ce,
                        "age": age,
                        "cisd": cisd,
                        "trend_aligned": trend_bearish,
                        "sl_price": c1_high,
                        "touches": touch_count,
                    },
                )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _find_bullish_fvgs(self, candles: list[Candle], atr: float) -> list:
        """모든 유효한 Bullish FVG 탐색"""
        fvgs = []
        n = len(candles)
        for i in range(n - 4, max(n - self.fvg_max_age - 3, 1), -1):
            c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]

            # 중간 캔들은 양봉
            if not (c2.close > c2.open):
                continue

            # FVG: c1의 고가 < c3의 저가
            gap_low = c1.high
            gap_high = c3.low

            if gap_high <= gap_low:
                continue

            gap_size = gap_high - gap_low
            if gap_size < atr * self.min_gap_atr_ratio:
                continue

            # 터치 횟수 계산 & 완전히 채워졌는지 확인
            touch_count = 0
            filled = False
            for j in range(i + 3, n - 1):
                if candles[j].low <= gap_low:
                    filled = True
                    break
                if candles[j].low <= gap_high:
                    touch_count += 1

            if filled:
                continue

            ce = (gap_high + gap_low) / 2
            age = n - 1 - (i + 2)
            c1_low = c1.low  # 손절 기준
            fvgs.append((gap_high, gap_low, ce, age, gap_size, c1_low, touch_count))

        return fvgs

    def _find_bearish_fvgs(self, candles: list[Candle], atr: float) -> list:
        """모든 유효한 Bearish FVG 탐색"""
        fvgs = []
        n = len(candles)
        for i in range(n - 4, max(n - self.fvg_max_age - 3, 1), -1):
            c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]

            if not (c2.close < c2.open):
                continue

            gap_high = c1.low
            gap_low = c3.high

            if gap_high <= gap_low:
                continue

            gap_size = gap_high - gap_low
            if gap_size < atr * self.min_gap_atr_ratio:
                continue

            touch_count = 0
            filled = False
            for j in range(i + 3, n - 1):
                if candles[j].high >= gap_high:
                    filled = True
                    break
                if candles[j].high >= gap_low:
                    touch_count += 1

            if filled:
                continue

            ce = (gap_high + gap_low) / 2
            age = n - 1 - (i + 2)
            c1_high = c1.high
            fvgs.append((gap_high, gap_low, ce, age, gap_size, c1_high, touch_count))

        return fvgs
