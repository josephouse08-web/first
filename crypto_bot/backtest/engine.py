"""백테스팅 엔진 - 과거 데이터로 전략 성과 검증"""

import logging
from datetime import datetime
from typing import Optional

from ..core.models import (
    Candle, Order, OrderStatus, OrderType, Position, PositionStatus,
    Side, Signal, SignalType, TradeResult, PortfolioState,
)
from ..core.risk_manager import RiskManager

logger = logging.getLogger("crypto_bot.backtest")


class BacktestResult:
    """백테스트 결과 분석"""

    def __init__(self, trades: list[TradeResult], initial_balance: float,
                 final_balance: float, candles: list[Candle]):
        self.trades = trades
        self.initial_balance = initial_balance
        self.final_balance = final_balance
        self.candles = candles

    @property
    def total_return(self) -> float:
        return ((self.final_balance - self.initial_balance) / self.initial_balance) * 100

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def win_rate(self) -> float:
        return (self.winning_trades / self.total_trades * 100) if self.total_trades else 0

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return sum(wins) / len(wins) if wins else 0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        return sum(losses) / len(losses) if losses else 0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    @property
    def max_drawdown(self) -> float:
        if not self.trades:
            return 0
        peak = self.initial_balance
        max_dd = 0
        balance = self.initial_balance
        for trade in self.trades:
            balance += trade.pnl
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100

    @property
    def sharpe_ratio(self) -> float:
        if len(self.trades) < 2:
            return 0
        returns = [t.pnl_percent for t in self.trades]
        avg_return = sum(returns) / len(returns)
        std_return = (sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5
        if std_return == 0:
            return 0
        return (avg_return / std_return) * (252 ** 0.5)  # 연간화

    @property
    def equity_curve(self) -> list[float]:
        curve = [self.initial_balance]
        for trade in self.trades:
            curve.append(curve[-1] + trade.pnl)
        return curve

    def summary(self) -> str:
        duration = ""
        if self.candles:
            start = self.candles[0].timestamp
            end = self.candles[-1].timestamp
            duration = f"\n기간: {start.strftime('%Y-%m-%d')} → {end.strftime('%Y-%m-%d')}"

        return f"""
{'='*60}
📊 백테스트 결과 요약{duration}
{'='*60}
초기 자본:      ${self.initial_balance:>12,.2f}
최종 자본:      ${self.final_balance:>12,.2f}
총 수익률:      {self.total_return:>12.2f}%

총 거래 수:     {self.total_trades:>12}
승리 거래:      {self.winning_trades:>12}
패배 거래:      {self.losing_trades:>12}
승률:           {self.win_rate:>12.1f}%

평균 수익:      ${self.avg_win:>12,.2f}
평균 손실:      ${self.avg_loss:>12,.2f}
수익 팩터:      {self.profit_factor:>12.2f}

최대 낙폭(MDD): {self.max_drawdown:>12.2f}%
샤프 비율:      {self.sharpe_ratio:>12.2f}
{'='*60}
"""


class BacktestEngine:
    """과거 캔들 데이터로 전략 시뮬레이션"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.commission_rate = self.config.get("commission_rate", 0.001)

    def run(self, candles: list[Candle], strategies: list, risk_config: dict = None,
            allow_short: bool = True) -> BacktestResult:
        """
        백테스트 실행

        Args:
            candles: 과거 캔들 데이터
            strategies: 테스트할 전략 리스트
            risk_config: 리스크 매니저 설정
            allow_short: 숏 포지션 허용 여부 (기본 True)
        """
        if not candles or not strategies:
            return BacktestResult([], 0, 0, candles)

        initial_balance = self.config.get("initial_balance", 10000.0)
        risk_manager = RiskManager(risk_config or {})
        portfolio = PortfolioState(
            total_balance=initial_balance,
            available_balance=initial_balance,
        )

        position: Optional[Position] = None
        trades: list[TradeResult] = []
        lookback = self.config.get("lookback", 50)

        logger.info(f"백테스트 시작 | 캔들: {len(candles)} | 전략: {[s.name for s in strategies]} | 숏 허용: {allow_short}")

        for i in range(lookback, len(candles)):
            window = candles[:i + 1]
            current_candle = candles[i]
            current_price = current_candle.close

            # 포지션 존재 시 SL/TP 체크
            if position:
                position.update_pnl(current_price)
                exit_reason = self._check_exit(position, current_candle)
                if exit_reason:
                    trade = self._close_position(position, current_price, current_candle.timestamp, exit_reason)
                    trades.append(trade)
                    portfolio.available_balance += trade.quantity * trade.exit_price + trade.pnl
                    position = None

            # 전략 시그널 수집
            signals = []
            for strategy in strategies:
                signal = strategy.analyze(window)
                if signal and signal.type != SignalType.HOLD:
                    signals.append(signal)

            if not signals:
                continue

            combined = self._combine_signals(signals)
            if combined is None:
                continue

            # 리스크 체크
            if not risk_manager.approve_signal(combined, portfolio, "BACKTEST"):
                continue

            # 매수 시그널
            if combined.type == SignalType.BUY:
                # 숏 포지션 보유 중이면 청산
                if position and position.side == Side.SELL:
                    trade = self._close_position(position, current_price, current_candle.timestamp, "매수 시그널(숏 청산)")
                    trades.append(trade)
                    portfolio.available_balance += trade.quantity * trade.exit_price + trade.pnl
                    position = None

                if position is None:
                    quantity = risk_manager.calculate_position_size(portfolio, current_price, combined.strength)
                    if quantity <= 0:
                        continue
                    sl, tp = risk_manager.calculate_sl_tp(current_price, Side.BUY)
                    cost = quantity * current_price
                    commission = cost * self.commission_rate
                    portfolio.available_balance -= cost + commission

                    position = Position(
                        symbol="BACKTEST",
                        side=Side.BUY,
                        entry_price=current_price,
                        quantity=quantity,
                        stop_loss=sl,
                        take_profit=tp,
                        opened_at=current_candle.timestamp,
                    )

            # 매도 시그널
            elif combined.type == SignalType.SELL:
                # 롱 포지션 보유 중이면 청산 (이건 항상)
                if position and position.side == Side.BUY:
                    trade = self._close_position(position, current_price, current_candle.timestamp, "매도 시그널(롱 청산)")
                    trades.append(trade)
                    portfolio.available_balance += trade.quantity * trade.exit_price + trade.pnl
                    position = None

                # 숏 진입: 독립적으로 "숏을 쳐서 먹을 수 있는 상황"인지 판단
                if position is None and allow_short:
                    if self._is_short_setup_valid(combined, candles[:i + 1]):
                        quantity = risk_manager.calculate_position_size(portfolio, current_price, combined.strength)
                        if quantity <= 0:
                            continue
                        sl, tp = risk_manager.calculate_sl_tp(current_price, Side.SELL)
                        cost = quantity * current_price
                        commission = cost * self.commission_rate
                        portfolio.available_balance -= cost + commission

                        position = Position(
                            symbol="BACKTEST",
                            side=Side.SELL,
                            entry_price=current_price,
                            quantity=quantity,
                            stop_loss=sl,
                            take_profit=tp,
                            opened_at=current_candle.timestamp,
                        )

        # 남은 포지션 청산
        if position:
            trade = self._close_position(position, candles[-1].close, candles[-1].timestamp, "백테스트 종료")
            trades.append(trade)
            portfolio.available_balance += trade.quantity * trade.exit_price + trade.pnl

        final_balance = portfolio.available_balance
        result = BacktestResult(trades, initial_balance, final_balance, candles)
        logger.info(f"백테스트 완료 | 수익률: {result.total_return:.2f}% | 거래: {result.total_trades}")
        return result

    def _check_exit(self, position: Position, candle: Candle) -> Optional[str]:
        if position.stop_loss:
            if position.side == Side.BUY and candle.low <= position.stop_loss:
                return "스탑로스"
            if position.side == Side.SELL and candle.high >= position.stop_loss:
                return "스탑로스"

        if position.take_profit:
            if position.side == Side.BUY and candle.high >= position.take_profit:
                return "테이크프로핏"
            if position.side == Side.SELL and candle.low <= position.take_profit:
                return "테이크프로핏"

        return None

    def _close_position(self, position: Position, price: float,
                        timestamp: datetime, reason: str) -> TradeResult:
        position.update_pnl(price)
        commission = position.quantity * price * self.commission_rate

        return TradeResult(
            symbol=position.symbol,
            side=position.side,
            entry_price=position.entry_price,
            exit_price=price,
            quantity=position.quantity,
            pnl=position.pnl - commission,
            pnl_percent=position.pnl_percent,
            entry_time=position.opened_at,
            exit_time=timestamp,
            strategy="backtest",
            commission=commission,
        )

    def _is_short_setup_valid(self, signal: Signal, candles: list[Candle]) -> bool:
        """
        독립적으로 숏 진입이 유효한지 판단.
        매도 시그널이 왔다고 무조건 숏을 치는 게 아니라,
        차트를 보고 '숏을 치면 먹을 수 있겠다'고 판단될 때만 진입.
        """
        if len(candles) < 50:
            return False

        recent = candles[-50:]
        closes = [c.close for c in recent]
        highs = [c.high for c in recent]
        lows = [c.low for c in recent]

        # EMA 계산
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)

        score = 0

        # 1) 추세 방향: EMA20 < EMA50 = 하락 추세 (+1)
        if ema20[-1] < ema50[-1]:
            score += 1

        # 2) 가격이 EMA20 아래 = 약세 확인 (+1)
        if closes[-1] < ema20[-1]:
            score += 1

        # 3) 하락 구조: 최근 스윙 고점이 낮아지는 패턴 (Lower Highs)
        swing_highs = []
        for i in range(2, len(recent) - 2):
            if recent[i].high > recent[i-1].high and recent[i].high > recent[i-2].high \
               and recent[i].high > recent[i+1].high and recent[i].high > recent[i+2].high:
                swing_highs.append(recent[i].high)

        if len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]:
            score += 1

        # 4) 하락 구조: 최근 스윙 저점이 낮아지는 패턴 (Lower Lows)
        swing_lows = []
        for i in range(2, len(recent) - 2):
            if recent[i].low < recent[i-1].low and recent[i].low < recent[i-2].low \
               and recent[i].low < recent[i+1].low and recent[i].low < recent[i+2].low:
                swing_lows.append(recent[i].low)

        if len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]:
            score += 1

        # 5) 최근 캔들 모멘텀: 최근 5개 중 음봉이 더 많으면 (+1)
        recent_5 = candles[-5:]
        bearish_count = sum(1 for c in recent_5 if c.close < c.open)
        if bearish_count >= 3:
            score += 1

        # 6) 시그널 강도 기준: 숏은 더 높은 확신 필요 (0.5 이상)
        if signal.strength >= 0.5:
            score += 1

        # 최소 3점 이상이어야 숏 진입 허용
        return score >= 3

    @staticmethod
    def _ema(values: list[float], period: int) -> list[float]:
        """지수 이동평균 (내부 헬퍼)"""
        result = []
        multiplier = 2 / (period + 1)
        for i, val in enumerate(values):
            if i == 0:
                result.append(val)
            else:
                result.append((val - result[-1]) * multiplier + result[-1])
        return result

    def _combine_signals(self, signals: list[Signal]) -> Optional[Signal]:
        buy_strength = sum(s.strength for s in signals if s.type == SignalType.BUY)
        sell_strength = sum(s.strength for s in signals if s.type == SignalType.SELL)
        buy_count = sum(1 for s in signals if s.type == SignalType.BUY)
        sell_count = sum(1 for s in signals if s.type == SignalType.SELL)

        if buy_count > sell_count:
            return Signal(
                type=SignalType.BUY, strength=buy_strength / len(signals),
                strategy_name="combined", price=signals[0].price, timestamp=datetime.now(),
            )
        elif sell_count > buy_count:
            return Signal(
                type=SignalType.SELL, strength=sell_strength / len(signals),
                strategy_name="combined", price=signals[0].price, timestamp=datetime.now(),
            )
        return None
