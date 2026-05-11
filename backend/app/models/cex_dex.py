"""ORM 模型 — cex_dex_opportunities (TimescaleDB 超级表)"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CexDexOpportunityRecord(Base):
    __tablename__ = "cex_dex_opportunities"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pair: Mapped[str] = mapped_column(String(20), primary_key=True)
    direction: Mapped[str] = mapped_column(String(20), primary_key=True)

    cex_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    dex_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    raw_spread_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    estimated_gas_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    net_profit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    trade_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")
    executed: Mapped[bool] = mapped_column(default=False)
