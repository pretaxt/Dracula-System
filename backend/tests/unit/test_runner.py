"""单元测试 — strategies/funding_rate/runner.py

使用 Mock 替换 DB session、Redis publish 和 FundingRateScanner，
不需要真实数据库或 Redis 连接。
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exchanges.models import FundingRate, OrderBook, Symbol
from app.strategies.funding_rate.runner import (
    OPPORTUNITIES_CHANNEL,
    FundingRateRunner,
)
from app.strategies.funding_rate.scanner import (
    FundingRateOpportunity,
    ScannerConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BTC = Symbol("BTC", "USDT")

_PRICE = Decimal("60000")
_QTY = Decimal("1")


def _make_orderbook() -> OrderBook:
    return OrderBook(
        symbol=BTC,
        bids=[(_PRICE - Decimal("1"), _QTY)],
        asks=[(_PRICE, _QTY)],
        timestamp=1_700_000_000_000,
    )


def _make_opportunity(rate: str = "0.0001") -> FundingRateOpportunity:
    fr = FundingRate(
        symbol=BTC,
        exchange="binance",
        rate=Decimal(rate),
        next_funding_time=1_700_064_000_000,
        funding_interval_hours=8,
    )
    return FundingRateOpportunity(
        exchange="binance",
        symbol=BTC,
        funding_rate=fr,
        spot_orderbook=_make_orderbook(),
        perp_orderbook=_make_orderbook(),
    )


def _make_runner(scan_result: list[FundingRateOpportunity]) -> FundingRateRunner:
    """Runner whose scanner is replaced with a mock returning ``scan_result``."""
    runner = FundingRateRunner(
        adapters={},
        symbols=[BTC],
        config=ScannerConfig(
            min_apr_pct=Decimal("10"),
            min_orderbook_depth_usd=Decimal("10000"),
            max_spread_bps=Decimal("50"),
            lookback_periods=3,
            min_positive_periods=2,
        ),
        scan_interval_seconds=0.0,
    )
    runner._scanner.scan = AsyncMock(return_value=scan_result)  # type: ignore[method-assign]
    return runner


@asynccontextmanager
async def _fake_session() -> AsyncGenerator[MagicMock, None]:
    session = MagicMock()
    session.merge = AsyncMock()
    yield session


# ---------------------------------------------------------------------------
# run_once — happy path
# ---------------------------------------------------------------------------


class TestRunOnce:
    @pytest.mark.asyncio
    async def test_returns_opportunities(self):
        runner = _make_runner([_make_opportunity()])
        with (
            patch("app.strategies.funding_rate.runner.publish", new=AsyncMock(return_value=1)),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            results = await runner.run_once()

        assert len(results) == 1
        assert results[0].exchange == "binance"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_opportunities(self):
        runner = _make_runner([])
        with (
            patch("app.strategies.funding_rate.runner.publish", new=AsyncMock(return_value=0)),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            results = await runner.run_once()

        assert results == []

    @pytest.mark.asyncio
    async def test_persist_merges_correct_record_fields(self):
        opp = _make_opportunity("0.0002")
        runner = _make_runner([opp])

        merged_records: list = []

        @asynccontextmanager
        async def capturing_session() -> AsyncGenerator[MagicMock, None]:
            session = MagicMock()

            async def capture(record):
                merged_records.append(record)

            session.merge = capture
            yield session

        with (
            patch("app.strategies.funding_rate.runner.publish", new=AsyncMock(return_value=1)),
            patch("app.core.database.get_session", new=capturing_session),
        ):
            await runner.run_once()

        assert len(merged_records) == 1
        rec = merged_records[0]
        assert rec.exchange == "binance"
        assert rec.symbol == "BTC/USDT"
        assert rec.instrument_type == "PERPETUAL"
        assert rec.funding_rate == Decimal("0.0002")

    @pytest.mark.asyncio
    async def test_publish_uses_correct_channel(self):
        runner = _make_runner([_make_opportunity()])
        publish_mock = AsyncMock(return_value=1)

        with (
            patch("app.strategies.funding_rate.runner.publish", new=publish_mock),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            await runner.run_once()

        publish_mock.assert_called_once()
        assert publish_mock.call_args[0][0] == OPPORTUNITIES_CHANNEL

    @pytest.mark.asyncio
    async def test_publish_payload_structure(self):
        runner = _make_runner([_make_opportunity()])
        publish_mock = AsyncMock(return_value=1)

        with (
            patch("app.strategies.funding_rate.runner.publish", new=publish_mock),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            await runner.run_once()

        payload = json.loads(publish_mock.call_args[0][1])
        assert "scanned_at" in payload
        assert "opportunities" in payload
        assert len(payload["opportunities"]) == 1
        opp_data = payload["opportunities"][0]
        assert opp_data["exchange"] == "binance"
        assert opp_data["symbol"] == "BTC/USDT"
        assert "apr_pct" in opp_data
        assert "funding_rate" in opp_data

    @pytest.mark.asyncio
    async def test_no_persist_or_publish_when_empty(self):
        runner = _make_runner([])
        publish_mock = AsyncMock(return_value=0)

        with (
            patch("app.strategies.funding_rate.runner.publish", new=publish_mock),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            await runner.run_once()

        publish_mock.assert_not_called()


# ---------------------------------------------------------------------------
# run_once — error resilience
# ---------------------------------------------------------------------------


class TestRunOnceErrorResilience:
    @pytest.mark.asyncio
    async def test_scanner_error_returns_empty(self):
        runner = _make_runner([])
        runner._scanner.scan = AsyncMock(side_effect=RuntimeError("exchange down"))  # type: ignore[method-assign]

        results = await runner.run_once()
        assert results == []

    @pytest.mark.asyncio
    async def test_persist_failure_still_returns_opportunities(self):
        runner = _make_runner([_make_opportunity()])

        @asynccontextmanager
        async def failing_session() -> AsyncGenerator[MagicMock, None]:
            raise RuntimeError("db down")
            yield  # pragma: no cover

        with (
            patch("app.strategies.funding_rate.runner.publish", new=AsyncMock(return_value=0)),
            patch("app.core.database.get_session", new=failing_session),
        ):
            results = await runner.run_once()

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_publish_failure_still_returns_opportunities(self):
        runner = _make_runner([_make_opportunity()])

        with (
            patch(
                "app.strategies.funding_rate.runner.publish",
                new=AsyncMock(side_effect=ConnectionError("redis down")),
            ),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            results = await runner.run_once()

        assert len(results) == 1


# ---------------------------------------------------------------------------
# run_forever — loop control
# ---------------------------------------------------------------------------


class TestRunForever:
    @pytest.mark.asyncio
    async def test_stop_exits_after_current_tick(self):
        """stop() causes run_forever() to exit after completing the current tick."""
        runner = _make_runner([])
        tick_count = 0
        original_tick = runner._tick

        async def counting_tick():
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                await runner.stop()
            return await original_tick()

        runner._tick = counting_tick  # type: ignore[method-assign]

        with (
            patch("app.strategies.funding_rate.runner.publish", new=AsyncMock(return_value=0)),
            patch("app.core.database.get_session", new=_fake_session),
        ):
            await runner.run_forever()

        assert tick_count == 2


# ---------------------------------------------------------------------------
# v0.4.3 — schedule cache + window gate（取消 8h 假设，每标的真实周期）
# ---------------------------------------------------------------------------


class TestScheduleCacheAndWindowGate:
    """`window_only_minutes` gate + schedule cache GC + auto-advance。"""

    def _runner(self, window_min: float) -> FundingRateRunner:
        r = FundingRateRunner(
            adapters={},
            symbols=[BTC],
            config=ScannerConfig(min_apr_pct=Decimal("10")),
            scan_interval_seconds=0.0,
            window_only_minutes=window_min,
        )
        return r

    def test_window_disabled_always_in_window(self):
        r = self._runner(window_min=0)
        # 即使 cache 空 + window=0 → 永远 True（旧行为兼容）
        assert r._is_in_funding_window(datetime.now(UTC)) is True

    def test_cold_start_empty_cache_returns_true(self):
        r = self._runner(window_min=15)
        # cache 空 = 冷启动 → 必扫一次填 cache
        assert r._is_in_funding_window(datetime.now(UTC)) is True

    def test_within_window_returns_true(self):
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 7, 50, tzinfo=UTC)
        next_funding = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)  # 10min later
        r._schedule_cache[("binance", "BTC/USDT")] = {
            "next_funding_ms": int(next_funding.timestamp() * 1000),
            "interval_hours": 8,
            "updated_at": now,
        }
        assert r._is_in_funding_window(now) is True

    def test_outside_window_returns_false(self):
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 12, 30, tzinfo=UTC)
        next_funding = datetime(2026, 5, 10, 16, 0, tzinfo=UTC)  # 3h30min later
        r._schedule_cache[("binance", "BTC/USDT")] = {
            "next_funding_ms": int(next_funding.timestamp() * 1000),
            "interval_hours": 8,
            "updated_at": now,
        }
        assert r._is_in_funding_window(now) is False

    def test_mixed_cadence_one_in_window_triggers_scan(self):
        """8h 标的距 4h，1h 标的距 5min → 1h 触发扫描。"""
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 12, 30, tzinfo=UTC)
        r._schedule_cache[("binance", "BTC/USDT")] = {
            "next_funding_ms": int(datetime(2026, 5, 10, 16, 0, tzinfo=UTC).timestamp() * 1000),
            "interval_hours": 8,
            "updated_at": now,
        }
        r._schedule_cache[("okx", "ALT/USDT")] = {
            "next_funding_ms": int(datetime(2026, 5, 10, 12, 35, tzinfo=UTC).timestamp() * 1000),
            "interval_hours": 1,
            "updated_at": now,
        }
        assert r._is_in_funding_window(now) is True

    def test_advance_schedule_past_now(self):
        """next_funding 已过 → += interval。"""
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
        # next_funding 设在 now 前 1h，interval=8h → 应 advance 到 now + 7h
        past = datetime(2026, 5, 10, 8, 0, tzinfo=UTC)
        r._schedule_cache[("binance", "BTC/USDT")] = {
            "next_funding_ms": int(past.timestamp() * 1000),
            "interval_hours": 8,
            "updated_at": now,
        }
        r._advance_schedule(now)
        new_ts = r._schedule_cache[("binance", "BTC/USDT")]["next_funding_ms"]
        expected = datetime(2026, 5, 10, 16, 0, tzinfo=UTC)  # past + 8h
        assert new_ts == int(expected.timestamp() * 1000)

    def test_advance_schedule_handles_multiple_intervals(self):
        """next_funding 远古（>1 个 interval 前）→ while loop 多次 advance。"""
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 9, 0, tzinfo=UTC)
        # 1h 标的，next_funding 设在 5h 前 → 应 advance 5 次到 4:00 + 5h = 9h（仍 ≤ now=9）→ 再 advance 一次到 10:00
        r._schedule_cache[("binance", "ALT/USDT")] = {
            "next_funding_ms": int(datetime(2026, 5, 10, 4, 0, tzinfo=UTC).timestamp() * 1000),
            "interval_hours": 1,
            "updated_at": now,
        }
        r._advance_schedule(now)
        new_ts = r._schedule_cache[("binance", "ALT/USDT")]["next_funding_ms"]
        # while loop: 4 → 5 → 6 → 7 → 8 → 9 → 10 (>9, exits)
        expected = datetime(2026, 5, 10, 10, 0, tzinfo=UTC)
        assert new_ts == int(expected.timestamp() * 1000)

    def test_gc_removes_stale_entries(self):
        """24h 没更新的条目被 GC 掉。"""
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
        r._schedule_cache[("binance", "FRESH/USDT")] = {
            "next_funding_ms": 0,
            "interval_hours": 8,
            "updated_at": datetime(2026, 5, 10, 11, 0, tzinfo=UTC),  # 1h 前
        }
        r._schedule_cache[("binance", "STALE/USDT")] = {
            "next_funding_ms": 0,
            "interval_hours": 8,
            "updated_at": datetime(2026, 5, 9, 10, 0, tzinfo=UTC),  # 26h 前
        }
        r._gc_stale_schedule(now)
        assert ("binance", "FRESH/USDT") in r._schedule_cache
        assert ("binance", "STALE/USDT") not in r._schedule_cache

    def test_update_schedule_writes_from_opps(self):
        r = self._runner(window_min=15)
        now = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
        opp = _make_opportunity()
        r._update_schedule([opp], now)
        key = ("binance", "BTC/USDT")
        assert key in r._schedule_cache
        assert r._schedule_cache[key]["next_funding_ms"] == 1_700_064_000_000
        assert r._schedule_cache[key]["interval_hours"] == 8
        assert r._schedule_cache[key]["updated_at"] == now
