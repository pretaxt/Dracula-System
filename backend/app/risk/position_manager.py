"""仓位管理器

维护所有持仓的内存状态，并将变更异步持久化到数据库。

设计原则：
- 内存字典 ``_positions`` 是唯一的读取来源（SSOT）
- 所有写操作先更新内存，再异步写 DB；DB 失败不回滚内存（允许短暂不一致）
- ``load_open_positions()`` 在服务启动时从 DB 恢复内存状态
- P0-γ: per-position lock 防止并发 close 导致双重平仓 / 双重 DB 写

用法::

    manager = PositionManager()
    await manager.load_open_positions()

    pos = manager.create(strategy_instance="funding_rate_main",
                         symbol=Symbol("BTC", "USDT"),
                         notional_usd=Decimal("500"))
    pos.add_leg(leg)
    pos.mark_open()
    await manager.save(pos)

    # 关键 close path：
    async with manager.lock_for(pos.id):
        await executor.close_position(pos.id, reason=...)
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Sequence

from app.core.logging import get_logger
from app.exchanges.models import Symbol
from app.models.position import PositionRecord
from app.risk.models import ExitReason, Position, PositionLeg, PositionStatus

logger = get_logger(__name__)


class PositionManager:
    """线程安全（asyncio 单线程）的仓位管理器。"""

    def __init__(self, strategy_type: str = "funding_rate") -> None:
        # key: position.id (UUID 字符串)
        self._positions: dict[str, Position] = {}
        self._strategy_type = strategy_type
        # P0-γ: per-position asyncio.Lock，防止并发 close 双重平仓
        # asyncio 单线程不会数据竞争，但 close_position 内部多个 await 之间
        # 状态可能被外部 modify。lock 序列化关键路径。
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, position_id: str) -> asyncio.Lock:
        """获取该仓位的串行化锁（lazy 创建）。

        典型用法：
            async with manager.lock_for(pos.id):
                await executor.close_position(pos.id, ...)
        """
        if position_id not in self._locks:
            self._locks[position_id] = asyncio.Lock()
        return self._locks[position_id]

    def _release_lock(self, position_id: str) -> None:
        """完全清理该 position 的 lock（仅在 position 移除时调用）。"""
        self._locks.pop(position_id, None)

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

    def discard(self, position_id: str) -> bool:
        """从内存丢弃尚未成功开仓的 position（broker 失败回滚）。

        Why: 旧实现在 broker.execute_pair 抛错时 `_positions[id]` 仍残留，
        造成"幽灵持仓"——内存计数 +1 但 DB 没记录，触发 max_positions 错误屏蔽
        后续候选。仅在 ``open_delta_neutral`` 早期失败路径调用，不写 DB。

        Returns
        -------
        bool
            True 实际删除了一项；False 表示 id 不存在（幂等）。
        """
        if position_id in self._positions:
            del self._positions[position_id]
            self._release_lock(position_id)
            logger.info("position_discarded", position_id=position_id)
            return True
        return False

    # ------------------------------------------------------------------
    # DB 持久化
    # ------------------------------------------------------------------

    async def save(self, position: Position) -> None:
        """将仓位状态 + 所有 legs upsert 到数据库（X5 修复：legs 必须持久化）。

        实现细节：用 uuid 反查 id 避免 session.merge 在 PK=None 时新插行
        （schema 是 id bigint PK + uuid unique）。每次 save 全量替换 legs。
        """
        try:
            from sqlalchemy import delete, select  # noqa: PLC0415

            from app.core.database import get_session  # noqa: PLC0415
            from app.models.position import PositionLegRecord  # noqa: PLC0415

            async with get_session() as session:
                # 1. 先按 uuid 查现有 id（如已存在则更新而非插入新行）
                existing_id = (
                    await session.execute(
                        select(PositionRecord.id).where(
                            PositionRecord.uuid == position.id
                        )
                    )
                ).scalar_one_or_none()

                record = PositionRecord.from_domain(position, strategy_type=self._strategy_type)
                if existing_id is not None:
                    record.id = existing_id
                merged = await session.merge(record)
                await session.flush()  # 确保 merged.id 可用

                # 2. 全量替换 legs（每次 save 都是 SSOT 同步）
                if merged.id is not None:
                    await session.execute(
                        delete(PositionLegRecord).where(
                            PositionLegRecord.position_id == merged.id
                        )
                    )
                    for leg in position.legs:
                        leg_rec = PositionLegRecord.from_domain(leg, merged.id)
                        if position.status == PositionStatus.CLOSED:
                            leg_rec.status = "closed"
                        session.add(leg_rec)

            logger.debug(
                "position_saved",
                position_id=position.id,
                status=position.status.value,
                legs=len(position.legs),
            )
        except Exception:
            logger.exception("position_save_failed", position_id=position.id)

    async def load_open_positions(self) -> int:
        """启动时从 DB 恢复所有 OPEN/PENDING 仓位 + 其 legs 到内存。返回恢复数量。

        X5 修复：必须连同 legs 一起加载，否则 close_position 跳过所有腿。
        """
        try:
            from sqlalchemy import select  # noqa: PLC0415

            from app.core.database import get_session  # noqa: PLC0415
            from app.models.position import PositionLegRecord  # noqa: PLC0415

            async with get_session() as session:
                result = await session.execute(
                    select(PositionRecord).where(
                        PositionRecord.status.in_(["open", "pending"]),
                        PositionRecord.strategy_type == self._strategy_type,
                    )
                )
                records: list[PositionRecord] = list(result.scalars().all())

                # 批量加载 legs（按 position_id 分组）
                if records:
                    pos_ids = [r.id for r in records]
                    leg_result = await session.execute(
                        select(PositionLegRecord).where(
                            PositionLegRecord.position_id.in_(pos_ids)
                        )
                    )
                    leg_rows: list[PositionLegRecord] = list(leg_result.scalars().all())
                    legs_by_pos: dict[int, list[PositionLegRecord]] = {}
                    for lr in leg_rows:
                        legs_by_pos.setdefault(lr.position_id, []).append(lr)
                else:
                    legs_by_pos = {}

            restored = 0
            missing_legs = 0
            for record in records:
                pos = record.to_domain()
                for leg_rec in legs_by_pos.get(record.id, []):
                    pos.legs.append(leg_rec.to_domain())
                if not pos.legs:
                    missing_legs += 1
                    logger.warning(
                        "position_restored_without_legs",
                        position_id=pos.id,
                        symbol=str(pos.symbol),
                    )
                self._positions[pos.id] = pos
                restored += 1

            logger.info(
                "positions_loaded_from_db",
                count=restored,
                missing_legs=missing_legs,
            )
            return restored
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
