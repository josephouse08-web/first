"""거짓 돌파 & 함정(Fake Out & Trap) 전략"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class FakeoutStrategy(BaseStrategy):
    """
    거짓 돌파(Fake Out) & 유동성 함정(Liquidity Trap) 전략

    핵심 원리:
    - 기관은 유동성이 집중된 곳(스탑로스 클러스터)을 돌파하여
      개인 투자자의 스탑을 터뜨린 후 반대로 움직임
    - 고점/저점을 일시적으로 돌파 후 빠르게 되돌림 = Fake Out

    매수 시그널:
    - 이전 저점을 하향 돌파 후 빠르게 되돌림 (Bull Trap 후 반전)
    - 긴 아래꼬리 + 종가가 지지선 위로 복귀

    매도 시그널:
    - 이전 고점을 상향 돌파 후 빠르게 되돌림 (Bear Trap 후 반전)
    - 긴 윗꼬리 + 종가가 저항선 아래로 복귀
    """

    def __init__(self, config: dict = None):
        super().__init__("Fakeout Trap", config)
        self.swing_lookback = self.config.get("swing_lookback", 5)
        self.wick_ratio_threshold = self.config.get("wick_ratio_threshold", 0.6)
        self.structure_lookback = self.config.get("structure_lookback", 30)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < self.structure_lookback + 10:
            return None

        recent = candles[-(self.structure_lookback + 10):]
        current = candles[-1]
        prev = candles[-2]
        current_price = current.close

        atr_vals = self.atr(
            [c.high for c in recent],
            [c.low for c in recent],
            [c.close for c in recent],
            period=14,
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        buy_score = 0.0
        sell_score = 0.0
        details = {}

        # 1. 최근 스윙 저점/고점 찾기
        structure = recent[:-2]  # 현재 + 직전 캔들 제외
        swing_lows = self._find_key_levels(structure, "low")
        swing_highs = self._find_key_levels(structure, "high")

        # 2. Bullish Fakeout: 저점 하향 돌파 후 되돌림
        if swing_lows:
            nearest_low = min(swing_lows[-3:]) if len(swing_lows) >= 3 else min(swing_lows)

            # 직전 캔들 또는 현재 캔들이 저점을 하향 돌파했는가
            wick_below = False
            sweep_candle = None

            if current.low < nearest_low and current.close > nearest_low:
                wick_below = True
                sweep_candle = current
            elif prev.low < nearest_low and prev.close > nearest_low and current.close > nearest_low:
                wick_below = True
                sweep_candle = prev

            if wick_below and sweep_candle:
                # 꼬리 비율 확인 (아래꼬리가 몸통 대비 큰지)
                body = sweep_candle.body_size
                lower_wick = min(sweep_candle.open, sweep_candle.close) - sweep_candle.low
                total_range = sweep_candle.high - sweep_candle.low

                if total_range > 0 and lower_wick / total_range >= self.wick_ratio_threshold:
                    buy_score += 0.5
                    details["fakeout_type"] = "bullish_sweep"
                    details["swept_level"] = nearest_low
                    details["wick_ratio"] = lower_wick / total_range
                elif total_range > 0:
                    buy_score += 0.3
                    details["fakeout_type"] = "bullish_wick"

        # 3. Bearish Fakeout: 고점 상향 돌파 후 되돌림
        if swing_highs:
            nearest_high = max(swing_highs[-3:]) if len(swing_highs) >= 3 else max(swing_highs)

            wick_above = False
            sweep_candle = None

            if current.high > nearest_high and current.close < nearest_high:
                wick_above = True
                sweep_candle = current
            elif prev.high > nearest_high and prev.close < nearest_high and current.close < nearest_high:
                wick_above = True
                sweep_candle = prev

            if wick_above and sweep_candle:
                body = sweep_candle.body_size
                upper_wick = sweep_candle.high - max(sweep_candle.open, sweep_candle.close)
                total_range = sweep_candle.high - sweep_candle.low

                if total_range > 0 and upper_wick / total_range >= self.wick_ratio_threshold:
                    sell_score += 0.5
                    details["fakeout_type"] = "bearish_sweep"
                    details["swept_level"] = nearest_high
                    details["wick_ratio"] = upper_wick / total_range
                elif total_range > 0:
                    sell_score += 0.3
                    details["fakeout_type"] = "bearish_wick"

        # 4. 볼륨 확인: 높은 거래량 + 되돌림 = 더 강한 시그널
        avg_volume = sum(c.volume for c in recent[-20:]) / 20
        if current.volume > avg_volume * 1.5:
            if buy_score > 0:
                buy_score += 0.15
                details["high_volume"] = True
            if sell_score > 0:
                sell_score += 0.15

        # 5. 연속 fakeout 방지: RSI 극단 확인
        closes = [c.close for c in recent]
        rsi_vals = self.rsi(closes, 14)
        current_rsi = rsi_vals[-1]
        if current_rsi < 35 and buy_score > 0:
            buy_score += 0.15
            details["rsi_oversold"] = current_rsi
        elif current_rsi > 65 and sell_score > 0:
            sell_score += 0.15
            details["rsi_overbought"] = current_rsi

        # 시그널 생성
        if buy_score >= 0.4 and buy_score > sell_score:
            return Signal(
                type=SignalType.BUY,
                strength=min(buy_score, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata=details,
            )
        elif sell_score >= 0.4 and sell_score > buy_score:
            return Signal(
                type=SignalType.SELL,
                strength=min(sell_score, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata=details,
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _find_key_levels(self, candles: list[Candle], level_type: str) -> list[float]:
        """주요 스윙 레벨 탐색"""
        levels = []
        n = self.swing_lookback

        for i in range(n, len(candles) - n):
            if level_type == "low":
                is_swing = all(
                    candles[i].low <= candles[i - j].low and candles[i].low <= candles[i + j].low
                    for j in range(1, n + 1)
                )
                if is_swing:
                    levels.append(candles[i].low)
            else:
                is_swing = all(
                    candles[i].high >= candles[i - j].high and candles[i].high >= candles[i + j].high
                    for j in range(1, n + 1)
                )
                if is_swing:
                    levels.append(candles[i].high)

        return levels
