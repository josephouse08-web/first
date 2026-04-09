"""트레이딩 엔진 - 전체 시스템의 핵심 오케스트레이터"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Optional

from .models import (
    Candle, Order, OrderStatus, OrderType, Position, PositionStatus,
    Side, Signal, SignalType, TradeResult, PortfolioState,
)

logger = logging.getLogger("crypto_bot.engine")


class TradingEngine:
    """전략 시그널을 받아 주문을 실행하고 포지션을 관리하는 핵심 엔진"""

    def __init__(self, exchange, risk_manager, config: dict):
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.config = config
        self.positions: dict[str, Position] = {}
        self.open_orders: dict[str, Order] = {}
        self.trade_history: list[TradeResult] = []
        self.strategies = []
        self.notifiers = []
        self.running = False
        self.portfolio = PortfolioState(
            total_balance=config.get("initial_balance", 10000.0),
            available_balance=config.get("initial_balance", 10000.0),
        )

    def add_strategy(self, strategy):
        self.strategies.append(strategy)
        logger.info(f"전략 추가: {strategy.name}")

    def add_notifier(self, notifier):
        self.notifiers.append(notifier)

    async def start(self):
        """메인 트레이딩 루프 시작"""
        self.running = True
        symbols = self.config.get("symbols", ["BTC/USDT"])
        interval = self.config.get("interval", "1h")
        poll_seconds = self.config.get("poll_seconds", 60)

        logger.info(f"트레이딩 엔진 시작 | 심볼: {symbols} | 인터벌: {interval}")
        await self._notify(f"🚀 트레이딩 봇 시작\n심볼: {', '.join(symbols)}\n인터벌: {interval}")

        while self.running:
            try:
                for symbol in symbols:
                    await self._process_symbol(symbol, interval)
                await asyncio.sleep(poll_seconds)
            except Exception as e:
                logger.error(f"트레이딩 루프 에러: {e}", exc_info=True)
                await self._notify(f"⚠️ 에러 발생: {e}")
                await asyncio.sleep(10)

    async def stop(self):
        self.running = False
        logger.info("트레이딩 엔진 중지")
        await self._notify("🛑 트레이딩 봇 중지")

    async def _process_symbol(self, symbol: str, interval: str):
        """심볼 하나에 대해 전략 실행 → 시그널 처리"""
        candles = await self.exchange.fetch_candles(symbol, interval, limit=200)
        if not candles:
            return

        current_price = candles[-1].close

        # 기존 포지션 PnL 업데이트 & 스탑로스/테이크프로핏 체크
        if symbol in self.positions:
            pos = self.positions[symbol]
            pos.update_pnl(current_price)
            await self._check_exit_conditions(pos, current_price)

        # 각 전략에서 시그널 수집
        signals: list[Signal] = []
        for strategy in self.strategies:
            signal = strategy.analyze(candles)
            if signal and signal.type != SignalType.HOLD:
                signals.append(signal)

        if not signals:
            return

        # 시그널 합산 (다수결 + 강도 가중)
        combined = self._combine_signals(signals)
        if combined is None:
            return

        logger.info(f"[{symbol}] 합산 시그널: {combined.type.value} (강도: {combined.strength:.2f})")

        # 리스크 매니저 검증
        if not self.risk_manager.approve_signal(combined, self.portfolio, symbol):
            logger.info(f"[{symbol}] 리스크 매니저에 의해 시그널 거부됨")
            return

        await self._execute_signal(symbol, combined, current_price)

    def _combine_signals(self, signals: list[Signal]) -> Optional[Signal]:
        """여러 전략 시그널을 하나로 합산"""
        buy_strength = sum(s.strength for s in signals if s.type == SignalType.BUY)
        sell_strength = sum(s.strength for s in signals if s.type == SignalType.SELL)

        total = len(signals)
        buy_count = sum(1 for s in signals if s.type == SignalType.BUY)
        sell_count = sum(1 for s in signals if s.type == SignalType.SELL)

        min_agreement = self.config.get("min_strategy_agreement", 0.5)

        if buy_count / total >= min_agreement and buy_strength > sell_strength:
            return Signal(
                type=SignalType.BUY,
                strength=buy_strength / total,
                strategy_name="combined",
                price=signals[0].price,
                timestamp=datetime.now(),
            )
        elif sell_count / total >= min_agreement and sell_strength > buy_strength:
            return Signal(
                type=SignalType.SELL,
                strength=sell_strength / total,
                strategy_name="combined",
                price=signals[0].price,
                timestamp=datetime.now(),
            )
        return None

    async def _execute_signal(self, symbol: str, signal: Signal, current_price: float):
        """시그널에 따라 주문 실행"""
        position = self.positions.get(symbol)

        if signal.type == SignalType.BUY:
            if position and position.side == Side.BUY:
                return  # 이미 롱 포지션 보유

            if position and position.side == Side.SELL:
                await self._close_position(symbol, current_price, "시그널 반전(숏→롱)")

            quantity = self.risk_manager.calculate_position_size(
                self.portfolio, current_price, signal.strength
            )
            if quantity <= 0:
                return

            order = await self._place_order(symbol, Side.BUY, quantity, current_price)
            if order and order.status == OrderStatus.FILLED:
                sl, tp = self.risk_manager.calculate_sl_tp(current_price, Side.BUY)
                self.positions[symbol] = Position(
                    symbol=symbol,
                    side=Side.BUY,
                    entry_price=order.filled_price or current_price,
                    quantity=quantity,
                    stop_loss=sl,
                    take_profit=tp,
                )
                self.portfolio.available_balance -= quantity * current_price
                await self._notify(
                    f"📈 매수 | {symbol}\n"
                    f"가격: {current_price:,.2f} | 수량: {quantity:.6f}\n"
                    f"SL: {sl:,.2f} | TP: {tp:,.2f}"
                )

        elif signal.type == SignalType.SELL:
            if position and position.side == Side.BUY:
                await self._close_position(symbol, current_price, "매도 시그널")
            elif not position:
                if self.config.get("allow_short", False):
                    quantity = self.risk_manager.calculate_position_size(
                        self.portfolio, current_price, signal.strength
                    )
                    if quantity <= 0:
                        return
                    order = await self._place_order(symbol, Side.SELL, quantity, current_price)
                    if order and order.status == OrderStatus.FILLED:
                        sl, tp = self.risk_manager.calculate_sl_tp(current_price, Side.SELL)
                        self.positions[symbol] = Position(
                            symbol=symbol,
                            side=Side.SELL,
                            entry_price=order.filled_price or current_price,
                            quantity=quantity,
                            stop_loss=sl,
                            take_profit=tp,
                        )
                        await self._notify(
                            f"📉 숏 진입 | {symbol}\n"
                            f"가격: {current_price:,.2f} | 수량: {quantity:.6f}\n"
                            f"SL: {sl:,.2f} | TP: {tp:,.2f}"
                        )

    async def _place_order(self, symbol: str, side: Side, quantity: float,
                           price: float) -> Optional[Order]:
        """거래소에 주문 전송"""
        order = Order(
            id=str(uuid.uuid4())[:8],
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            price=price,
        )
        try:
            result = await self.exchange.place_order(order)
            self.open_orders[order.id] = result
            logger.info(f"주문 체결: {side.value} {quantity:.6f} {symbol} @ {price:,.2f}")
            return result
        except Exception as e:
            logger.error(f"주문 실패: {e}")
            await self._notify(f"❌ 주문 실패: {symbol} {side.value} - {e}")
            return None

    async def _close_position(self, symbol: str, price: float, reason: str):
        """포지션 청산"""
        pos = self.positions.get(symbol)
        if not pos:
            return

        close_side = Side.SELL if pos.side == Side.BUY else Side.BUY
        order = await self._place_order(symbol, close_side, pos.quantity, price)

        if order and order.status == OrderStatus.FILLED:
            pos.exit_price = price
            pos.status = PositionStatus.CLOSED
            pos.closed_at = datetime.now()
            pos.update_pnl(price)

            trade = TradeResult(
                symbol=symbol,
                side=pos.side,
                entry_price=pos.entry_price,
                exit_price=price,
                quantity=pos.quantity,
                pnl=pos.pnl,
                pnl_percent=pos.pnl_percent,
                entry_time=pos.opened_at,
                exit_time=pos.closed_at,
                strategy="combined",
                commission=order.commission,
            )
            self.trade_history.append(trade)
            self.portfolio.available_balance += pos.quantity * price
            self.portfolio.total_trades += 1
            if pos.pnl > 0:
                self.portfolio.winning_trades += 1
            else:
                self.portfolio.losing_trades += 1
            if self.portfolio.total_trades > 0:
                self.portfolio.win_rate = self.portfolio.winning_trades / self.portfolio.total_trades

            self.portfolio.total_pnl += pos.pnl

            del self.positions[symbol]

            emoji = "✅" if pos.pnl > 0 else "🔴"
            await self._notify(
                f"{emoji} 포지션 청산 | {symbol}\n"
                f"사유: {reason}\n"
                f"진입: {pos.entry_price:,.2f} → 청산: {price:,.2f}\n"
                f"PnL: {pos.pnl:,.2f} ({pos.pnl_percent:+.2f}%)\n"
                f"누적 수익: {self.portfolio.total_pnl:,.2f}"
            )

    async def _check_exit_conditions(self, pos: Position, current_price: float):
        """스탑로스/테이크프로핏 자동 청산 체크"""
        if pos.stop_loss:
            if (pos.side == Side.BUY and current_price <= pos.stop_loss) or \
               (pos.side == Side.SELL and current_price >= pos.stop_loss):
                await self._close_position(pos.symbol, current_price, "스탑로스 도달")
                return

        if pos.take_profit:
            if (pos.side == Side.BUY and current_price >= pos.take_profit) or \
               (pos.side == Side.SELL and current_price <= pos.take_profit):
                await self._close_position(pos.symbol, current_price, "테이크프로핏 도달")
                return

        # 트레일링 스탑
        trailing_pct = self.config.get("trailing_stop_pct")
        if trailing_pct and pos.pnl_percent > trailing_pct:
            new_sl = current_price * (1 - trailing_pct / 100) if pos.side == Side.BUY \
                else current_price * (1 + trailing_pct / 100)
            if pos.stop_loss is None or \
               (pos.side == Side.BUY and new_sl > pos.stop_loss) or \
               (pos.side == Side.SELL and new_sl < pos.stop_loss):
                pos.stop_loss = new_sl
                logger.info(f"[{pos.symbol}] 트레일링 스탑 업데이트: {new_sl:,.2f}")

    async def _notify(self, message: str):
        for notifier in self.notifiers:
            try:
                await notifier.send(message)
            except Exception as e:
                logger.error(f"알림 전송 실패: {e}")

    def get_status(self) -> dict:
        """현재 봇 상태 반환 (대시보드용)"""
        return {
            "running": self.running,
            "portfolio": {
                "total_balance": self.portfolio.total_balance + self.portfolio.total_pnl,
                "available_balance": self.portfolio.available_balance,
                "total_pnl": self.portfolio.total_pnl,
                "total_pnl_percent": (self.portfolio.total_pnl / self.portfolio.total_balance * 100)
                    if self.portfolio.total_balance else 0,
                "win_rate": self.portfolio.win_rate,
                "total_trades": self.portfolio.total_trades,
                "winning_trades": self.portfolio.winning_trades,
                "losing_trades": self.portfolio.losing_trades,
            },
            "positions": {
                sym: {
                    "side": p.side.value,
                    "entry_price": p.entry_price,
                    "quantity": p.quantity,
                    "pnl": p.pnl,
                    "pnl_percent": p.pnl_percent,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                }
                for sym, p in self.positions.items()
            },
            "recent_trades": [
                {
                    "symbol": t.symbol,
                    "side": t.side.value,
                    "entry": t.entry_price,
                    "exit": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_percent": t.pnl_percent,
                    "time": t.exit_time.isoformat(),
                }
                for t in self.trade_history[-20:]
            ],
            "strategies": [s.name for s in self.strategies],
        }
