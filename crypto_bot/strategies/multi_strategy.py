"""복합 전략 - 여러 기술적 지표를 통합한 고급 전략"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy


class MultiIndicatorStrategy(BaseStrategy):
    """
    RSI + MACD + 볼린저밴드 + 스토캐스틱 + VWAP + ATR을 통합한 종합 전략

    매수 조건 (3개 이상 충족 시):
    1. RSI < 40 (과매도 접근)
    2. MACD 히스토그램 상승 전환
    3. 가격이 볼린저 하단 근처
    4. 스토캐스틱 %K가 %D를 상향 돌파
    5. 가격이 VWAP 아래 (할인 매수)
    6. ATR 기반 적정 변동성

    매도 조건은 반대
    """

    def __init__(self, config: dict = None):
        super().__init__("Multi-Indicator Strategy", config)
        self.min_confirmations = self.config.get("min_confirmations", 3)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < 50:
            return None

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        current_price = candles[-1].close

        # 지표 계산
        rsi_vals = self.rsi(closes, 14)
        macd_line, signal_line, histogram = self.macd(closes)
        upper, middle, lower = self.bollinger_bands(closes)
        stoch_k, stoch_d = self.stochastic(highs, lows, closes)
        vwap_vals = self.vwap(highs, lows, closes, volumes)
        atr_vals = self.atr(highs, lows, closes)

        buy_signals = 0
        sell_signals = 0
        details = {}

        # 1) RSI
        current_rsi = rsi_vals[-1]
        if current_rsi < 40:
            buy_signals += 1
            details["rsi"] = "bullish"
        elif current_rsi > 60:
            sell_signals += 1
            details["rsi"] = "bearish"

        # 2) MACD 히스토그램 방향
        if histogram[-1] > histogram[-2]:
            buy_signals += 1
            details["macd"] = "bullish"
        elif histogram[-1] < histogram[-2]:
            sell_signals += 1
            details["macd"] = "bearish"

        # 3) 볼린저 밴드 위치
        if lower[-1] is not None:
            bb_position = (current_price - lower[-1]) / (upper[-1] - lower[-1]) \
                if (upper[-1] - lower[-1]) != 0 else 0.5
            if bb_position < 0.2:
                buy_signals += 1
                details["bb"] = "bullish"
            elif bb_position > 0.8:
                sell_signals += 1
                details["bb"] = "bearish"

        # 4) 스토캐스틱 크로스
        if stoch_k[-1] > stoch_d[-1] and stoch_k[-2] <= stoch_d[-2]:
            buy_signals += 1
            details["stoch"] = "bullish"
        elif stoch_k[-1] < stoch_d[-1] and stoch_k[-2] >= stoch_d[-2]:
            sell_signals += 1
            details["stoch"] = "bearish"

        # 5) VWAP 위치
        if current_price < vwap_vals[-1] * 0.99:
            buy_signals += 1
            details["vwap"] = "bullish"
        elif current_price > vwap_vals[-1] * 1.01:
            sell_signals += 1
            details["vwap"] = "bearish"

        # 6) ATR 변동성 (적절한 변동성일 때만)
        avg_atr = sum(atr_vals[-20:]) / 20 if len(atr_vals) >= 20 else atr_vals[-1]
        if atr_vals[-1] > avg_atr * 0.5:  # 변동성이 너무 낮지 않을 때
            details["atr_ok"] = True
        else:
            # 변동성 너무 낮으면 시그널 약화
            buy_signals = max(0, buy_signals - 1)
            sell_signals = max(0, sell_signals - 1)

        # 시그널 합산
        if buy_signals >= self.min_confirmations:
            strength = buy_signals / 6.0
            return Signal(
                type=SignalType.BUY,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={
                    "confirmations": buy_signals,
                    "indicators": details,
                    "rsi": current_rsi,
                },
            )
        elif sell_signals >= self.min_confirmations:
            strength = sell_signals / 6.0
            return Signal(
                type=SignalType.SELL,
                strength=min(strength, 1.0),
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={
                    "confirmations": sell_signals,
                    "indicators": details,
                    "rsi": current_rsi,
                },
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())
