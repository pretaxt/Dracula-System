"""单元测试 — safety/polling_liquidation_watcher.py"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.models import Side, Symbol
from app.safety.polling_liquidation_watcher import PollingLiquidationWatcher


BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")


def _fake_position(symbol: Symbol, side: Side, size: str = "0.01") -> MagicMock:
    p = MagicMock()
    p.symbol = symbol
    p.side = side
    p.size = Decimal(size)
    return p


def _make_adapter(positions: list | Exception | None = None) -> MagicMock:
    a = MagicMock()
    if isinstance(positions, Exception):
        a.fetch_positions = AsyncMock(side_effect=positions)
    else:
        a.fetch_positions = AsyncMock(return_value=positions or [])
    return a


def _make_watcher(adapter, expected: set, callback=None, interval: float = 0.05):
    cb = callback or AsyncMock()
    return PollingLiquidationWatcher(
        adapter=adapter,
        exchange_name="okx",
        on_liquidation=cb,
        get_expected_positions=lambda: expected,
        interval_seconds=interval,
    ), cb


# ---------------------------------------------------------------------------
# _tick — 核心逻辑
# ---------------------------------------------------------------------------


class TestTickLogic:
    @pytest.mark.asyncio
    async def test_no_expected_no_callback(self):
        """期望集为空 → 不 fetch 不触发."""
        adapter = _make_adapter([_fake_position(BTC, Side.SELL)])
        w, cb = _make_watcher(adapter, expected=set())
        await w._tick()
        cb.assert_not_called()
        adapter.fetch_positions.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_tick_no_alarm_grace_period(self):
        """首次 tick：仓位刚开 previously_seen 为空 → 不触发即便交易所没显示。"""
        adapter = _make_adapter([])
        expected = {("BTC/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_position_seen_then_lost_triggers(self):
        """tick1 见到 → 基线建立；tick2 没了 → 触发 callback."""
        adapter = _make_adapter([_fake_position(BTC, Side.SELL)])
        expected = {("BTC/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        cb.assert_not_called()

        adapter.fetch_positions = AsyncMock(return_value=[])
        await w._tick()
        cb.assert_awaited_once()
        symbol_arg, raw_arg = cb.await_args.args
        assert symbol_arg == BTC
        assert raw_arg["source"] == "polling_watcher"
        assert raw_arg["exchange"] == "okx"
        assert raw_arg["side"] == "sell"

    @pytest.mark.asyncio
    async def test_position_still_present_no_alarm(self):
        adapter = _make_adapter([_fake_position(BTC, Side.SELL)])
        expected = {("BTC/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        await w._tick()
        cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_size_position_treated_as_missing(self):
        adapter = _make_adapter([_fake_position(BTC, Side.SELL, size="0.01")])
        expected = {("BTC/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        adapter.fetch_positions = AsyncMock(
            return_value=[_fake_position(BTC, Side.SELL, size="0")]
        )
        await w._tick()
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fetch_failure_does_not_crash(self):
        adapter = _make_adapter(RuntimeError("rate limited"))
        expected = {("BTC/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        cb.assert_not_called()
        assert w._previously_seen == set()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self):
        adapter = _make_adapter([_fake_position(BTC, Side.SELL)])
        cb = AsyncMock(side_effect=RuntimeError("close failed"))
        expected = {("BTC/USDT", "sell")}
        w, _ = _make_watcher(adapter, expected, callback=cb)
        await w._tick()
        adapter.fetch_positions = AsyncMock(return_value=[])
        await w._tick()  # 不应抛
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_positions_independent_tracking(self):
        adapter = _make_adapter([
            _fake_position(BTC, Side.SELL),
            _fake_position(ETH, Side.SELL),
        ])
        expected = {("BTC/USDT", "sell"), ("ETH/USDT", "sell")}
        w, cb = _make_watcher(adapter, expected)
        await w._tick()
        adapter.fetch_positions = AsyncMock(
            return_value=[_fake_position(BTC, Side.SELL)]
        )
        await w._tick()
        cb.assert_awaited_once()
        symbol_arg, _ = cb.await_args.args
        assert symbol_arg == ETH


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        adapter = _make_adapter([])
        w, _ = _make_watcher(adapter, expected=set())
        await w.start()
        assert w._task is not None and not w._task.done()
        await w.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        adapter = _make_adapter([])
        w, _ = _make_watcher(adapter, expected=set())
        await w.start()
        first = w._task
        await w.start()
        assert w._task is first
        await w.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        adapter = _make_adapter([])
        w, _ = _make_watcher(adapter, expected=set())
        await w.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_running(self):
        adapter = _make_adapter([])
        w, _ = _make_watcher(adapter, expected=set(), interval=10.0)
        await w.start()
        task = w._task
        await w.stop()
        assert task.cancelled() or task.done()
        assert w._running is False
