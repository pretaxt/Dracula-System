"""Tests for inflight_manager (Phase E.5)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.dgr_btc.inflight_manager import (
    InflightOrderManager,
    EvictionResult,
)
from app.strategies.dgr_btc.types import MarketType


class FakeBroker:
    def __init__(self) -> None:
        self.canceled: list[str] = []

    async def cancel_order(self, order_id: str) -> None:
        self.canceled.append(order_id)


# ============================================================
# PAPER mode skip
# ============================================================

class TestPaperModeSkip:
    @pytest.mark.asyncio
    async def test_paper_mode_always_accepts(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=2, live_mode=False)
        for i in range(10):
            r = await mgr.register(
                f"o{i}", MarketType.SPOT, Decimal("76000"), "SELL",
                Decimal("76000"),
            )
            assert r.accepted
        # paper mode 不实际维护 in-flight
        assert mgr.count(MarketType.SPOT) == 0


# ============================================================
# LIVE mode — cap not exceeded
# ============================================================

class TestUnderCap:
    @pytest.mark.asyncio
    async def test_register_under_cap_succeeds(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=2, broker_adapter=broker, live_mode=True,
        )
        r1 = await mgr.register("o1", MarketType.SPOT, Decimal("76000"), "SELL", Decimal("76000"))
        r2 = await mgr.register("o2", MarketType.SPOT, Decimal("76250"), "SELL", Decimal("76000"))
        assert r1.accepted and r2.accepted
        assert mgr.count(MarketType.SPOT) == 2
        assert len(broker.canceled) == 0

    @pytest.mark.asyncio
    async def test_spot_and_perp_independent_buckets(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=2, broker_adapter=broker, live_mode=True,
        )
        for i in range(2):
            await mgr.register(f"s{i}", MarketType.SPOT, Decimal("76000") + i * Decimal("250"), "SELL", Decimal("76000"))
            await mgr.register(f"p{i}", MarketType.PERP, Decimal("76000") + i * Decimal("250"), "SELL", Decimal("76000"))
        assert mgr.count(MarketType.SPOT) == 2
        assert mgr.count(MarketType.PERP) == 2
        assert mgr.total_inflight() == 4


# ============================================================
# Eviction logic
# ============================================================

class TestEviction:
    @pytest.mark.asyncio
    async def test_evicts_farthest_when_new_is_closer(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=2, broker_adapter=broker, live_mode=True,
        )
        center = Decimal("76000")
        # 先填满: 76250 (距 250) + 76500 (距 500)
        await mgr.register("o1", MarketType.SPOT, Decimal("76250"), "SELL", center)
        await mgr.register("o2", MarketType.SPOT, Decimal("76500"), "SELL", center)
        # 新单 76100 (距 100) → 应挤掉 76500 (最远)
        r = await mgr.register("o3", MarketType.SPOT, Decimal("76100"), "SELL", center)
        assert r.accepted
        assert r.evicted_order_id == "o2"
        assert r.evicted_grid_level == Decimal("76500")
        assert "o2" in broker.canceled
        # 新桶应该是 o1 + o3
        levels = sorted(o.grid_level for o in mgr.get_inflight(MarketType.SPOT))
        assert levels == [Decimal("76100"), Decimal("76250")]
        assert mgr.n_evictions == 1

    @pytest.mark.asyncio
    async def test_rejects_new_if_farther_than_all_existing(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=2, broker_adapter=broker, live_mode=True,
        )
        center = Decimal("76000")
        await mgr.register("o1", MarketType.SPOT, Decimal("76100"), "SELL", center)
        await mgr.register("o2", MarketType.SPOT, Decimal("76250"), "SELL", center)
        # 新单 76500 (距 500) > 现有 max (距 250) → reject
        r = await mgr.register("o3", MarketType.SPOT, Decimal("76500"), "SELL", center)
        assert r.accepted is False
        assert r.evicted_order_id is None
        assert len(broker.canceled) == 0
        assert mgr.n_rejected == 1
        # 桶不变
        assert mgr.count(MarketType.SPOT) == 2


# ============================================================
# Unregister
# ============================================================

class TestUnregister:
    @pytest.mark.asyncio
    async def test_unregister_releases_slot(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=2, broker_adapter=broker, live_mode=True,
        )
        await mgr.register("o1", MarketType.SPOT, Decimal("76250"), "SELL", Decimal("76000"))
        await mgr.register("o2", MarketType.SPOT, Decimal("76500"), "SELL", Decimal("76000"))
        ok = mgr.unregister("o1", MarketType.SPOT)
        assert ok
        assert mgr.count(MarketType.SPOT) == 1
        # Now slot freed, can accept new
        r = await mgr.register("o3", MarketType.SPOT, Decimal("76750"), "SELL", Decimal("76000"))
        assert r.accepted
        assert mgr.count(MarketType.SPOT) == 2

    def test_unregister_unknown_returns_false(self) -> None:
        mgr = InflightOrderManager(live_mode=True)
        ok = mgr.unregister("never_existed", MarketType.SPOT)
        assert ok is False


# ============================================================
# Stats
# ============================================================

class TestStats:
    @pytest.mark.asyncio
    async def test_stats_track_eviction_and_rejection(self) -> None:
        broker = FakeBroker()
        mgr = InflightOrderManager(
            max_inflight_per_side=1, broker_adapter=broker, live_mode=True,
        )
        center = Decimal("76000")
        await mgr.register("o1", MarketType.SPOT, Decimal("76250"), "SELL", center)
        # 76100 距 100 < 76250 距 250 → evict o1
        await mgr.register("o2", MarketType.SPOT, Decimal("76100"), "SELL", center)
        # 76500 距 500 > 76100 距 100 → reject
        await mgr.register("o3", MarketType.SPOT, Decimal("76500"), "SELL", center)
        s = mgr.stats()
        assert s["n_registers"] == 3
        assert s["n_evictions"] == 1
        assert s["n_rejected"] == 1
        assert s["current_spot_inflight"] == 1
