"""전략 베이스 클래스"""

from abc import ABC, abstractmethod
from typing import Optional

from ..core.models import Candle, Signal


class BaseStrategy(ABC):
    """모든 트레이딩 전략의 베이스 클래스"""

    def __init__(self, name: str, config: dict = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        """캔들 데이터를 분석하여 매매 시그널 생성"""
        pass

    @staticmethod
    def sma(values: list[float], period: int) -> list[float]:
        """단순 이동평균"""
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(values[i - period + 1:i + 1]) / period)
        return result

    @staticmethod
    def ema(values: list[float], period: int) -> list[float]:
        """지수 이동평균"""
        result = []
        multiplier = 2 / (period + 1)
        for i, val in enumerate(values):
            if i == 0:
                result.append(val)
            else:
                result.append((val - result[-1]) * multiplier + result[-1])
        return result

    @staticmethod
    def rsi(values: list[float], period: int = 14) -> list[float]:
        """RSI (Relative Strength Index)"""
        if len(values) < period + 1:
            return [50.0] * len(values)

        deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]

        result = [50.0]  # 첫 값

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period):
            result.append(50.0)

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                result.append(100.0)
            else:
                rs = avg_gain / avg_loss
                result.append(100 - (100 / (1 + rs)))

        return result

    @staticmethod
    def macd(values: list[float], fast: int = 12, slow: int = 26,
             signal_period: int = 9) -> tuple[list[float], list[float], list[float]]:
        """MACD (Moving Average Convergence Divergence)"""
        ema_fast = BaseStrategy.ema(values, fast)
        ema_slow = BaseStrategy.ema(values, slow)

        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = BaseStrategy.ema(macd_line, signal_period)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]

        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(values: list[float], period: int = 20,
                        std_dev: float = 2.0) -> tuple[list[float], list[float], list[float]]:
        """볼린저 밴드"""
        middle = BaseStrategy.sma(values, period)
        upper = []
        lower = []

        for i in range(len(values)):
            if middle[i] is None:
                upper.append(None)
                lower.append(None)
            else:
                window = values[i - period + 1:i + 1]
                std = (sum((x - middle[i]) ** 2 for x in window) / period) ** 0.5
                upper.append(middle[i] + std_dev * std)
                lower.append(middle[i] - std_dev * std)

        return upper, middle, lower

    @staticmethod
    def stochastic(highs: list[float], lows: list[float], closes: list[float],
                   k_period: int = 14, d_period: int = 3) -> tuple[list[float], list[float]]:
        """스토캐스틱 오실레이터"""
        k_values = []
        for i in range(len(closes)):
            if i < k_period - 1:
                k_values.append(50.0)
            else:
                high_max = max(highs[i - k_period + 1:i + 1])
                low_min = min(lows[i - k_period + 1:i + 1])
                if high_max == low_min:
                    k_values.append(50.0)
                else:
                    k_values.append(((closes[i] - low_min) / (high_max - low_min)) * 100)

        d_values = BaseStrategy.sma(k_values, d_period)
        d_values = [v if v is not None else 50.0 for v in d_values]
        return k_values, d_values

    @staticmethod
    def atr(highs: list[float], lows: list[float], closes: list[float],
            period: int = 14) -> list[float]:
        """ATR (Average True Range)"""
        tr_values = []
        for i in range(len(closes)):
            if i == 0:
                tr_values.append(highs[i] - lows[i])
            else:
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1])
                )
                tr_values.append(tr)
        return BaseStrategy.ema(tr_values, period)

    @staticmethod
    def vwap(highs: list[float], lows: list[float], closes: list[float],
             volumes: list[float]) -> list[float]:
        """VWAP (Volume Weighted Average Price)"""
        result = []
        cumulative_tp_vol = 0.0
        cumulative_vol = 0.0
        for i in range(len(closes)):
            typical_price = (highs[i] + lows[i] + closes[i]) / 3
            cumulative_tp_vol += typical_price * volumes[i]
            cumulative_vol += volumes[i]
            if cumulative_vol == 0:
                result.append(closes[i])
            else:
                result.append(cumulative_tp_vol / cumulative_vol)
        return result
