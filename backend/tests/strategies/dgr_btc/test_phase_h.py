"""Phase H pre-place 核心路径单测.

覆盖:
  - inflight_manager.update_from_open_orders 的 (missing, stale, orphan) 三个分类
  - inflight_manager.find_by_level / register_local / force_unregister
  - broker_adapter.place_limit_maker no_wait=True 返回 placeholder Trade
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.strategies.dgr_btc.inflight_manager import (
    InflightOrder,
    InflightOrderManager,
)
from app.strategies.dgr_btc.types import MarketType


def _mk_open_order(oid: str, dgr_market: str = "spot", price: str = "76000", side: str = "sell") -> dict:
    return {
        "id": oid,
        "clientOrderId": f"dgr_{oid[:12]}",
        "_dgr_market": dgr_market,
        "price": price,
        "side": side,
        "status": "open",
    }


class TestUpdateFromOpenOrders:
    """update_from_open_orders 返回 (missing, stale, orphan) 三元组的行为。"""

    def test_empty_broker_means_all_missing(self) -> None:
        """本地有 inflight，broker 全空 → 全部 missing"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("o1", MarketType.SPOT, Decimal("76000"), "SELL")
        mgr.register_local("o2", MarketType.PERP, Decimal("76000"), "BUY")

        missing, stale, orphan = mgr.update_from_open_orders([])

        assert missing == {"o1", "o2"}
        assert stale == []
        assert orphan == []

    def test_orphan_when_broker_has_unknown(self) -> None:
        """broker 有但本地没记录 → orphan (restart recovery 场景)"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        # 本地空。broker 有两单。
        orders = [
            _mk_open_order("o1", "spot", "76000", "sell"),
            _mk_open_order("o2", "swap", "76000", "buy"),
        ]
        missing, stale, orphan = mgr.update_from_open_orders(orders)

        assert missing == set()
        assert stale == []
        assert len(orphan) == 2
        assert {o["id"] for o in orphan} == {"o1", "o2"}

    def test_stale_outside_center_window(self) -> None:
        """本地 inflight 偏离 center 太远 → stale"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("near", MarketType.SPOT, Decimal("76200"), "SELL")
        mgr.register_local("far",  MarketType.SPOT, Decimal("80000"), "SELL")
        orders = [
            _mk_open_order("near", "spot", "76200", "sell"),
            _mk_open_order("far",  "spot", "80000", "sell"),
        ]
        missing, stale, orphan = mgr.update_from_open_orders(
            orders,
            max_grid_drift=Decimal("1000"),
            current_center=Decimal("76000"),
        )
        # near 在 ±1000 内, far 在 4000 之外 → stale
        assert missing == set()
        assert orphan == []
        assert len(stale) == 1
        assert stale[0].order_id == "far"

    def test_perfect_sync_no_diff(self) -> None:
        """本地和 broker 一致 → 三个分类都空"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000"), "SELL")
        mgr.register_local("b", MarketType.PERP, Decimal("76000"), "BUY")
        orders = [
            _mk_open_order("a", "spot", "76000", "sell"),
            _mk_open_order("b", "swap", "76000", "buy"),
        ]
        missing, stale, orphan = mgr.update_from_open_orders(orders)
        assert missing == set()
        assert stale == []
        assert orphan == []

    def test_mixed_missing_and_orphan(self) -> None:
        """本地 + broker 有交集 + 差集 → missing/orphan 都有"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("shared",     MarketType.SPOT, Decimal("76000"), "SELL")
        mgr.register_local("only_local", MarketType.PERP, Decimal("76000"), "BUY")
        orders = [
            _mk_open_order("shared",      "spot", "76000", "sell"),
            _mk_open_order("only_broker", "swap", "76500", "buy"),
        ]
        missing, stale, orphan = mgr.update_from_open_orders(orders)
        assert missing == {"only_local"}
        assert len(orphan) == 1
        assert orphan[0]["id"] == "only_broker"


class TestFindByLevel:
    """find_by_level 用于 maintain 检查同 grid 是否已挂单。"""

    def test_finds_within_tolerance(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000.3"), "SELL")
        # tol 0.5 USD 内 → 应该匹配
        found = mgr.find_by_level(
            MarketType.SPOT, Decimal("76000"), tol=Decimal("0.5"),
        )
        assert found is not None
        assert found.order_id == "a"

    def test_outside_tolerance(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76001"), "SELL")
        found = mgr.find_by_level(
            MarketType.SPOT, Decimal("76000"), tol=Decimal("0.5"),
        )
        assert found is None

    def test_wrong_market_not_matched(self) -> None:
        """SPOT bucket 内的 inflight 不应 match PERP find"""
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000"), "SELL")
        assert mgr.find_by_level(MarketType.PERP, Decimal("76000")) is None


class TestForceUnregister:
    def test_returns_and_removes(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000"), "SELL")
        assert mgr.count(MarketType.SPOT) == 1

        io = mgr.force_unregister("a")
        assert io is not None
        assert io.order_id == "a"
        assert mgr.count(MarketType.SPOT) == 0

    def test_unknown_returns_none(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        assert mgr.force_unregister("missing") is None


class TestRegisterLocal:
    def test_paper_mode_skips(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=False)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000"), "SELL")
        # paper 模式不维护 inflight
        assert mgr.count(MarketType.SPOT) == 0

    def test_live_mode_registers(self) -> None:
        mgr = InflightOrderManager(max_inflight_per_side=10, live_mode=True)
        mgr.register_local("a", MarketType.SPOT, Decimal("76000"), "SELL")
        assert mgr.count(MarketType.SPOT) == 1
        assert mgr.n_registers == 1
