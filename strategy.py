import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from config import Config
from logger_setup import setup_logger

logger = setup_logger("strategy")


@dataclass
class TradeAction:
    action: str  # "buy", "sell", "hold"
    amount: float = 0.0
    reason: str = ""
    entry_price: float = 0.0
    target_price: float = 0.0
    stop_loss: float = 0.0


@dataclass
class DailyStats:
    date: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit_pct: float = 0.0
    starting_balance: float = 0.0
    current_balance: float = 0.0

    def __post_init__(self):
        if not self.date:
            self.date = date.today().isoformat()

    @property
    def profit_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return ((self.current_balance - self.starting_balance)
                / self.starting_balance * 100)

    @property
    def target_reached(self) -> bool:
        return self.profit_pct >= Config.DAILY_TARGET

    @property
    def max_loss_reached(self) -> bool:
        return self.profit_pct <= Config.MAX_LOSS

    @property
    def max_trades_reached(self) -> bool:
        return self.total_trades >= Config.MAX_DAILY_TRADES


class BaseStrategy(ABC):
    """전략 기본 클래스 - 추후 사용자 전략 추가 시 이 클래스를 상속"""

    @abstractmethod
    def evaluate(self, analysis: dict, daily_stats: DailyStats,
                 current_position: dict) -> TradeAction:
        pass


class ScalpingStrategy(BaseStrategy):
    """기본 스캘핑 전략"""

    def __init__(self):
        self.last_trade_time = 0.0
        self.daily_stats = DailyStats()

    def reset_daily(self, starting_balance: float):
        """일일 통계 초기화"""
        today = date.today().isoformat()
        if self.daily_stats.date != today:
            self.daily_stats = DailyStats(
                date=today,
                starting_balance=starting_balance,
                current_balance=starting_balance,
            )
            logger.info(f"일일 통계 초기화: {today}, 시작 잔고: {starting_balance:,.0f}원")

    def update_balance(self, current_balance: float):
        """현재 잔고 업데이트"""
        self.daily_stats.current_balance = current_balance

    def record_trade(self, is_profit: bool):
        """거래 기록"""
        self.daily_stats.total_trades += 1
        if is_profit:
            self.daily_stats.winning_trades += 1
        else:
            self.daily_stats.losing_trades += 1

    def evaluate(self, analysis: dict, daily_stats: DailyStats = None,
                 current_position: dict = None) -> TradeAction:
        """AI 분석 결과를 기반으로 매매 판단"""
        stats = daily_stats or self.daily_stats
        position = current_position or {}

        # 일일 한도 체크
        if stats.target_reached:
            logger.info(f"일일 목표 수익률 달성! ({stats.profit_pct:.2f}%)")
            return TradeAction(action="hold", reason="일일 목표 수익률 달성")

        if stats.max_loss_reached:
            logger.warning(f"일일 최대 손실 도달! ({stats.profit_pct:.2f}%)")
            # 포지션이 있으면 손절 매도
            if position.get("has_position"):
                return TradeAction(
                    action="sell",
                    reason="일일 최대 손실 도달 - 강제 청산",
                    amount=position.get("volume", 0),
                )
            return TradeAction(action="hold", reason="일일 최대 손실 도달 - 거래 중단")

        if stats.max_trades_reached:
            logger.info(f"일일 최대 거래 횟수 도달 ({stats.total_trades}회)")
            return TradeAction(action="hold", reason="일일 최대 거래 횟수 도달")

        # 최소 거래 간격 체크
        elapsed = time.time() - self.last_trade_time
        if elapsed < Config.MIN_TRADE_INTERVAL:
            remaining = Config.MIN_TRADE_INTERVAL - elapsed
            return TradeAction(
                action="hold",
                reason=f"최소 거래 간격 대기 중 ({remaining:.0f}초 남음)",
            )

        decision = analysis.get("decision", "hold")
        confidence = analysis.get("confidence", 0.0)

        # 신뢰도 필터
        if confidence < Config.MIN_CONFIDENCE:
            logger.info(
                f"신뢰도 부족: {confidence:.1%} < {Config.MIN_CONFIDENCE:.1%}"
            )
            return TradeAction(
                action="hold",
                reason=f"신뢰도 부족 ({confidence:.1%})",
            )

        # 매수 신호
        if decision == "buy" and not position.get("has_position"):
            self.last_trade_time = time.time()
            return TradeAction(
                action="buy",
                amount=Config.TRADE_AMOUNT,
                reason=analysis.get("reason", "AI 매수 신호"),
                entry_price=analysis.get("entry_price", 0),
                target_price=analysis.get("target_price", 0),
                stop_loss=analysis.get("stop_loss", 0),
            )

        # 매도 신호
        if decision == "sell" and position.get("has_position"):
            self.last_trade_time = time.time()
            return TradeAction(
                action="sell",
                amount=position.get("volume", 0),
                reason=analysis.get("reason", "AI 매도 신호"),
            )

        # 포지션 보유 중 손절/익절 체크
        if position.get("has_position"):
            current_price = position.get("current_price", 0)
            avg_price = position.get("avg_price", 0)

            if avg_price > 0 and current_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price * 100

                # 목표가 도달 시 익절
                target = analysis.get("target_price")
                if target and current_price >= target:
                    self.last_trade_time = time.time()
                    return TradeAction(
                        action="sell",
                        amount=position.get("volume", 0),
                        reason=f"목표가 도달 (수익률: {pnl_pct:.2f}%)",
                    )

                # 손절가 도달 시 손절
                stop = analysis.get("stop_loss")
                if stop and current_price <= stop:
                    self.last_trade_time = time.time()
                    return TradeAction(
                        action="sell",
                        amount=position.get("volume", 0),
                        reason=f"손절가 도달 (손실률: {pnl_pct:.2f}%)",
                    )

        return TradeAction(
            action="hold",
            reason=analysis.get("reason", "관망"),
        )
