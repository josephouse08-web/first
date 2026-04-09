"""추세선 & 채널 전략 - 추세 구조 기반 매매"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class TrendlineChannelStrategy(BaseStrategy):
    """
    추세선 & 채널 전략

    1. 추세선: 연속된 스윙 고점/저점을 연결하여 추세선 형성
       - 상승 추세선 지지 터치 → 매수
       - 하락 추세선 저항 터치 → 매도

    2. 채널: 상단/하단 추세선으로 가격 범위 형성
       - 상승 채널 하단 터치 → 매수
       - 하락 채널 상단 터치 → 매도
       - 채널 이탈 시 추세 강화 시그널

    3. Break of Structure (BOS) / CHoCH 감지
       - 고점/저점 갱신 패턴으로 추세 전환 판단
    """

    def __init__(self, config: dict = None):
        super().__init__("Trendline Channel", config)
        self.swing_lookback = self.config.get("swing_lookback", 5)
        self.min_touches = self.config.get("min_touches", 2)
        self.channel_tolerance = self.config.get("channel_tolerance", 0.003)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < 50:
            return None

        recent = candles[-80:] if len(candles) >= 80 else candles
        current_price = candles[-1].close
        current_candle = candles[-1]
        atr_vals = self.atr(
            [c.high for c in recent],
            [c.low for c in recent],
            [c.close for c in recent],
            period=14,
        )
        current_atr = atr_vals[-1] if atr_vals[-1] > 0 else current_price * 0.01

        # 스윙 고점/저점 탐색
        swing_highs = self._find_swing_highs(recent)
        swing_lows = self._find_swing_lows(recent)

        buy_score = 0.0
        sell_score = 0.0
        details = {}

        # 1. BOS / CHoCH (구조 변화) 감지
        trend = self._detect_structure(swing_highs, swing_lows, recent)
        if trend == "bullish_bos":
            buy_score += 0.3
            details["structure"] = "bullish_bos"
        elif trend == "bearish_bos":
            sell_score += 0.3
            details["structure"] = "bearish_bos"
        elif trend == "bullish_choch":
            buy_score += 0.4
            details["structure"] = "bullish_choch"
        elif trend == "bearish_choch":
            sell_score += 0.4
            details["structure"] = "bearish_choch"

        # 2. 상승 추세선 지지 확인
        if len(swing_lows) >= self.min_touches:
            support_level = self._calculate_trendline_value(swing_lows, recent, len(recent) - 1)
            if support_level and abs(current_price - support_level) < current_atr * 0.5:
                buy_score += 0.3
                details["trendline_support"] = support_level

        # 3. 하락 추세선 저항 확인
        if len(swing_highs) >= self.min_touches:
            resistance_level = self._calculate_trendline_value(swing_highs, recent, len(recent) - 1)
            if resistance_level and abs(current_price - resistance_level) < current_atr * 0.5:
                sell_score += 0.3
                details["trendline_resistance"] = resistance_level

        # 4. 채널 감지 & 위치
        channel = self._detect_channel(swing_highs, swing_lows, recent)
        if channel:
            upper, lower, channel_type = channel
            channel_width = upper - lower
            if channel_width > 0:
                position_in_channel = (current_price - lower) / channel_width
                details["channel"] = channel_type
                details["channel_position"] = position_in_channel

                if channel_type == "ascending":
                    if position_in_channel < 0.2:  # 채널 하단
                        buy_score += 0.3
                    elif position_in_channel > 0.9:  # 채널 상단 근처
                        sell_score += 0.15  # 약한 매도 (추세 방향이 상승이므로)
                elif channel_type == "descending":
                    if position_in_channel > 0.8:  # 채널 상단
                        sell_score += 0.3
                    elif position_in_channel < 0.1:  # 채널 하단 근처
                        buy_score += 0.15

        # 5. 이동평균 추세 확인 (추가 컨펌)
        closes = [c.close for c in recent]
        ema20 = self.ema(closes, 20)
        ema50 = self.ema(closes, 50)
        if ema20[-1] > ema50[-1] and current_price > ema20[-1]:
            buy_score += 0.1
            details["ema_trend"] = "bullish"
        elif ema20[-1] < ema50[-1] and current_price < ema20[-1]:
            sell_score += 0.1
            details["ema_trend"] = "bearish"

        # 시그널 생성
        if buy_score >= 0.5 and buy_score > sell_score:
            return Signal(
                type=SignalType.BUY,
                strength=min(buy_score, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata=details,
            )
        elif sell_score >= 0.5 and sell_score > buy_score:
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

    def _find_swing_highs(self, candles: list[Candle]) -> list[tuple[int, float]]:
        """스윙 고점 탐색: 좌우 n개 캔들보다 높은 고점"""
        swings = []
        n = self.swing_lookback
        for i in range(n, len(candles) - n):
            is_swing = True
            for j in range(1, n + 1):
                if candles[i].high <= candles[i - j].high or candles[i].high <= candles[i + j].high:
                    is_swing = False
                    break
            if is_swing:
                swings.append((i, candles[i].high))
        return swings

    def _find_swing_lows(self, candles: list[Candle]) -> list[tuple[int, float]]:
        """스윙 저점 탐색"""
        swings = []
        n = self.swing_lookback
        for i in range(n, len(candles) - n):
            is_swing = True
            for j in range(1, n + 1):
                if candles[i].low >= candles[i - j].low or candles[i].low >= candles[i + j].low:
                    is_swing = False
                    break
            if is_swing:
                swings.append((i, candles[i].low))
        return swings

    def _detect_structure(self, swing_highs, swing_lows, candles) -> Optional[str]:
        """
        BOS (Break of Structure) / CHoCH (Change of Character) 감지

        BOS: 추세 지속 (상승추세에서 고점 갱신)
        CHoCH: 추세 전환 (하락추세에서 갑자기 고점 갱신)
        """
        if len(swing_highs) < 3 or len(swing_lows) < 3:
            return None

        last_highs = [h for _, h in swing_highs[-3:]]
        last_lows = [l for _, l in swing_lows[-3:]]

        # Higher highs + Higher lows = Bullish
        hh = last_highs[-1] > last_highs[-2]
        hl = last_lows[-1] > last_lows[-2]

        # Lower highs + Lower lows = Bearish
        lh = last_highs[-1] < last_highs[-2]
        ll = last_lows[-1] < last_lows[-2]

        # 이전 구조 확인
        prev_hh = last_highs[-2] > last_highs[-3] if len(last_highs) >= 3 else False
        prev_ll = last_lows[-2] < last_lows[-3] if len(last_lows) >= 3 else False

        # CHoCH: 추세 반전
        if prev_ll and hh:  # 하락하다 갑자기 HH
            return "bullish_choch"
        if prev_hh and ll:  # 상승하다 갑자기 LL
            return "bearish_choch"

        # BOS: 추세 지속
        if hh and hl:
            return "bullish_bos"
        if lh and ll:
            return "bearish_bos"

        return None

    def _calculate_trendline_value(self, swing_points: list[tuple[int, float]],
                                    candles: list[Candle], target_idx: int) -> Optional[float]:
        """최근 2개 스윙 포인트로 추세선 연장값 계산"""
        if len(swing_points) < 2:
            return None

        p1_idx, p1_val = swing_points[-2]
        p2_idx, p2_val = swing_points[-1]

        if p2_idx == p1_idx:
            return None

        slope = (p2_val - p1_val) / (p2_idx - p1_idx)
        value = p2_val + slope * (target_idx - p2_idx)
        return value

    def _detect_channel(self, swing_highs, swing_lows, candles):
        """상단/하단 추세선으로 채널 감지"""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return None

        n = len(candles) - 1

        upper = self._calculate_trendline_value(swing_highs, candles, n)
        lower = self._calculate_trendline_value(swing_lows, candles, n)

        if upper is None or lower is None or upper <= lower:
            return None

        # 채널 방향 판단
        h_slope = (swing_highs[-1][1] - swing_highs[-2][1]) / max(1, swing_highs[-1][0] - swing_highs[-2][0])
        l_slope = (swing_lows[-1][1] - swing_lows[-2][1]) / max(1, swing_lows[-1][0] - swing_lows[-2][0])
        avg_slope = (h_slope + l_slope) / 2

        if avg_slope > 0:
            channel_type = "ascending"
        elif avg_slope < 0:
            channel_type = "descending"
        else:
            channel_type = "horizontal"

        return (upper, lower, channel_type)
