"""Tests for LiveMetricsCollector (§6.2 #4)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.strategies.dgr_btc.live_metrics import (
    LiveMetricsCollector,
    _active_collectors,
    get_collector,
    list_collectors,
    register_collector,
    unregister_collector,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear module-level registry before each test (isolation)."""
    _active_collectors.clear()
    yield
    _active_collectors.clear()


@pytest.fixture
def collector():
    return LiveMetricsCollector(instance_name="test")


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------


class TestRegistry:
    def test_register_then_get(self, collector):
        register_collector("test", collector)
        assert get_collector("test") is collector

    def test_list_returns_copy(self, collector):
        register_collector("test", collector)
        d = list_collectors()
        d["intruder"] = None  # should not affect internal registry
        assert "intruder" not in _active_collectors

    def test_unregister(self, collector):
        register_collector("test", collector)
        unregister_collector("test")
        assert get_collector("test") is None


# ----------------------------------------------------------------------
# Maker fill rate
# ----------------------------------------------------------------------


class TestMakerFillRate:
    def test_insufficient_samples_returns_none(self, collector):
        # 1 sample: < 5
        collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        assert collector.maker_fill_rate() is None

    def test_all_filled(self, collector):
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        assert collector.maker_fill_rate() == 1.0

    def test_mixed_with_post_only_rejects(self, collector):
        for _ in range(8):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        for _ in range(2):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        # 8/10 = 0.8
        assert collector.maker_fill_rate() == pytest.approx(0.8)

    def test_window_excludes_old_samples(self, collector):
        # 10 old samples (filled)
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        # 手动改 ts 让它们落在 window 外
        for rec in collector._maker_records:
            rec.ts = old_ts
        # 5 new fresh rejects
        for _ in range(5):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        # 仅 5 new (全是 reject) → 0.0
        rate = collector.maker_fill_rate(window_sec=600)
        assert rate == 0.0


# ----------------------------------------------------------------------
# Latency p95
# ----------------------------------------------------------------------


class TestLatencyP95:
    def test_insufficient_samples_returns_none(self, collector):
        collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=100)
        collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=200)
        # 2 samples < 3
        assert collector.latency_p95_ms() is None

    def test_p95_basic(self, collector):
        for i in range(100):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=float(i))
        # p95 of 0..99 = index 94 (0-indexed) → 94
        # int(100 * 0.95) - 1 = 94
        p95 = collector.latency_p95_ms()
        assert p95 == 94.0

    def test_rejected_orders_excluded_from_latency(self, collector):
        # 10 fast fills + 10 rejects (no latency)
        for i in range(10):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50.0)
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only", latency_ms=None)
        # rejects 没 latency, 不进入 p95
        assert collector.latency_p95_ms() == 50.0


# ----------------------------------------------------------------------
# Safety reject rate
# ----------------------------------------------------------------------


class TestSafetyRejectRate:
    def test_zero_by_default(self, collector):
        assert collector.safety_reject_rate_per_hour() == 0

    def test_count_within_1h(self, collector):
        for _ in range(3):
            collector.record_safety_reject("max_order_usd $1000 > $500")
        assert collector.safety_reject_rate_per_hour() == 3

    def test_old_rejects_excluded(self, collector):
        for _ in range(5):
            collector.record_safety_reject("kill_switch_active")
        # Move all to >1h ago
        old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
        for rec in collector._safety_records:
            rec.ts = old_ts
        assert collector.safety_reject_rate_per_hour() == 0

    def test_breakdown_groups_by_reason_key(self, collector):
        collector.record_safety_reject("max_order_usd $100 > $50")
        collector.record_safety_reject("max_order_usd $200 > $50")
        collector.record_safety_reject("kill_switch_active")
        bd = collector.safety_reject_breakdown_1h()
        assert bd["max_order_usd"] == 2
        assert bd["kill_switch_active"] == 1


# ----------------------------------------------------------------------
# Threshold breach + alert dedup
# ----------------------------------------------------------------------


class TestThresholdEval:
    def test_fill_rate_below_floor_no_breach_yet_until_duration(self, collector):
        # 10 rejects = 0% fill rate, status=breach
        # but breach_required_sec=600 — 第一次 check 只 set breached_since, 不发
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        alerts = collector.check_thresholds()
        # No alert yet (just started breach)
        assert all(a["key"] != "maker_fill_rate" for a in alerts)
        # breached_since should be set
        assert collector._alert_states["maker_fill_rate"].breached_since is not None

    def test_fill_rate_breach_fires_after_duration(self, collector):
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        # 1st check: set breached_since
        collector.check_thresholds()
        # Force breached_since to 11 min ago
        collector._alert_states["maker_fill_rate"].breached_since = (
            datetime.now(timezone.utc) - timedelta(minutes=11)
        )
        alerts = collector.check_thresholds()
        fill_alerts = [a for a in alerts if a["key"] == "maker_fill_rate"]
        assert len(fill_alerts) == 1
        assert "maker fill rate" in fill_alerts[0]["message"]
        assert fill_alerts[0]["value"] == 0.0

    def test_safety_reject_breach_fires_immediately(self, collector):
        # safety reject 阈值 5/h, breach_required_sec=0 — 触发即报
        for _ in range(6):
            collector.record_safety_reject("max_order_usd")
        alerts = collector.check_thresholds()
        safety_alerts = [a for a in alerts if a["key"] == "safety_reject_rate"]
        assert len(safety_alerts) == 1
        assert safety_alerts[0]["value"] == 6.0

    def test_alert_cooldown_dedups(self, collector):
        # Fire safety alert
        for _ in range(6):
            collector.record_safety_reject("max_order_usd")
        alerts1 = collector.check_thresholds()
        assert any(a["key"] == "safety_reject_rate" for a in alerts1)
        # Immediate re-check: cooldown active, no new alert
        alerts2 = collector.check_thresholds()
        assert not any(a["key"] == "safety_reject_rate" for a in alerts2)

    def test_recovery_clears_breach_state(self, collector):
        # 10 rejects → enter breach
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        collector.check_thresholds()
        assert collector._alert_states["maker_fill_rate"].breached_since is not None
        # Add 100 fresh fills → rate recovers
        for _ in range(100):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        collector.check_thresholds()
        assert collector._alert_states["maker_fill_rate"].breached_since is None


