"""
dgr_btc/inflight_manager.py
===========================
InflightOrderManager — 限制同时 in-flight 的 grid 单数量 (Phase E.5).

问题:
  剧烈波动下 grid trigger 密集, 短时间内堆积 N 个 LIMIT_MAKER:
  - binance per-symbol rate-limit (50 orders / 10s)
  - 撤单复杂度爆炸 (止损时要撤一大堆)
  - 自打风险 (家自家 SELL hit 自家 BUY = wash trade)

解决:
  - 每条腿 (spot / perp) 最多 max_inflight_per_side 个活单 (默认 2)
  - 超过 → 撤离 current_center 最远的旧单 → 让位给新单
  - PAPER 模式跳过 (paper 即时成交, 没 in-flight)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.types import MarketType

logger = structlog.get_logger(__name__)


@dataclass
class InflightOrder:
    order_id: str
    market: MarketType
    grid_level: Decimal
    placed_at: datetime
    side: str  # "BUY" / "SELL"


@dataclass
class EvictionResult:
    accepted: bool
    evicted_order_id: Optional[str] = None
    evicted_grid_level: Optional[Decimal] = None
    reason: str = ""


class InflightOrderManager:
    def __init__(
        self,
        max_inflight_per_side: int = 2,
        broker_adapter: Any | None = None,
        live_mode: bool = False,
    ) -> None:
        self.max_per_side = max_inflight_per_side
        self.broker = broker_adapter
        self.live_mode = live_mode
        # 按 market 分桶: spot orders + perp orders
        self._orders: dict[MarketType, list[InflightOrder]] = {
            MarketType.SPOT: [],
            MarketType.PERP: [],
        }
        self.n_registers = 0
        self.n_evictions = 0
        self.n_rejected = 0

    async def register(
        self,
        order_id: str,
        market: MarketType,
        grid_level: Decimal,
        side: str,
        current_center: Decimal,
    ) -> EvictionResult:
        """注册新 inflight 单. 若已满, 评估能否挤掉最远的旧单 (距 center 比新单更远).

        PAPER 模式: 不强制 cap (直接 accept, 不维护 in-flight 集合)
        """
        if not self.live_mode:
            return EvictionResult(accepted=True, reason="paper_mode_skipped")

        self.n_registers += 1
        bucket = self._orders[market]

        # 1. 未到上限: 直接 accept
        if len(bucket) < self.max_per_side:
            bucket.append(InflightOrder(
                order_id=order_id, market=market, grid_level=grid_level,
                placed_at=datetime.now(timezone.utc), side=side,
            ))
            return EvictionResult(accepted=True)

        # 2. 到上限: 找最远 (距 center) 的旧单, 比新单更远 → evict
        new_dist = abs(grid_level - current_center)
        farthest = max(
            bucket,
            key=lambda o: abs(o.grid_level - current_center),
        )
        farthest_dist = abs(farthest.grid_level - current_center)

        if farthest_dist <= new_dist:
            # 新单比所有现有的都远 → 直接 reject
            self.n_rejected += 1
            logger.info(
                "dgr_btc_inflight_new_too_far",
                market=market.value,
                new_level=str(grid_level),
                new_dist=str(new_dist),
                farthest_dist=str(farthest_dist),
            )
            return EvictionResult(
                accepted=False,
                reason=f"new_grid {grid_level} farther than farthest_inflight {farthest.grid_level}",
            )

        # 3. evict 最远旧单
        await self._cancel_broker_order(farthest.order_id)
        bucket.remove(farthest)
        bucket.append(InflightOrder(
            order_id=order_id, market=market, grid_level=grid_level,
            placed_at=datetime.now(timezone.utc), side=side,
        ))
        self.n_evictions += 1
        logger.info(
            "dgr_btc_inflight_evicted",
            market=market.value,
            evicted_order=farthest.order_id,
            evicted_level=str(farthest.grid_level),
            new_level=str(grid_level),
        )
        return EvictionResult(
            accepted=True,
            evicted_order_id=farthest.order_id,
            evicted_grid_level=farthest.grid_level,
            reason="evicted_farthest",
        )

    def unregister(self, order_id: str, market: MarketType) -> bool:
        """成交/撤单后调用, 释放 slot."""
        bucket = self._orders[market]
        for o in bucket:
            if o.order_id == order_id:
                bucket.remove(o)
                return True
        return False

    def get_inflight(self, market: MarketType) -> list[InflightOrder]:
        return list(self._orders[market])

    def count(self, market: MarketType) -> int:
        return len(self._orders[market])

    def total_inflight(self) -> int:
        return sum(len(v) for v in self._orders.values())

    async def _cancel_broker_order(self, order_id: str) -> None:
        if self.broker is None:
            return
        try:
            await self.broker.cancel_order(order_id)
        except Exception as e:
            logger.warning(
                "dgr_btc_inflight_evict_cancel_failed",
                order_id=order_id, error=str(e)[:120],
            )

    def stats(self) -> dict:
        return {
            "n_registers": self.n_registers,
            "n_evictions": self.n_evictions,
            "n_rejected": self.n_rejected,
            "current_spot_inflight": self.count(MarketType.SPOT),
            "current_perp_inflight": self.count(MarketType.PERP),
            "max_per_side": self.max_per_side,
        }

    # ------------------------------------------------------------------
    # Phase H.5: 与 broker.fetch_open_orders 增量同步
    # ------------------------------------------------------------------

    def update_from_open_orders(
        self,
        open_orders: list[dict],
        max_grid_drift: Decimal | None = None,
        current_center: Decimal | None = None,
    ) -> tuple[set[str], list[InflightOrder], list[dict]]:
        """根据 broker.fetch_open_orders() 返回值同步本地 inflight。

        返回 (missing_order_ids, stale_orders, orphan_in_broker):
          - missing_order_ids: 本地有但 broker 列表中已消失的 order_id (= filled or canceled)
          - stale_orders: broker 列表中且本地有，但偏离 current_center 太远的 inflight
          - orphan_in_broker: broker 列表中存在但本地 inflight 没记录的（restart 后恢复用）

        调用方接到 missing_order_ids 后调 broker.fetch_order 拿最终状态决定 on_trade。
        stale_orders 留给调用方 cancel。
        orphan_in_broker 留给调用方 register_local 回收（restart recovery）。
        """
        # 1. 算 broker 端当前活单的 (order_id) 集合 + 本地 inflight 集合
        broker_ids: set[str] = set()
        for o in open_orders:
            oid = str(o.get("id") or o.get("orderId") or o.get("clientOrderId") or "")
            if oid:
                broker_ids.add(oid)
        local_ids: set[str] = set()
        for market in [MarketType.SPOT, MarketType.PERP]:
            for io in self._orders[market]:
                local_ids.add(io.order_id)

        # 2. 本地存在但 broker 没有 → missing (filled/canceled)
        missing: set[str] = set()
        for market in [MarketType.SPOT, MarketType.PERP]:
            for io in list(self._orders[market]):
                if io.order_id not in broker_ids:
                    missing.add(io.order_id)
                    # 不立即 unregister — 由调用方拿到 fetch_order 状态后决定

        # 3. broker 端 stale 单（偏离 center 太远）— 仅 local 端的 inflight
        stale: list[InflightOrder] = []
        if max_grid_drift is not None and current_center is not None:
            for market in [MarketType.SPOT, MarketType.PERP]:
                for io in self._orders[market]:
                    dist = abs(io.grid_level - current_center)
                    if dist > max_grid_drift:
                        stale.append(io)

        # 4. orphan: broker 端有但本地没有（restart recovery 用 — 调用方 register_local 回收）
        orphan: list[dict] = []
        for o in open_orders:
            oid = str(o.get("id") or o.get("orderId") or o.get("clientOrderId") or "")
            if oid and oid not in local_ids:
                orphan.append(o)

        return missing, stale, orphan

    def force_unregister(self, order_id: str) -> Optional[InflightOrder]:
        """无条件移除并返回 inflight 对象（_sync_fills 处理完 fill / cancel 后调）。"""
        for market in [MarketType.SPOT, MarketType.PERP]:
            bucket = self._orders[market]
            for o in bucket:
                if o.order_id == order_id:
                    bucket.remove(o)
                    return o
        return None

    def find_by_level(
        self, market: MarketType, grid_level: Decimal, tol: Decimal = Decimal("0.5")
    ) -> Optional[InflightOrder]:
        """maintain 用：查在某 grid_level（±tol USD）已有的 inflight。

        tol 默认 0.5 USD 防 float 精度抖动。
        """
        for io in self._orders[market]:
            if abs(io.grid_level - grid_level) <= tol:
                return io
        return None

    def register_local(
        self,
        order_id: str,
        market: MarketType,
        grid_level: Decimal,
        side: str,
    ) -> None:
        """maintain 派单后直接 register (绕开 eviction 逻辑, eviction 由 update_from_open_orders 兜底)。"""
        if not self.live_mode:
            return
        self._orders[market].append(InflightOrder(
            order_id=order_id, market=market, grid_level=grid_level,
            placed_at=datetime.now(timezone.utc), side=side,
        ))
        self.n_registers += 1
