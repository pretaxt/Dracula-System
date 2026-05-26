"""
dgr_btc/types.py
================
策略内部 Decimal 数据类（dynamic grid + recenter, #13）。

完全独立实现，不 import 其他策略 (#11/#12/etc) 的 types。
共享层 (app.exchanges.models / app.risk.models) 由 paper_trading 层做 mapping。

设计原则:
  - Decimal 全程，不用 float（精度 + 一致性）
  - 数据类只持状态，逻辑由 GridManager/DeltaHedger/Strategy 承担
  - Position/Trade 内部使用，DB 持久化由 paper_trading 层映射到 app.risk.models
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class MarketType(str, Enum):
    """对应 app.exchanges.models.InstrumentType (SPOT/PERPETUAL)."""

    SPOT = "SPOT"
    PERP = "PERP"


_ZERO = Decimal("0")


@dataclass
class Order:
    """订单（dgr_btc 内部表示）。"""

    order_id: str
    symbol: str
    market: MarketType
    side: Side
    order_type: OrderType
    price: Decimal
    quantity: Decimal
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: Decimal = _ZERO
    filled_price: Decimal = _ZERO
    fee: Decimal = _ZERO
    is_maker: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    grid_level: Optional[Decimal] = None

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def remaining(self) -> Decimal:
        return self.quantity - self.filled_qty


@dataclass
class Trade:
    """成交事件。"""

    trade_id: str
    order_id: str
    symbol: str
    market: MarketType
    side: Side
    price: Decimal
    quantity: Decimal
    fee: Decimal
    is_maker: bool
    timestamp: datetime
    grid_level: Optional[Decimal] = None

    @property
    def notional(self) -> Decimal:
        return self.price * self.quantity

    @property
    def net_value(self) -> Decimal:
        """有符号净额: SELL 为正(收钱), BUY 为负(花钱), 已扣手续费."""
        sign = Decimal(1) if self.side == Side.SELL else Decimal(-1)
        return sign * self.notional - self.fee


@dataclass
class Position:
    """单一标的仓位（spot 持有量 / perp 净仓位，空为负）。"""

    symbol: str
    market: MarketType
    quantity: Decimal = _ZERO
    avg_entry: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO

    def update_on_trade(self, trade: Trade) -> None:
        """成交后更新仓位状态。"""
        if self.market == MarketType.SPOT:
            self._update_spot(trade)
        else:
            self._update_perp(trade)

    def _update_spot(self, trade: Trade) -> None:
        delta = trade.quantity if trade.side == Side.BUY else -trade.quantity
        if delta > 0:  # 买入：更新加权均价
            new_qty = self.quantity + delta
            if new_qty > 0:
                self.avg_entry = (
                    self.quantity * self.avg_entry + delta * trade.price
                ) / new_qty
            self.quantity = new_qty
        else:  # 卖出：结算 spot realized
            if self.quantity > 0:
                self.realized_pnl += (trade.price - self.avg_entry) * abs(delta)
            self.quantity += delta
        # fee 由 cash 侧统一处理（trade.net_value 已含 fee）

    def _update_perp(self, trade: Trade) -> None:
        # perp: SELL = 做空（qty 减），BUY = 做多/平空
        delta = trade.quantity if trade.side == Side.BUY else -trade.quantity
        new_qty = self.quantity + delta
        # 同向加仓: 更新均价
        if (self.quantity >= 0 and delta > 0) or (self.quantity <= 0 and delta < 0):
            if abs(new_qty) > 0:
                self.avg_entry = (
                    abs(self.quantity) * self.avg_entry + abs(delta) * trade.price
                ) / abs(new_qty)
        else:  # 减仓/反向: 结算已实现盈亏
            close_qty = min(abs(self.quantity), abs(delta))
            if self.quantity > 0:  # 多头平仓
                self.realized_pnl += (trade.price - self.avg_entry) * close_qty
            else:  # 空头平仓
                self.realized_pnl += (self.avg_entry - trade.price) * close_qty
            if abs(delta) > abs(self.quantity):
                self.avg_entry = trade.price
        self.quantity = new_qty
        self.realized_pnl -= trade.fee

    def mark_to_market(self, mark_price: Decimal) -> None:
        if self.market == MarketType.SPOT:
            self.unrealized_pnl = (mark_price - self.avg_entry) * self.quantity
        else:
            if self.quantity > 0:
                self.unrealized_pnl = (mark_price - self.avg_entry) * self.quantity
            elif self.quantity < 0:
                self.unrealized_pnl = (self.avg_entry - mark_price) * abs(self.quantity)
            else:
                self.unrealized_pnl = _ZERO

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl


@dataclass
class GridLevel:
    """网格档位状态。"""

    price: Decimal
    index: int
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    last_filled_side: Optional[Side] = None
    fill_count: int = 0


@dataclass
class MarketState:
    """市场快照。"""

    timestamp: datetime
    spot_price: Decimal
    perp_price: Decimal
    funding_rate: Decimal = _ZERO
    next_funding_time: Optional[datetime] = None
    bid_depth_usdt: Decimal = _ZERO
    ask_depth_usdt: Decimal = _ZERO
    realized_vol_1h: Decimal = _ZERO
    funding_settled: bool = False  # 当前 tick 是否落在 8h funding settle 边界

    @property
    def basis_bps(self) -> Decimal:
        if self.spot_price == 0:
            return _ZERO
        return (self.perp_price - self.spot_price) / self.spot_price * Decimal(10000)


@dataclass
class PortfolioSnapshot:
    timestamp: datetime
    spot_position: Position
    perp_position: Position
    cash_usdt: Decimal
    mark_price: Decimal      # 兼容旧字段, 等于 spot_mark_price
    delta: Decimal  # spot_qty - |short_qty|
    total_equity: Decimal
    funding_paid: Decimal = _ZERO
    total_fees: Decimal = _ZERO
    n_trades: int = 0
    # Phase E.3: spot/perp 分别 mark (real basis-aware)
    spot_mark_price: Decimal = _ZERO
    perp_mark_price: Decimal = _ZERO
    basis_bps: Decimal = _ZERO  # (perp - spot) / spot * 10000
