"""ORM 模型 — positions / position_legs 表

对应 schema.sql 中的 positions + position_legs 两张表。
提供 from_domain() / to_domain() 在领域对象与 ORM 行之间转换。

X5 修复（2026-05-10）：legs 持久化。重启后 restore 必须能拿到完整 legs，
否则 close_position 找不到腿信息无法平真实交易所持仓 → DB closed 但真实持仓
残留。
"""
from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.exchanges.models import InstrumentType, Side, Symbol
from app.risk.models import ExitReason, Position, PositionLeg, PositionStatus


class PositionRecord(Base):
    """对应 ``positions`` 表的 ORM 行。"""

    __tablename__ = "positions"

    # -------------------------------------------------------------------
    # 主键（使用 DB 自增 id，uuid 作为业务键）
    # -------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(UUID(as_uuid=False), unique=True, nullable=False, index=True)

    # -------------------------------------------------------------------
    # 策略信息
    # -------------------------------------------------------------------
    strategy_instance: Mapped[str] = mapped_column(String(100), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False, default="funding_rate")

    # -------------------------------------------------------------------
    # 仓位状态
    # -------------------------------------------------------------------
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    margin_used: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    target_apr_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)

    # -------------------------------------------------------------------
    # 盈亏
    # -------------------------------------------------------------------
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    funding_received: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )
    fees_paid: Mapped[Decimal] = mapped_column(
        Numeric(20, 8), nullable=False, default=Decimal("0")
    )

    # -------------------------------------------------------------------
    # 时间
    # -------------------------------------------------------------------
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # -------------------------------------------------------------------
    # 元数据（notes 临时存储 symbol 字符串，Week 6 迁移到独立字段）
    # -------------------------------------------------------------------
    exit_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # -------------------------------------------------------------------
    # 转换方法
    # -------------------------------------------------------------------

    @classmethod
    def from_domain(
        cls,
        pos: Position,
        strategy_type: str = "funding_rate",
    ) -> "PositionRecord":
        """从领域对象构建 ORM 记录（用于 upsert）。

        strategy_type: "funding_rate" / "spot_perp" / "perp_basis" 等。
        """
        return cls(
            uuid=pos.id,
            strategy_instance=pos.strategy_instance,
            strategy_type=strategy_type,
            status=pos.status.value,
            notional_usd=pos.notional_usd,
            margin_used=pos.margin_used,
            target_apr_pct=pos.target_apr_pct,
            realized_pnl=pos.realized_pnl,
            unrealized_pnl=pos.unrealized_pnl,
            funding_received=pos.funding_received,
            fees_paid=pos.fees_paid,
            opened_at=pos.opened_at,
            closed_at=pos.closed_at,
            exit_reason=pos.exit_reason.value if pos.exit_reason else None,
            # notes 临时存储 symbol 供 to_domain() 恢复
            notes=str(pos.symbol),
        )

    def to_domain(self) -> Position:
        """从 ORM 记录还原领域对象（启动时恢复内存状态）。"""
        try:
            symbol = Symbol.from_ccxt(self.notes) if "/" in (self.notes or "") else Symbol("BTC", "USDT")
        except ValueError:
            symbol = Symbol("BTC", "USDT")

        return Position(
            id=self.uuid,
            strategy_instance=self.strategy_instance,
            symbol=symbol,
            notional_usd=self.notional_usd,
            status=PositionStatus(self.status),
            target_apr_pct=self.target_apr_pct or Decimal("0"),
            funding_received=self.funding_received,
            realized_pnl=self.realized_pnl,
            fees_paid=self.fees_paid,
            opened_at=self.opened_at,
            closed_at=self.closed_at,
            exit_reason=ExitReason(self.exit_reason) if self.exit_reason else None,
        )

    def __repr__(self) -> str:
        return (
            f"<PositionRecord uuid={self.uuid[:8]} "
            f"status={self.status} "
            f"notional={self.notional_usd}>"
        )


class PositionLegRecord(Base):
    """对应 ``position_legs`` 表的 ORM 行。

    Why X5: 之前 PositionRecord 不持久化 legs，重启 restore 后 Position.legs=[]
    导致 close_position 跳过所有腿，DB 标 closed 但真实交易所持仓残留。
    """

    __tablename__ = "position_legs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        UUID(as_uuid=False), unique=True, nullable=False,
        default=lambda: str(uuid_lib.uuid4()),
    )
    position_id: Mapped[int] = mapped_column(
        ForeignKey("positions.id"), nullable=False, index=True,
    )
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    leverage: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1"))
    margin: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    funding_paid: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    fees_paid: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")

    @classmethod
    def from_domain(cls, leg: PositionLeg, position_id: int) -> "PositionLegRecord":
        return cls(
            position_id=position_id,
            exchange=leg.exchange,
            symbol=str(leg.symbol),
            instrument_type=leg.instrument_type.value,
            side=leg.side.value,
            size=leg.size,
            entry_price=leg.entry_price,
            current_price=leg.current_price,
            leverage=leg.leverage,
            margin=leg.margin_used,
            status="open",
        )

    def to_domain(self) -> PositionLeg:
        try:
            sym = Symbol.from_ccxt(self.symbol)
        except ValueError:
            base, _, quote = self.symbol.partition("/")
            sym = Symbol(base or "BTC", quote or "USDT")
        return PositionLeg(
            exchange=self.exchange,
            symbol=sym,
            instrument_type=InstrumentType(self.instrument_type),
            side=Side(self.side),
            size=self.size,
            entry_price=self.entry_price,
            leverage=self.leverage or Decimal("1"),
            current_price=self.current_price,
        )
