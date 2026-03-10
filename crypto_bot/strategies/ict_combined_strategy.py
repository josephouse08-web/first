"""ICT 종합 전략 - 오더블럭 + FVG + 추세선/채널 + 거짓돌파 통합 컨플루언스"""

from datetime import datetime
from typing import Optional

from ..core.models import Candle, Signal, SignalType
from .base import BaseStrategy
from .orderblock_strategy import OrderBlockStrategy
from .fvg_strategy import FVGStrategy
from .trendline_channel_strategy import TrendlineChannelStrategy
from .fakeout_strategy import FakeoutStrategy


class ICTCombinedStrategy(BaseStrategy):
    """
    ICT 종합 컨플루언스 전략

    쉽알남 매매법 통합:
    1. 오더블럭(OB) - 기관 매물대
    2. FVG - 가격 비효율 갭
    3. 추세선/채널 - 구조 분석 + BOS/CHoCH
    4. 거짓돌파(Fakeout) - 유동성 스윕

    컨플루언스 룰:
    - 4개 하위 전략 중 2개 이상 같은 방향 시그널 → 실행
    - OB + FVG 동시 발생 시 추가 보너스 (특히 강력)
    - 구조(BOS/CHoCH)가 방향을 확인해주면 보너스
    - 분할 진입/청산 전략: 시그널 강도에 따라 포지션 크기 조절
    """

    def __init__(self, config: dict = None):
        super().__init__("ICT Combined", config)
        self.min_confluence = self.config.get("min_confluence", 2)

        # 하위 전략 초기화
        self.ob_strategy = OrderBlockStrategy(config)
        self.fvg_strategy = FVGStrategy(config)
        self.trendline_strategy = TrendlineChannelStrategy(config)
        self.fakeout_strategy = FakeoutStrategy(config)

    def analyze(self, candles: list[Candle]) -> Optional[Signal]:
        if len(candles) < 50:
            return None

        current_price = candles[-1].close

        # 4개 하위 전략 시그널 수집
        sub_signals = {}
        strategies = {
            "ob": self.ob_strategy,
            "fvg": self.fvg_strategy,
            "trendline": self.trendline_strategy,
            "fakeout": self.fakeout_strategy,
        }

        for name, strategy in strategies.items():
            signal = strategy.analyze(candles)
            if signal and signal.type != SignalType.HOLD:
                sub_signals[name] = signal

        if not sub_signals:
            return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                           price=current_price, timestamp=datetime.now())

        # 방향별 집계
        buy_signals = {k: v for k, v in sub_signals.items() if v.type == SignalType.BUY}
        sell_signals = {k: v for k, v in sub_signals.items() if v.type == SignalType.SELL}

        buy_count = len(buy_signals)
        sell_count = len(sell_signals)

        # 컨플루언스 체크
        if buy_count >= self.min_confluence and buy_count > sell_count:
            strength = self._calculate_confluence_strength(buy_signals, "BUY")
            return Signal(
                type=SignalType.BUY,
                strength=strength,
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={
                    "confluence_count": buy_count,
                    "active_signals": list(buy_signals.keys()),
                    "sub_strengths": {k: v.strength for k, v in buy_signals.items()},
                    "sub_metadata": {k: v.metadata for k, v in buy_signals.items()},
                },
            )
        elif sell_count >= self.min_confluence and sell_count > buy_count:
            strength = self._calculate_confluence_strength(sell_signals, "SELL")
            return Signal(
                type=SignalType.SELL,
                strength=strength,
                strategy_name=self.name,
                price=current_price,
                timestamp=datetime.now(),
                metadata={
                    "confluence_count": sell_count,
                    "active_signals": list(sell_signals.keys()),
                    "sub_strengths": {k: v.strength for k, v in sell_signals.items()},
                    "sub_metadata": {k: v.metadata for k, v in sell_signals.items()},
                },
            )

        return Signal(type=SignalType.HOLD, strength=0, strategy_name=self.name,
                       price=current_price, timestamp=datetime.now())

    def _calculate_confluence_strength(self, signals: dict, direction: str) -> float:
        """컨플루언스 기반 시그널 강도 계산"""
        # 기본 강도: 하위 시그널 강도 평균
        avg_strength = sum(s.strength for s in signals.values()) / len(signals)

        # 컨플루언스 보너스
        count = len(signals)
        confluence_bonus = (count - 1) * 0.1  # 2개=+0.1, 3개=+0.2, 4개=+0.3

        # OB + FVG 동시 발생 보너스 (가장 강력한 ICT 셋업)
        ob_fvg_bonus = 0.0
        if "ob" in signals and "fvg" in signals:
            ob_fvg_bonus = 0.15

        # 구조 확인 보너스 (추세선 전략이 같은 방향이면)
        structure_bonus = 0.0
        if "trendline" in signals:
            meta = signals["trendline"].metadata
            structure = meta.get("structure", "")
            if direction == "BUY" and "bullish" in structure:
                structure_bonus = 0.1
            elif direction == "SELL" and "bearish" in structure:
                structure_bonus = 0.1

        total = avg_strength + confluence_bonus + ob_fvg_bonus + structure_bonus
        return min(total, 1.0)
