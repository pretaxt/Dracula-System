"""单元测试 — safety/liquidation_watcher.py

只测纯 Python 行为（事件解析 / start-stop 生命周期）；
WS 主循环和 listenKey REST 调用属于 IO 层，
留给 Week 5 的实盘 testnet 集成测试。
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.exchanges.models import Symbol
from app.safety.liquidation_watcher import LiquidationWatcher


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_watcher(callback: Any | None = None) -> LiquidationWatcher:
    cb = callback or AsyncMock(return_value=None)
    return LiquidationWatcher(
        api_key="test-key",
        api_secret="test-secret",
        on_liquidation=cb,
    )


def _liq_event(
    symbol_str: str = "BTCUSDT",
    ot: str = "LIQUIDATION",
    side: str = "BUY",
    qty: str = "0.01",
    avg_price: str = "60000",
    status: str = "FILLED",
) -> dict:
    return {
        "e": "ORDER_TRADE_UPDATE",
        "o": {
            "s": symbol_str,
            "ot": ot,
            "S": side,
            "q": qty,
            "ap": avg_price,
            "X": status,
        },
    }


# ---------------------------------------------------------------------------
# _parse_usdt_symbol
# ---------------------------------------------------------------------------


class TestParseUsdtSymbol:
    def test_btcusdt_returns_symbol(self):
        result = LiquidationWatcher._parse_usdt_symbol("BTCUSDT")
        assert result == Symbol("BTC", "USDT")

    def test_ethusdt_returns_symbol(self):
        assert LiquidationWatcher._parse_usdt_symbol("ETHUSDT") == Symbol("ETH", "USDT")

    def test_long_base_works(self):
        assert LiquidationWatcher._parse_usdt_symbol("DOGEUSDT") == Symbol("DOGE", "USDT")

    def test_busd_quote_returns_none(self):
        """非 USDT 永续不在范围内（系统只跑 USDT-margined）。"""
        assert LiquidationWatcher._parse_usdt_symbol("BTCBUSD") is None

    def test_too_short_returns_none(self):
        """长度 ≤ 4 时拆出空 base。"""
        assert LiquidationWatcher._parse_usdt_symbol("USDT") is None

    def test_no_quote_suffix_returns_none(self):
        assert LiquidationWatcher._parse_usdt_symbol("BTC") is None

    def test_non_alnum_base_returns_none(self):
        """带 dash/dot 等非字母数字字符的 base 拒绝（防意外解析）。"""
        assert LiquidationWatcher._parse_usdt_symbol("BTC-USDT") is None

    def test_empty_string_returns_none(self):
        assert LiquidationWatcher._parse_usdt_symbol("") is None


# ---------------------------------------------------------------------------
# _handle_event — 事件分发逻辑
# ---------------------------------------------------------------------------


class TestHandleEventFilters:
    @pytest.mark.asyncio
    async def test_ignores_non_order_trade_update(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event({"e": "ACCOUNT_UPDATE", "o": {}})
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_event_without_e_field(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event({"o": {"ot": "LIQUIDATION", "s": "BTCUSDT"}})
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_normal_market_order(self):
        """正常市价/限价单 ot=MARKET 或 LIMIT，不应触发。"""
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(ot="MARKET"))
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_adl_order(self):
        """ADL（自动减仓）也不应触发——只 LIQUIDATION 触发紧急路径。"""
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(ot="ADL"))
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_missing_symbol(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        evt = _liq_event()
        del evt["o"]["s"]
        await watcher._handle_event(evt)
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_non_string_symbol(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        evt = _liq_event()
        evt["o"]["s"] = 12345
        await watcher._handle_event(evt)
        cb.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ignores_non_usdt_symbol(self):
        """BUSD/USDC 永续被监听到时也不应触发（系统只跑 USDT-margined）。"""
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(symbol_str="BTCBUSD"))
        cb.assert_not_awaited()


# ---------------------------------------------------------------------------
# _handle_event — 触发回调
# ---------------------------------------------------------------------------


class TestHandleEventCallback:
    @pytest.mark.asyncio
    async def test_liquidation_invokes_callback_with_symbol(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(symbol_str="BTCUSDT"))
        cb.assert_awaited_once()
        symbol_arg, raw_arg = cb.await_args.args
        assert symbol_arg == Symbol("BTC", "USDT")

    @pytest.mark.asyncio
    async def test_liquidation_passes_raw_order_dict(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(qty="2.5", avg_price="60123"))
        _, raw_arg = cb.await_args.args
        assert raw_arg["q"] == "2.5"
        assert raw_arg["ap"] == "60123"
        assert raw_arg["ot"] == "LIQUIDATION"

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_propagate(self):
        """回调内部抛错时 watcher 主循环应不崩——只记日志。"""
        cb = AsyncMock(side_effect=RuntimeError("close spot failed"))
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event())
        cb.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_liquidations_invoke_callback_each(self):
        cb = AsyncMock()
        watcher = _make_watcher(cb)
        await watcher._handle_event(_liq_event(symbol_str="BTCUSDT"))
        await watcher._handle_event(_liq_event(symbol_str="ETHUSDT"))
        assert cb.await_count == 2


# ---------------------------------------------------------------------------
# 生命周期 — start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_without_start_does_not_raise(self):
        watcher = _make_watcher()
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self, monkeypatch):
        watcher = _make_watcher()
        ran = asyncio.Event()

        async def fake_run() -> None:
            ran.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        monkeypatch.setattr(watcher, "_run", fake_run)
        await watcher.start()
        await asyncio.wait_for(ran.wait(), timeout=1.0)
        assert watcher._task is not None
        assert not watcher._task.done()
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, monkeypatch):
        """重复 start() 不应再起一个 task。"""
        watcher = _make_watcher()

        async def fake_run() -> None:
            await asyncio.sleep(3600)

        monkeypatch.setattr(watcher, "_run", fake_run)
        await watcher.start()
        first_task = watcher._task
        await watcher.start()
        assert watcher._task is first_task
        await watcher.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self, monkeypatch):
        watcher = _make_watcher()

        async def fake_run() -> None:
            await asyncio.sleep(3600)

        monkeypatch.setattr(watcher, "_run", fake_run)
        await watcher.start()
        task = watcher._task
        await watcher.stop()
        assert task is not None
        assert task.cancelled() or task.done()
        assert watcher._running is False

    @pytest.mark.asyncio
    async def test_stop_swallows_run_exceptions(self, monkeypatch):
        """_run 抛错时 stop() 仍然要干净退出。"""
        watcher = _make_watcher()

        async def crashing_run() -> None:
            raise RuntimeError("ws crashed")

        monkeypatch.setattr(watcher, "_run", crashing_run)
        await watcher.start()
        await asyncio.sleep(0.05)
        await watcher.stop()
        # 显式取出已完成 task 的异常，避免 "Task exception was never retrieved" 警告
        if watcher._task and watcher._task.done() and not watcher._task.cancelled():
            with pytest.raises(RuntimeError, match="ws crashed"):
                watcher._task.result()
