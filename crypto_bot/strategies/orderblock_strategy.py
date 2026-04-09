"""오더블럭(Order Block) 전략 - ICT 기반 기관 매물대 매매

쉽알남 매매법 + ICT 정석:
- OTE (Optimal Trade Entry) 61.8~79% 되돌림 구간에서 진입
- MSS (Market Structure Shift) 확인 후 진입
- OB 아래/위 손절 (ATR 여유분 포함)
- 1:2 이상 손익비 타겟
"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class OrderBlockStrategy(BaseStrategy):

    def __init__(self, config: dict = None):
        super().__init__("Order Block", config)
        self.lookback = self.config.get("ob_lookback", 60)
        self.impulse_threshold = self.config.get("impulse_threshold", 0.8)
        self.ob_max_age = self.config.get("ob_max_age", 30)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.lookback:
            return None

        recent = candles[-self.lookback:]
        current_price = candles[-1].close
        prev_price = candles[-2].close
        atr_vals = self.atr(
            [c.high for c in recent],
            [c.low for c in recent],
            [c.close for c in recent],
            period=14,
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        # 유효한 오더블럭 탐색
        bullish_obs = self._find_bullish_obs(recent, current_atr)
        bearish_obs = self._find_bearish_obs(recent, current_atr)

        # Bullish OB 진입 확인
        for ob in bullish_obs:
            ob_high, ob_low, ob_age, impulse_size, impulse_high = ob
            ob_mid = (ob_high + ob_low) / 2

            # OTE 진입: 가격이 OB의 50% 레벨(CE) 부근에 도달
            # 또는 OB 영역 전체에 진입
            if ob_low <= current_price <= ob_high:
                # MSS 확인: 직전 캔들이 하락하다 현재 양봉 전환
                mss = (prev_price < current_price and candles[-1].is_bullish)

                strength = 0.4
                # 임펄스 크기 보너스
                strength += min((impulse_size / current_atr) * 0.08, 0.2)
                # 나이 보너스 (신선할수록 좋음)
                strength += max(0, (self.ob_max_age - ob_age) / self.ob_max_age) * 0.15
                # MSS 보너스
                if mss:
                    strength += 0.15
                # OB 중간(CE) 근처 보너스
                if abs(current_price - ob_mid) < (ob_high - ob_low) * 0.3:
                    strength += 0.1

                return Signal(
                    type=SignalType.BUY,
                    strength=min(strength, 1.0),
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "ob_type": "bullish",
                        "ob_high": ob_high,
                        "ob_low": ob_low,
                        "ob_age": ob_age,
                        "mss": mss,
                        "sl_price": ob_low - current_atr * 0.5,
                        "tp_price": current_price + (current_price - ob_low) * 2,
                    },
                )

        # Bearish OB 진입 확인
        for ob in bearish_obs:
            ob_high, ob_low, ob_age, impulse_size, impulse_low = ob
            ob_mid = (ob_high + ob_low) / 2

            if ob_low <= current_price <= ob_high:
                mss = (prev_price > current_price and not candles[-1].is_bullish)

                strength = 0.4
                strength += min((impulse_size / current_atr) * 0.08, 0.2)
                strength += max(0, (self.ob_max_age - ob_age) / self.ob_max_age) * 0.15
                if mss:
                    strength += 0.15
                if abs(current_price - ob_mid) < (ob_high - ob_low) * 0.3:
                    strength += 0.1

                return Signal(
                    type=SignalType.SELL,
                    strength=min(strength, 1.0),
                    strategy_name=self.name,
                    price=current_price,
                    timestamp=datetime.now(),
                    metadata={
                        "ob_type": "bearish",
                        "ob_high": ob_high,
                        "ob_low": ob_low,
                        "ob_age": ob_age,
                        "mss": mss,
                        "sl_price": ob_high + current_atr * 0.5,
                        "tp_price": current_price - (ob_high - current_price) * 2,
                    },
                )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _find_bullish_obs(self, candles: list[Candle], atr: float) -> list:
        """모든 유효한 Bullish OB를 찾는다 (최신순)"""
        obs = []
        n = len(candles)
        for i in range(n - 3, max(n - self.ob_max_age - 3, 2), -1):
            # 임펄스: i+1 캔들이 강하게 상승 (여러 캔들에 걸친 임펄스도 허용)
            impulse_move = 0
            impulse_high = candles[i + 1].high
            for k in range(i + 1, min(i + 4, n)):
                impulse_move = max(impulse_move, candles[k].close - candles[i].close)
                impulse_high = max(impulse_high, candles[k].high)

            if impulse_move < atr * self.impulse_threshold:
                continue

            # 마지막 반대 캔들 (하락 캔들)
            if candles[i].is_bullish:
                continue

            # BOS: 임펄스가 최근 스윙 고점을 돌파
            lookback_start = max(0, i - 15)
            recent_high = max(c.high for c in candles[lookback_start:i])
            if impulse_high <= recent_high:
                continue

            # OB 영역 정의
            ob_high = max(candles[i].open, candles[i].close)
            ob_low = candles[i].low

            # Unmitigated: OB 영역에 아직 가격이 되돌아오지 않았는지
            mitigated = False
            for j in range(i + 2, n - 1):  # 현재 캔들은 제외
                if candles[j].low <= ob_high:
                    mitigated = True
                    break

            if mitigated:
                continue

            age = n - 1 - i
            obs.append((ob_high, ob_low, age, impulse_move, impulse_high))

        return obs

    def _find_bearish_obs(self, candles: list[Candle], atr: float) -> list:
        """모든 유효한 Bearish OB를 찾는다"""
        obs = []
        n = len(candles)
        for i in range(n - 3, max(n - self.ob_max_age - 3, 2), -1):
            impulse_move = 0
            impulse_low = candles[i + 1].low
            for k in range(i + 1, min(i + 4, n)):
                impulse_move = max(impulse_move, candles[i].close - candles[k].close)
                impulse_low = min(impulse_low, candles[k].low)

            if impulse_move < atr * self.impulse_threshold:
                continue

            if not candles[i].is_bullish:
                continue

            lookback_start = max(0, i - 15)
            recent_low = min(c.low for c in candles[lookback_start:i])
            if impulse_low >= recent_low:
                continue

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
            obs.append((ob_high, ob_low, age, impulse_move, impulse_low))

        return obs
