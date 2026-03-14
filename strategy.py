import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
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
    @abstractmethod
    def evaluate(self, analysis: dict, daily_stats: DailyStats,
                 current_position: dict) -> TradeAction:
        pass


class SMCScalpingStrategy(BaseStrategy):
    """SMC(Smart Money Concepts) 기반 스캘핑 전략

    쉽알남 전략 원칙:
    1. 다중 근거 (confluence) 2개 이상일 때만 진입
    2. 오더블럭 / FVG 구간에서 진입
    3. 추세선·채널 기반 방향성 확인
    4. Fake out / Trap 감지 시 반대 방향 진입
    5. 구조물 기반 손절/익절 (감이 아닌 기준)
    """

    def __init__(self):
        self.last_trade_time = 0.0
        self.daily_stats = DailyStats()
        self.last_analysis = {}
        self.consecutive_losses = 0
        self.loss_cooldown_until = 0.0

    def reset_daily(self, starting_balance: float):
        today = date.today().isoformat()
        if self.daily_stats.date != today:
            self.daily_stats = DailyStats(
                date=today,
                starting_balance=starting_balance,
                current_balance=starting_balance,
            )
            logger.info(f"일일 통계 초기화: {today}, 시작 잔고: {starting_balance:,.0f}원")

    def update_balance(self, current_balance: float):
        self.daily_stats.current_balance = current_balance

    def record_trade(self, is_profit: bool):
        self.daily_stats.total_trades += 1
        if is_profit:
            self.daily_stats.winning_trades += 1
            self.consecutive_losses = 0
        else:
            self.daily_stats.losing_trades += 1
            self.consecutive_losses += 1
            if self.consecutive_losses >= Config.CONSECUTIVE_LOSS_LIMIT:
                self.loss_cooldown_until = time.time() + Config.CONSECUTIVE_LOSS_COOLDOWN
                logger.warning(
                    f"연속 {self.consecutive_losses}패 → "
                    f"{Config.CONSECUTIVE_LOSS_COOLDOWN}초 쿨다운 발동"
                )

    def evaluate(self, analysis: dict, daily_stats: DailyStats = None,
                 current_position: dict = None) -> TradeAction:
        """SMC 분석 결과 기반 매매 판단"""
        stats = daily_stats or self.daily_stats
        position = current_position or {}

        # === 일일 한도 체크 ===
        if stats.target_reached:
            logger.info(f"일일 목표 수익률 달성! ({stats.profit_pct:.2f}%)")
            return TradeAction(action="hold", reason="일일 목표 수익률 달성")

        if stats.max_loss_reached:
            logger.warning(f"일일 최대 손실 도달! ({stats.profit_pct:.2f}%)")
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

        # === 연패 쿨다운 ===
        now = time.time()
        if now < self.loss_cooldown_until:
            remaining = self.loss_cooldown_until - now
            logger.info(
                f"연패 쿨다운 중: {remaining:.0f}초 남음 "
                f"(연속 {self.consecutive_losses}패)"
            )
            # 쿨다운 중에도 보유 포지션 손절/익절은 허용 (아래 포지션 관리로 진행)
            if not position.get("has_position"):
                return TradeAction(
                    action="hold",
                    reason=f"연패 쿨다운 ({remaining:.0f}초 남음, {self.consecutive_losses}연패)",
                )

        # === 최소 거래 간격 ===
        elapsed = now - self.last_trade_time
        if elapsed < Config.MIN_TRADE_INTERVAL:
            remaining = Config.MIN_TRADE_INTERVAL - elapsed
            return TradeAction(
                action="hold",
                reason=f"최소 거래 간격 대기 중 ({remaining:.0f}초 남음)",
            )

        decision = analysis.get("decision", "hold")
        confidence = analysis.get("confidence", 0.0)
        confluence = analysis.get("confluence_count", 0)

        self.last_analysis = analysis

        # === SMC 신뢰도 필터 ===
        if confidence < Config.MIN_CONFIDENCE:
            logger.info(f"신뢰도 부족: {confidence:.0%} < {Config.MIN_CONFIDENCE:.0%}")
            return TradeAction(action="hold", reason=f"신뢰도 부족 ({confidence:.0%})")

        # === 다중 근거 필터 (SMC 핵심 원칙) ===
        if confluence < Config.MIN_CONFLUENCE and decision != "hold":
            logger.info(f"근거 부족: {confluence}개 < {Config.MIN_CONFLUENCE}개 필요")
            return TradeAction(
                action="hold",
                reason=f"SMC 근거 부족 ({confluence}개, 최소 {Config.MIN_CONFLUENCE}개 필요)",
            )

        # === 매수 신호 ===
        if decision == "buy" and not position.get("has_position"):
            self.last_trade_time = time.time()
            entry = analysis.get("entry_price") or 0
            target = analysis.get("target_price") or 0
            stop = analysis.get("stop_loss") or 0

            # 리스크:리워드 체크
            if entry and target and stop and entry > stop:
                risk = entry - stop
                reward = target - entry
                rr_ratio = reward / risk if risk > 0 else 0
                if rr_ratio < 1.5:
                    logger.info(f"R:R 부족: {rr_ratio:.1f} < 1.5")
                    return TradeAction(
                        action="hold",
                        reason=f"리스크:리워드 부족 ({rr_ratio:.1f}:1, 최소 1.5:1 필요)",
                    )
                logger.info(f"R:R 비율: {rr_ratio:.1f}:1")

            smc_reason = self._build_smc_reason(analysis)
            return TradeAction(
                action="buy",
                amount=Config.TRADE_AMOUNT,
                reason=smc_reason,
                entry_price=entry,
                target_price=target,
                stop_loss=stop,
            )

        # === 매도 신호 ===
        if decision == "sell" and position.get("has_position"):
            self.last_trade_time = time.time()
            return TradeAction(
                action="sell",
                amount=position.get("volume", 0),
                reason=analysis.get("reason", "SMC 매도 신호"),
            )

        # === 포지션 보유 중: 구조물 기반 손절/익절 ===
        if position.get("has_position"):
            current_price = position.get("current_price", 0)
            avg_price = position.get("avg_price", 0)

            # === 최대 보유 시간 초과 → 강제 청산 ===
            entry_time_str = position.get("entry_time")
            if entry_time_str and Config.MAX_HOLD_MINUTES > 0:
                try:
                    entry_dt = datetime.fromisoformat(entry_time_str)
                    hold_minutes = (datetime.now() - entry_dt).total_seconds() / 60
                    if hold_minutes >= Config.MAX_HOLD_MINUTES:
                        pnl_pct = 0.0
                        if avg_price > 0 and current_price > 0:
                            pnl_pct = (current_price - avg_price) / avg_price * 100
                        self.last_trade_time = time.time()
                        logger.warning(
                            f"최대 보유 시간 초과: {hold_minutes:.0f}분 "
                            f"(한도: {Config.MAX_HOLD_MINUTES}분)"
                        )
                        return TradeAction(
                            action="sell",
                            amount=position.get("volume", 0),
                            reason=f"최대 보유 시간 초과 ({hold_minutes:.0f}분, 수익률: {pnl_pct:+.2f}%)",
                        )
                except (ValueError, TypeError):
                    pass

            if avg_price > 0 and current_price > 0:
                pnl_pct = (current_price - avg_price) / avg_price * 100

                # AI가 제시한 목표가 도달 → 익절
                target = analysis.get("target_price")
                if target and current_price >= target:
                    self.last_trade_time = time.time()
                    return TradeAction(
                        action="sell",
                        amount=position.get("volume", 0),
                        reason=f"SMC 목표가 도달 (수익률: {pnl_pct:+.2f}%)",
                    )

                # AI가 제시한 손절가 도달 → 손절
                stop = analysis.get("stop_loss")
                if stop and current_price <= stop:
                    self.last_trade_time = time.time()
                    return TradeAction(
                        action="sell",
                        amount=position.get("volume", 0),
                        reason=f"SMC 손절가 도달 (손실률: {pnl_pct:+.2f}%)",
                    )

                # 반대 구조물 출현 → 익절
                structures = analysis.get("smc_structures", {})
                fakeout = structures.get("fakeout_trap", "")
                if fakeout and "반대" in str(fakeout):
                    self.last_trade_time = time.time()
                    return TradeAction(
                        action="sell",
                        amount=position.get("volume", 0),
                        reason=f"반대 구조물 출현 - 포지션 정리 (수익률: {pnl_pct:+.2f}%)",
                    )

        return TradeAction(
            action="hold",
            reason=analysis.get("reason", "관망"),
        )

    def _build_smc_reason(self, analysis: dict) -> str:
        """SMC 구조물 기반 진입 근거 생성"""
        structures = analysis.get("smc_structures", {})
        parts = []

        if structures.get("order_blocks"):
            parts.append(f"OB: {str(structures['order_blocks'])[:40]}")
        if structures.get("fvg"):
            parts.append(f"FVG: {str(structures['fvg'])[:40]}")
        if structures.get("fakeout_trap"):
            parts.append(f"Fakeout: {str(structures['fakeout_trap'])[:40]}")

        confluence = analysis.get("confluence_count", 0)
        base_reason = analysis.get("reason", "SMC 매수 신호")

        if parts:
            return f"[근거 {confluence}개] {' | '.join(parts)} - {base_reason[:60]}"
        return base_reason
