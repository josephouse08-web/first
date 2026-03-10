"""오더블럭(Order Block) 전략 - ICT 기반 기관 매물대 매매"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class OrderBlockStrategy(BaseStrategy):
    """
    ICT 오더블럭 전략

    오더블럭 = 급격한 가격 이동 직전의 마지막 반대 캔들(기관 주문 집중 구간)

    Bullish OB: 강한 상승 이전의 마지막 하락 캔들 → 가격이 되돌아오면 매수
    Bearish OB: 강한 하락 이전의 마지막 상승 캔들 → 가격이 되돌아오면 매도

    핵심 규칙 (ICT):
    1. Imbalance: 급격한 가격 이동이 있어야 함
    2. Break of Structure (BOS): 직전 고점/저점 돌파 필수
    3. Unmitigated: 오더블럭은 1회용 (이미 리테스트된 OB는 무효)
    4. 3~4캔들 이내 반응 없으면 무효
    """

    def __init__(self, config: dict = None):
        super().__init__("Order Block", config)
        self.lookback = self.config.get("ob_lookback", 50)
        self.impulse_threshold = self.config.get("impulse_threshold", 1.5)
        self.ob_max_age = self.config.get("ob_max_age", 20)

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

        # 유효한 오더블럭 탐색 (최신 것부터)
        bullish_ob = self._find_bullish_ob(recent, current_atr)
        bearish_ob = self._find_bearish_ob(recent, current_atr)

        # Bullish OB: 가격이 OB 영역으로 되돌아왔을 때 매수
        if bullish_ob:
            ob_high, ob_low, ob_age, impulse_size = bullish_ob
            if ob_low <= current_price <= ob_high:
                # OB 영역 안에 있음 → 매수 시그널
                strength = min(0.4 + (impulse_size / current_atr) * 0.1, 0.9)
                # 나이가 어릴수록 강한 시그널
                age_bonus = max(0, (self.ob_max_age - ob_age) / self.ob_max_age) * 0.2
                strength = min(strength + age_bonus, 1.0)

                return Signal(
                    type=SignalType.BUY,
                    strength=strength,
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "ob_type": "bullish",
                        "ob_high": ob_high,
                        "ob_low": ob_low,
                        "ob_age": ob_age,
                        "impulse_atr_ratio": impulse_size / current_atr,
                    },
                )

        # Bearish OB: 가격이 OB 영역으로 올라왔을 때 매도
        if bearish_ob:
            ob_high, ob_low, ob_age, impulse_size = bearish_ob
            if ob_low <= current_price <= ob_high:
                strength = min(0.4 + (impulse_size / current_atr) * 0.1, 0.9)
                age_bonus = max(0, (self.ob_max_age - ob_age) / self.ob_max_age) * 0.2
                strength = min(strength + age_bonus, 1.0)

                return Signal(
                    type=SignalType.SELL,
                    strength=strength,
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "ob_type": "bearish",
                        "ob_high": ob_high,
                        "ob_low": ob_low,
                        "ob_age": ob_age,
                        "impulse_atr_ratio": impulse_size / current_atr,
                    },
                )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _find_bullish_ob(self, candles: list[Candle], atr: float):
        """
        Bullish Order Block 탐색:
        강한 상승 임펄스 직전의 마지막 하락 캔들을 찾는다.
        """
        n = len(candles)
        for i in range(n - 3, max(n - self.ob_max_age - 3, 2), -1):
            # 임펄스 캔들: i+1부터 연속 상승
            impulse_move = candles[i + 1].close - candles[i].close
            if impulse_move < atr * self.impulse_threshold:
                continue

            # candles[i]가 하락 캔들(마지막 반대 캔들)이어야 함
            if candles[i].is_bullish:
                continue

            # Break of Structure 확인: 임펄스가 최근 고점을 돌파했는지
            recent_high = max(c.high for c in candles[max(0, i - 10):i])
            if candles[i + 1].high <= recent_high:
                continue

            # Unmitigated 확인: OB 영역이 아직 리테스트되지 않았는지
            ob_high = max(candles[i].open, candles[i].close)
            ob_low = candles[i].low
            mitigated = False
            for j in range(i + 2, n - 1):
                if candles[j].low <= ob_high:
                    mitigated = True
                    break

            if mitigated:
                continue

            age = n - 1 - i
            return (ob_high, ob_low, age, impulse_move)

        return None

    def _find_bearish_ob(self, candles: list[Candle], atr: float):
        """
        Bearish Order Block 탐색:
        강한 하락 임펄스 직전의 마지막 상승 캔들을 찾는다.
        """
        n = len(candles)
        for i in range(n - 3, max(n - self.ob_max_age - 3, 2), -1):
            impulse_move = candles[i].close - candles[i + 1].close
            if impulse_move < atr * self.impulse_threshold:
                continue

            if not candles[i].is_bullish:
                continue

            # BOS: 임펄스가 최근 저점을 하향 돌파
            recent_low = min(c.low for c in candles[max(0, i - 10):i])
            if candles[i + 1].low >= recent_low:
                continue

            # Unmitigated
            ob_high = candles[i].high
            ob_low = min(candles[i].open, candles[i].close)
            mitigated = False
            for j in range(i + 2, n - 1):
                if candles[j].high >= ob_low:
                    mitigated = True
                    break

            if mitigated:
                continue

            age = n - 1 - i
            return (ob_high, ob_low, age, impulse_move)

        return None
