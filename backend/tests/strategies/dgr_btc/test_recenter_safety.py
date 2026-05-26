"""Tests for recenter_safety (Phase E.4)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.strategies.dgr_btc.recenter_safety import (
    CancelOutcome,
    RecenterCancelManager,
)


class FakeBroker:
    def __init__(
        self,
        open_orders: list[dict],
        cancel_fail_ids: set | None = None,
        fetch_timeout: bool = False,
        cancel_timeout_ids: set | None = None,
    ) -> None:
        self.open_orders = open_orders
        self.cancel_fail_ids = cancel_fail_ids or set()
        self.fetch_timeout = fetch_timeout
        self.cancel_timeout_ids = cancel_timeout_ids or set()
        self.cancel_attempts: dict[str, int] = {}

    async def fetch_open_orders(self) -> list[dict]:
        if self.fetch_timeout:
            await asyncio.sleep(100)  # 用 cancel_timeout 截断
        return list(self.open_orders)

    async def cancel_order(self, order_id: str) -> None:
        self.cancel_attempts[order_id] = self.cancel_attempts.get(order_id, 0) + 1
        if order_id in self.cancel_timeout_ids:
            await asyncio.sleep(100)
        if order_id in self.cancel_fail_ids:
            raise RuntimeError(f"binance cancel {order_id} reject")


# ============================================================
# PAPER 模式 — skip
# ============================================================

class TestPaperModeSkip:
    @pytest.mark.asyncio
    async def test_paper_mode_skipped(self) -> None:
        mgr = RecenterCancelManager(live_mode=False)
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.SKIPPED_PAPER_MODE
        assert mgr.is_halted() is False


# ============================================================
# Happy path
# ============================================================

class TestAllCanceled:
    @pytest.mark.asyncio
    async def test_no_open_orders_succeeds(self) -> None:
        broker = FakeBroker(open_orders=[])
        mgr = RecenterCancelManager(broker_adapter=broker, live_mode=True)
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.ALL_CANCELED
        assert report.n_orders_total == 0
        assert mgr.is_halted() is False

    @pytest.mark.asyncio
    async def test_all_orders_canceled(self) -> None:
        broker = FakeBroker(open_orders=[
            {"id": "o1"}, {"id": "o2"}, {"id": "o3"}
        ])
        mgr = RecenterCancelManager(broker_adapter=broker, live_mode=True)
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.ALL_CANCELED
        assert report.n_canceled == 3
        assert report.n_failed == 0
        assert mgr.is_halted() is False
        assert mgr.n_successful_cancels == 3


# ============================================================
# Cancel failure with retry
# ============================================================

class TestCancelRetry:
    @pytest.mark.asyncio
    async def test_one_order_fails_all_attempts_triggers_halt(self) -> None:
        broker = FakeBroker(
            open_orders=[{"id": "o1"}, {"id": "o2"}],
            cancel_fail_ids={"o2"},
        )
        mgr = RecenterCancelManager(
            broker_adapter=broker, live_mode=True,
            max_retries=3, retry_delay_ms=0,
        )
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.PARTIAL_CANCELED_HALT
        assert report.n_canceled == 1
        assert report.n_failed == 1
        assert "o2" in report.failed_order_ids
        assert mgr.is_halted() is True
        assert mgr.halt_reason is not None
        assert broker.cancel_attempts["o2"] == 3
        assert broker.cancel_attempts["o1"] == 1

    @pytest.mark.asyncio
    async def test_all_orders_fail_full_halt(self) -> None:
        broker = FakeBroker(
            open_orders=[{"id": "o1"}, {"id": "o2"}],
            cancel_fail_ids={"o1", "o2"},
        )
        mgr = RecenterCancelManager(
            broker_adapter=broker, live_mode=True,
            max_retries=2, retry_delay_ms=0,
        )
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.NONE_CANCELED_HALT
        assert report.n_canceled == 0
        assert report.n_failed == 2
        assert mgr.is_halted() is True


# ============================================================
# Timeout
# ============================================================

class TestTimeout:
    @pytest.mark.asyncio
    async def test_fetch_timeout_triggers_halt(self) -> None:
        broker = FakeBroker(open_orders=[], fetch_timeout=True)
        mgr = RecenterCancelManager(
            broker_adapter=broker, live_mode=True,
            cancel_timeout_sec=0.1,
        )
        report = await mgr.cancel_open_orders_for_recenter()
        assert report.outcome == CancelOutcome.BROKER_TIMEOUT_HALT
        assert mgr.is_halted() is True


# ============================================================
# Halt callback
# ============================================================

class TestHaltCallback:
    @pytest.mark.asyncio
    async def test_halt_callback_invoked(self) -> None:
        called_with: list[str] = []
        async def cb(reason: str) -> None:
            called_with.append(reason)

        broker = FakeBroker(
            open_orders=[{"id": "o1"}],
            cancel_fail_ids={"o1"},
        )
        mgr = RecenterCancelManager(
            broker_adapter=broker, live_mode=True,
            max_retries=1, retry_delay_ms=0,
            halt_callback=cb,
        )
        await mgr.cancel_open_orders_for_recenter()
        assert mgr.is_halted() is True
        assert len(called_with) == 1
        assert "failed" in called_with[0]


# ============================================================
# Resume
# ============================================================

class TestResume:
    @pytest.mark.asyncio
    async def test_resume_clears_halt(self) -> None:
        broker = FakeBroker(
            open_orders=[{"id": "o1"}],
            cancel_fail_ids={"o1"},
        )
        mgr = RecenterCancelManager(
            broker_adapter=broker, live_mode=True,
            max_retries=1, retry_delay_ms=0,
        )
        await mgr.cancel_open_orders_for_recenter()
        assert mgr.is_halted() is True

        mgr.resume()
        assert mgr.is_halted() is False
        assert mgr.halt_reason is None
