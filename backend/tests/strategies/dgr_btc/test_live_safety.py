"""Tests for LiveSafetyGuard."""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from app.strategies.dgr_btc.live_safety import (
    LiveSafetyConfig,
    LiveSafetyGuard,
)
from app.strategies.dgr_btc.types import MarketType, Side


@pytest.fixture
def cfg(tmp_path):
    return LiveSafetyConfig(
        enabled=True,
        max_order_usd=Decimal("100"),
        max_daily_notional_usd=Decimal("500"),
        max_daily_order_count=5,
        max_price_deviation_pct=Decimal("0.005"),
        kill_switch_path=str(tmp_path / "KILL"),
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


class TestNormalFlow:
    def test_under_all_limits_passes(self, cfg):
        g = LiveSafetyGuard(cfg)
        ok, reason = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            Decimal("76000"), Decimal("0.001"),  # notional $76 < $100
        )
        assert ok is True
        assert reason is None

    def test_disabled_always_passes(self, cfg):
        cfg.enabled = False
        g = LiveSafetyGuard(cfg)
        ok, reason = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            Decimal("76000"), Decimal("10"),  # 巨大单
        )
        assert ok is True


class TestMaxOrderUsd:
    def test_over_max_order_rejects(self, cfg):
        g = LiveSafetyGuard(cfg)
        ok, reason = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            Decimal("76000"), Decimal("0.01"),  # notional $760 > $100
        )
        assert ok is False
        assert reason is not None and "max_order_usd" in reason
        assert g.n_block_max_order == 1


class TestDailyCaps:
    def test_daily_notional_cap(self, tmp_path):
        # 单独 cfg: count 大, notional 200 — 避免 count 先触
        cfg2 = LiveSafetyConfig(
            enabled=True, max_order_usd=Decimal("100"),
            max_daily_notional_usd=Decimal("200"), max_daily_order_count=100,
            kill_switch_path=str(tmp_path / "KILL_n"),
            audit_log_path=str(tmp_path / "audit_n.jsonl"),
        )
        g = LiveSafetyGuard(cfg2)
        for _ in range(2):
            ok, _ = g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("80"), Decimal("1"))
            assert ok
            g.record_filled(Decimal("80"))
        # 3rd: 160+80=240 > 200 → reject
        ok, reason = g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("80"), Decimal("1"))
        assert not ok
        assert "max_daily_notional" in (reason or "")
        assert g.n_block_daily_notional == 1

    def test_daily_count_cap(self, cfg):
        g = LiveSafetyGuard(cfg)  # max_daily_order_count=5
        for i in range(5):
            ok, _ = g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("50"), Decimal("0.1"))
            assert ok
            g.record_filled(Decimal("5"))
        # 6th → count > 5
        ok, reason = g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("50"), Decimal("0.1"))
        assert not ok
        assert "max_daily_order_count" in (reason or "")


class TestKillSwitch:
    def test_kill_switch_blocks_all(self, cfg):
        g = LiveSafetyGuard(cfg)
        # 触 kill
        g.trigger_kill(reason="test")
        assert g.is_killed()
        ok, reason = g.check_pre_order(
            MarketType.SPOT, Side.SELL, Decimal("10"), Decimal("0.001"),
        )
        assert not ok
        assert reason == "kill_switch_active"
        assert g.n_block_killswitch == 1

    def test_clear_kill(self, cfg):
        g = LiveSafetyGuard(cfg)
        g.trigger_kill("test")
        assert g.is_killed()
        g.clear_kill()
        assert not g.is_killed()
        ok, _ = g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("10"), Decimal("0.001"))
        assert ok


class TestPriceDeviation:
    def test_price_far_from_mark_rejected(self, cfg):
        g = LiveSafetyGuard(cfg)
        # price 76000, mark 75000 → dev = 1000/75000 ≈ 1.3% > 0.5%
        ok, reason = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            price=Decimal("76000"), quantity=Decimal("0.001"),
            mark_price=Decimal("75000"),
        )
        assert not ok
        assert "price_deviation" in (reason or "")

    def test_price_close_to_mark_ok(self, cfg):
        g = LiveSafetyGuard(cfg)
        ok, _ = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            price=Decimal("76000"), quantity=Decimal("0.001"),
            mark_price=Decimal("76100"),  # dev 0.13% < 0.5%
        )
        assert ok

    def test_no_mark_skips_check(self, cfg):
        g = LiveSafetyGuard(cfg)
        ok, _ = g.check_pre_order(
            MarketType.SPOT, Side.SELL,
            price=Decimal("76000"), quantity=Decimal("0.001"),
            mark_price=None,
        )
        assert ok


class TestAudit:
    def test_audit_writes_jsonl(self, cfg):
        g = LiveSafetyGuard(cfg)
        g.audit({"action": "fill", "market": "PERP", "qty": "0.01"})
        g.audit({"action": "cancel", "order_id": "abc"})
        with open(cfg.audit_log_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["action"] == "fill"
        assert "ts" in first


class TestStats:
    def test_stats_aggregate(self, cfg):
        g = LiveSafetyGuard(cfg)
        # 1 pass (notional $5), 1 block max_order (notional $150 > $100), 1 block kill
        g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("50"), Decimal("0.1"))  # $5
        g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("100"), Decimal("1.5"))  # $150 > $100
        g.trigger_kill("t")
        g.check_pre_order(MarketType.SPOT, Side.SELL, Decimal("10"), Decimal("0.001"))
        s = g.stats()
        assert s["n_check"] == 3
        assert s["n_block_max_order"] == 1
        assert s["n_block_killswitch"] == 1
        assert s["is_killed"] is True
