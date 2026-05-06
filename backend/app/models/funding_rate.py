"""ORM 模型 — funding_rate_history (TimescaleDB 超级表)

复合主键 (time, exchange, symbol) 对应 TimescaleDB hypertable 的分区键。
Alembic 迁移已通过 schema.sql 手动建表，此模型仅用于 ORM 查询与插入。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FundingRateRecord(Base):
    """对应 ``funding_rate_history`` 超级表的行。"""

    __tablename__ = "funding_rate_history"

    # -------------------------------------------------------------------
    # 复合主键 (TimescaleDB hypertable 按 time 分区)
    # -------------------------------------------------------------------
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        comment="资金费率结算时间 (UTC)",
    )
    exchange: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        comment="交易所标识符，如 binance",
    )
    symbol: Mapped[str] = mapped_column(
        String(30),
        primary_key=True,
        comment="交易对，如 BTC/USDT",
    )

    # -------------------------------------------------------------------
    # 业务字段
    # -------------------------------------------------------------------
    instrument_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="合约类型：PERPETUAL / SPOT",
    )
    funding_rate: Mapped[Decimal] = mapped_column(
        Numeric(15, 10),
        nullable=False,
        comment="原始资金费率，如 0.0001",
    )
    apr_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
        comment="年化收益率百分比，如 10.95",
    )
    next_funding_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="下次资金费率结算时间 (UTC)",
    )
    funding_interval_hours: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="资金费率结算间隔小时数，如 8",
    )

    def __repr__(self) -> str:
        return (
            f"<FundingRateRecord "
            f"exchange={self.exchange!r} "
            f"symbol={self.symbol!r} "
            f"rate={self.funding_rate} "
            f"time={self.time.isoformat() if self.time else None}>"
        )
