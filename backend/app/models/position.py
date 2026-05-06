"""ORM 模型 — positions 表

对应 schema.sql 中的 positions 表。
提供 from_domain() / to_domain() 在领域对象与 ORM 行之间转换。

注意：position_legs 的持久化将在 Week 6（执行层）实现；
当前只持久化 Position 头部信息。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.exchanges.models import Symbol
from app.risk.models import ExitReason, Position, PositionStatus


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
    def from_domain(cls, pos: Position) -> "PositionRecord":
        """从领域对象构建 ORM 记录（用于 upsert）。"""
        return cls(
            uuid=pos.id,
            strategy_instance=pos.strategy_instance,
            strategy_type="funding_rate",
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
