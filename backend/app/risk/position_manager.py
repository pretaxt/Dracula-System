"""仓位管理器

维护所有持仓的内存状态，并将变更异步持久化到数据库。

设计原则：
- 内存字典 ``_positions`` 是唯一的读取来源（SSOT）
- 所有写操作先更新内存，再异步写 DB；DB 失败不回滚内存（允许短暂不一致）
- ``load_open_positions()`` 在服务启动时从 DB 恢复内存状态

用法::

    manager = PositionManager()
    await manager.load_open_positions()

    pos = manager.create(strategy_instance="funding_rate_main",
                         symbol=Symbol("BTC", "USDT"),
                         notional_usd=Decimal("500"))
    pos.add_leg(leg)
    pos.mark_open()
    await manager.save(pos)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from app.core.logging import get_logger
from app.exchanges.models import Symbol
from app.models.position import PositionRecord
from app.risk.models import ExitReason, Position, PositionLeg, PositionStatus

logger = get_logger(__name__)


class PositionManager:
    """线程安全（asyncio 单线程）的仓位管理器。"""

    def __init__(self) -> None:
        # key: position.id (UUID 字符串)
        self._positions: dict[str, Position] = {}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def all_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.is_open]

    @property
    def total_notional_usd(self) -> Decimal:
        return sum((p.notional_usd for p in self.open_positions), Decimal("0"))

    def get(self, position_id: str) -> Position | None:
        return self._positions.get(position_id)

    def get_by_symbol(self, symbol: Symbol) -> list[Position]:
        return [p for p in self.open_positions if p.symbol == symbol]

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    def create(
        self,
        strategy_instance: str,
        symbol: Symbol,
        notional_usd: Decimal,
        target_apr_pct: Decimal = Decimal("0"),
        legs: Sequence[PositionLeg] | None = None,
    ) -> Position:
        """在内存中创建新仓位（状态 PENDING）。调用方需随后调用 save()。"""
        pos = Position(
            strategy_instance=strategy_instance,
            symbol=symbol,
            notional_usd=notional_usd,
            target_apr_pct=target_apr_pct,
        )
        if legs:
            for leg in legs:
                pos.add_leg(leg)
        self._positions[pos.id] = pos
        logger.info(
            "position_created",
            position_id=pos.id,
            symbol=str(symbol),
            notional_usd=str(notional_usd),
        )
        return pos

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    def record_funding(self, position_id: str, amount: Decimal) -> None:
        """累积已收取的资金费。"""
        pos = self._require(position_id)
        pos.funding_received += amount

    def record_fees(self, position_id: str, amount: Decimal) -> None:
        """累积已支付的手续费。"""
        pos = self._require(position_id)
        pos.fees_paid += amount

    def close(
        self,
        position_id: str,
        reason: ExitReason,
        realized_pnl: Decimal | None = None,
    ) -> Position:
        """关闭仓位（内存态），需随后调用 save() 持久化。"""
        pos = self._require(position_id)
        pos.mark_closed(reason=reason, realized_pnl=realized_pnl)
        logger.info(
            "position_closed",
            position_id=position_id,
            reason=reason.value,
            pnl=str(pos.total_pnl),
        )
        return pos

    # ------------------------------------------------------------------
    # DB 持久化
    # ------------------------------------------------------------------

    async def save(self, position: Position) -> None:
        """将仓位状态 upsert 到数据库。"""
        try:
            from app.core.database import get_session  # noqa: PLC0415

            record = PositionRecord.from_domain(position)
            async with get_session() as session:
                await session.merge(record)
            logger.debug("position_saved", position_id=position.id, status=position.status.value)
        except Exception:
            logger.exception("position_save_failed", position_id=position.id)

    async def load_open_positions(self) -> int:
        """启动时从 DB 恢复所有 OPEN/PENDING 仓位到内存。返回恢复数量。"""
        try:
            from sqlalchemy import select  # noqa: PLC0415

            from app.core.database import get_session  # noqa: PLC0415

            async with get_session() as session:
                result = await session.execute(
                    select(PositionRecord).where(
                        PositionRecord.status.in_(["open", "pending"])
                    )
                )
                records: list[PositionRecord] = list(result.scalars().all())

            for record in records:
                pos = record.to_domain()
                self._positions[pos.id] = pos

            count = len(records)
            logger.info("positions_loaded_from_db", count=count)
            return count
        except Exception:
            logger.exception("position_load_failed")
            return 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _require(self, position_id: str) -> Position:
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"仓位不存在: {position_id}")
        return pos