# ----------------------------------------------------------------------
# Health output shape
# ----------------------------------------------------------------------


class TestHealthOutput:
    def test_health_shape(self, collector):
        h = collector.health()
        assert h["instance_name"] == "test"
        assert "metrics" in h
        assert "maker_fill_rate" in h["metrics"]
        assert "latency_p95_ms" in h["metrics"]
        assert "safety_reject_per_hour" in h["metrics"]
        assert "counters" in h
        assert h["counters"]["n_maker_placed"] == 0

    def test_health_reflects_recorded_activity(self, collector):
        for _ in range(5):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=100)
        collector.record_safety_reject("max_order_usd")
        h = collector.health()
        assert h["counters"]["n_maker_placed"] == 5
        assert h["counters"]["n_maker_filled"] == 5
        assert h["counters"]["n_safety_reject"] == 1
        assert h["metrics"]["maker_fill_rate"]["value"] == 1.0
        assert h["metrics"]["safety_reject_per_hour"]["value"] == 1

    def test_status_ok_when_healthy(self, collector):
        # 10 fast fills + 0 safety rejects
        for _ in range(10):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        h = collector.health()
        assert h["metrics"]["maker_fill_rate"]["status"] == "ok"
        assert h["metrics"]["latency_p95_ms"]["status"] == "ok"
        assert h["metrics"]["safety_reject_per_hour"]["status"] == "ok"

    def test_status_breach_when_fill_rate_low(self, collector):
        # 8 reject + 2 filled → 0.2 < 0.90
        for _ in range(8):
            collector.record_maker_outcome("spot", "BUY", "rejected_post_only")
        for _ in range(2):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        h = collector.health()
        assert h["metrics"]["maker_fill_rate"]["status"] == "breach"


# ----------------------------------------------------------------------
# Unwind recording
# ----------------------------------------------------------------------


class TestAlertWatcher:
    @pytest.mark.asyncio
    async def test_watcher_fires_alert_via_telegram(self, monkeypatch):
        """watcher 检测到 breach 后调 notify_system."""
        import asyncio

        from app.strategies.dgr_btc import live_metrics as lm

        captured: list[str] = []

        def fake_notify(text: str) -> None:
            captured.append(text)

        # patch the import inside the watcher coroutine
        from app.notifications import telegram as _tg
        monkeypatch.setattr(_tg, "notify_system", fake_notify)

        collector = LiveMetricsCollector(instance_name="watcher_test")
        register_collector("watcher_test", collector)

        # 制造 safety breach: 6 rejects in 1h, immediate fire
        for _ in range(6):
            collector.record_safety_reject("max_order_usd $999 > $50")

        task = asyncio.create_task(
            lm.run_alert_watcher(
                instance_name="watcher_test",
                poll_interval_sec=0.1,
                telegram_enabled=True,
            )
        )
        try:
            # Wait one poll cycle + buffer
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # At least 1 alert message captured (safety reject)
        assert any("safety guard" in m for m in captured)

    @pytest.mark.asyncio
    async def test_watcher_no_telegram_when_disabled(self, monkeypatch):
        """telegram_enabled=False → 只 log, 不发推送."""
        import asyncio

        from app.strategies.dgr_btc import live_metrics as lm

        captured: list[str] = []
        from app.notifications import telegram as _tg
        monkeypatch.setattr(_tg, "notify_system", lambda t: captured.append(t))

        collector = LiveMetricsCollector(instance_name="silent_test")
        register_collector("silent_test", collector)
        for _ in range(6):
            collector.record_safety_reject("max_order_usd")

        task = asyncio.create_task(
            lm.run_alert_watcher(
                instance_name="silent_test",
                poll_interval_sec=0.1,
                telegram_enabled=False,
            )
        )
        try:
            await asyncio.sleep(0.3)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert captured == []


class TestUnwindRecording:
    def test_unwind_does_not_count_toward_fill_rate(self, collector):
        # 5 maker fills + 5 unwinds
        for _ in range(5):
            collector.record_maker_outcome("spot", "BUY", "filled", latency_ms=50)
        for _ in range(5):
            collector.record_unwind_outcome("spot", "SELL", latency_ms=20)
        h = collector.health()
        assert h["counters"]["n_unwind"] == 5
        assert h["counters"]["n_maker_placed"] == 5  # unwind 不增 maker placed
        # fill rate 应仍是 1.0 (基于 maker only)
        assert h["metrics"]["maker_fill_rate"]["value"] == 1.0
