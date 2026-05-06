"""风险模块领域模型 — 纯 Python 数据类，不依赖 SQLAlchemy

这些类在整个系统内流通：策略层、风控层、执行层均使用同一套领域对象。
数据库持久化由 app/models/position.py 的 ORM 模型负责。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum

from app.exchanges.models import InstrumentType, Side, Symbol


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class PositionStatus(str, Enum):
    """持仓生命周期状态。"""

    PENDING = "pending"      # 已创建，尚未完全建仓
    OPEN = "open"            # 完全建仓，正在运行
    CLOSING = "closing"      # 平仓指令已发出
    CLOSED = "closed"        # 完全平仓
    FAILED = "failed"        # 建仓或平仓失败
    STOPPED = "stopped"      # 触发止损/风控强制平仓


class ExitReason(str, Enum):
    """平仓原因。"""

    FUNDING_REVERSAL = "funding_reversal"    # 资金费率转负
    STOP_LOSS = "stop_loss"                  # 止损
    RISK_LIMIT = "risk_limit"                # 风控强制
    MAX_HOLD_TIME = "max_hold_time"          # 达到最长持仓时间
    MANUAL = "manual"                        # 手动平仓
    STRATEGY = "strategy"                    # 策略信号


# ---------------------------------------------------------------------------
# 持仓腿 (单个交易所单个方向)
# ---------------------------------------------------------------------------


@dataclass
class PositionLeg:
    """Delta 中性头寸的一条腿（现货多单 or 永续空单）。

    一个完整的 Delta 中性仓位通常有 2 条腿：
    - 现货 BUY（资金腿）
    - 永续 SELL（对冲腿）
    """

    exchange: str
    symbol: Symbol
    instrument_type: InstrumentType
    side: Side
    size: Decimal             # 数量（基础货币单位）
    entry_price: Decimal      # 均价
    leverage: Decimal = Decimal("1")
    current_price: Decimal | None = None

    @property
    def notional_usd(self) -> Decimal:
        """名义价值 (USD)。"""
        return self.size * self.entry_price

    @property
    def margin_used(self) -> Decimal:
        """占用保证金 (USD)。"""
        return self.notional_usd / self.leverage

    @property
    def unrealized_pnl(self) -> Decimal:
        """未实现盈亏 (USD)，空单方向反转。"""
        if self.current_price is None:
            return Decimal("0")
        price_diff = self.current_price - self.entry_price
        if self.side == Side.SELL:
            price_diff = -price_diff
        return price_diff * self.size


# ---------------------------------------------------------------------------
# 完整仓位
# ---------------------------------------------------------------------------


@dataclass
class Position:
    """一个完整的 Delta 中性套利仓位。

    由多条 :class:`PositionLeg` 组成，记录从开仓到平仓的全生命周期。
    """

    strategy_instance: str
    symbol: Symbol
    notional_usd: Decimal
    legs: list[PositionLeg] = field(default_factory=list)
    status: PositionStatus = PositionStatus.PENDING
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    target_apr_pct: Decimal = Decimal("0")
    funding_received: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    fees_paid: Decimal = Decimal("0")
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_reason: ExitReason | None = None
    notes: str = ""

    # ------------------------------------------------------------------
    # 计算属性
    # ------------------------------------------------------------------

    @property
    def margin_used(self) -> Decimal:
        return sum((leg.margin_used for leg in self.legs), Decimal("0"))

    @property
    def unrealized_pnl(self) -> Decimal:
        return sum((leg.unrealized_pnl for leg in self.legs), Decimal("0"))

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl + self.funding_received - self.fees_paid

    @property
    def holding_hours(self) -> Decimal:
        if self.opened_at is None:
            return Decimal("0")
        now = datetime.now(UTC) if self.closed_at is None else self.closed_at
        delta = now - self.opened_at
        return Decimal(str(round(delta.total_seconds() / 3600, 4)))

    @property
    def is_open(self) -> bool:
        return self.status in (PositionStatus.PENDING, PositionStatus.OPEN)

    def add_leg(self, leg: PositionLeg) -> None:
        self.legs.append(leg)

    def mark_open(self) -> None:
        self.status = PositionStatus.OPEN
        if self.opened_at is None:
            self.opened_at = datetime.now(UTC)

    def mark_closed(self, reason: ExitReason, realized_pnl: Decimal | None = None) -> None:
        self.status = PositionStatus.CLOSED
        self.closed_at = datetime.now(UTC)
        self.exit_reason = reason
        if realized_pnl is not None:
            self.realized_pnl = realized_pnl

    def __repr__(self) -> str:
        return (
            f"<Position id={self.id[:8]} "
            f"symbol={self.symbol} "
            f"notional={self.notional_usd:.0f}USD "
            f"status={self.status.value}>"
        )
