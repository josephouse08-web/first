"""데이터 모델 정의"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass
class Signal:
    type: SignalType
    strength: float  # 0.0 ~ 1.0
    strategy_name: str
    price: float
    timestamp: datetime
    metadata: dict = field(default_factory=dict)


@dataclass
class Order:
    id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    commission: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    symbol: str
    side: Side
    entry_price: float
    quantity: float
    status: PositionStatus = PositionStatus.OPEN
    exit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    pnl: float = 0.0
    pnl_percent: float = 0.0
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None

    @property
    def unrealized_pnl(self) -> float:
        return self.pnl

    def update_pnl(self, current_price: float):
        if self.side == Side.BUY:
            self.pnl = (current_price - self.entry_price) * self.quantity
            self.pnl_percent = ((current_price - self.entry_price) / self.entry_price) * 100
        else:
            self.pnl = (self.entry_price - current_price) * self.quantity
            self.pnl_percent = ((self.entry_price - current_price) / self.entry_price) * 100


@dataclass
class TradeResult:
    symbol: str
    side: Side
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_percent: float
    entry_time: datetime
    exit_time: datetime
    strategy: str
    commission: float = 0.0


@dataclass
class PortfolioState:
    total_balance: float
    available_balance: float
    positions: list[Position] = field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
