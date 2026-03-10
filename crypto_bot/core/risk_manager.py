"""리스크 관리 시스템"""

import logging
from datetime import datetime, timedelta

from .models import PortfolioState, Side, Signal, SignalType

logger = logging.getLogger("crypto_bot.risk")


class RiskManager:
    """포지션 사이징, 손절/익절, 일일 한도, 드로다운 보호 등 리스크 관리"""

    def __init__(self, config: dict):
        # 기본 리스크 파라미터
        self.max_risk_per_trade = config.get("max_risk_per_trade", 0.02)       # 거래당 최대 리스크 2%
        self.max_portfolio_risk = config.get("max_portfolio_risk", 0.06)        # 전체 포트폴리오 최대 리스크 6%
        self.max_positions = config.get("max_positions", 5)                     # 최대 동시 포지션 수
        self.stop_loss_pct = config.get("stop_loss_pct", 0.03)                  # 기본 스탑로스 3%
        self.take_profit_pct = config.get("take_profit_pct", 0.06)              # 기본 테이크프로핏 6%
        self.max_daily_loss = config.get("max_daily_loss", 0.05)                # 일일 최대 손실 5%
        self.max_drawdown = config.get("max_drawdown", 0.15)                    # 최대 드로다운 15%
        self.min_signal_strength = config.get("min_signal_strength", 0.3)       # 최소 시그널 강도

        # 상태 추적
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.max_daily_trades = config.get("max_daily_trades", 20)
        self.last_reset_date = datetime.now().date()
        self.peak_balance = 0.0

    def approve_signal(self, signal: Signal, portfolio: PortfolioState, symbol: str) -> bool:
        """시그널 실행 전 리스크 검증"""
        self._daily_reset_check()

        # 1) 시그널 강도 체크
        if signal.strength < self.min_signal_strength:
            logger.info(f"시그널 강도 부족: {signal.strength:.2f} < {self.min_signal_strength}")
            return False

        # 2) 최대 동시 포지션 수 체크
        open_positions = len([p for p in portfolio.positions if p.symbol != symbol])
        if open_positions >= self.max_positions:
            logger.info(f"최대 포지션 수 초과: {open_positions}/{self.max_positions}")
            return False

        # 3) 일일 손실 한도 체크
        if abs(self.daily_pnl) > portfolio.total_balance * self.max_daily_loss:
            logger.warning(f"일일 손실 한도 도달: {self.daily_pnl:,.2f}")
            return False

        # 4) 일일 거래 횟수 체크
        if self.daily_trades >= self.max_daily_trades:
            logger.info(f"일일 최대 거래 횟수 도달: {self.daily_trades}")
            return False

        # 5) 최대 드로다운 체크
        current_balance = portfolio.total_balance + portfolio.total_pnl
        if self.peak_balance > 0:
            drawdown = (self.peak_balance - current_balance) / self.peak_balance
            if drawdown > self.max_drawdown:
                logger.warning(f"최대 드로다운 도달: {drawdown:.2%} > {self.max_drawdown:.2%}")
                return False

        # 6) 잔고 부족 체크
        if portfolio.available_balance < portfolio.total_balance * 0.1:
            logger.info("가용 잔고 부족 (10% 미만)")
            return False

        self.daily_trades += 1
        return True

    def calculate_position_size(self, portfolio: PortfolioState, price: float,
                                signal_strength: float) -> float:
        """Kelly Criterion 변형 포지션 사이징"""
        # 기본 리스크 금액
        risk_amount = portfolio.available_balance * self.max_risk_per_trade

        # 시그널 강도에 따라 사이징 조절 (강할수록 더 큰 포지션)
        adjusted_risk = risk_amount * min(signal_strength, 1.0)

        # 스탑로스 기반 수량 계산
        stop_distance = price * self.stop_loss_pct
        if stop_distance == 0:
            return 0.0

        quantity = adjusted_risk / stop_distance

        # 최대 포지션 크기 제한 (가용 잔고의 20%)
        max_quantity = (portfolio.available_balance * 0.2) / price
        quantity = min(quantity, max_quantity)

        return round(quantity, 8)

    def calculate_sl_tp(self, entry_price: float, side: Side) -> tuple[float, float]:
        """스탑로스/테이크프로핏 가격 계산"""
        if side == Side.BUY:
            sl = entry_price * (1 - self.stop_loss_pct)
            tp = entry_price * (1 + self.take_profit_pct)
        else:
            sl = entry_price * (1 + self.stop_loss_pct)
            tp = entry_price * (1 - self.take_profit_pct)
        return round(sl, 2), round(tp, 2)

    def update_daily_pnl(self, pnl: float):
        self._daily_reset_check()
        self.daily_pnl += pnl

    def update_peak_balance(self, balance: float):
        if balance > self.peak_balance:
            self.peak_balance = balance

    def _daily_reset_check(self):
        today = datetime.now().date()
        if today != self.last_reset_date:
            logger.info(f"일일 리스크 카운터 리셋 | 어제 PnL: {self.daily_pnl:,.2f}")
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.last_reset_date = today
