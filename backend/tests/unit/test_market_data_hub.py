"""Market Data Hub 单元测试 — 缓存读写 + staleness。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.market_data_hub import (
    FundingRateEntry,
    MarketDataHub,
    TickerEntry,
)
from app.exchanges.models import FundingRate, InstrumentType, Symbol


def _ticker(bid=100.0, ask=100.5, last=100.2):
    return {"bid": bid, "ask": ask, "last": last}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_funding_rate(symbol_str: str = "BTC/USDT", rate: str = "0.0001"):
    base, _, quote = symbol_str.partition("/")
    return FundingRate(
        symbol=Symbol(base, quote or "USDT"),
        exchange="binance",
        rate=Decimal(rate),
        next_funding_time=1_700_000_000_000,
        funding_interval_hours=8,
    )


# ---------------------------------------------------------------------------
# Entry helpers
# ---------------------------------------------------------------------------


class TestEntries:
    def test_ticker_entry_decimals(self):
        e = TickerEntry(raw=_ticker(), fetched_at=_now())
        assert e.bid == Decimal("100.0")
        assert e.ask == Decimal("100.5")
        assert e.last == Decimal("100.2")

    def test_ticker_entry_missing_fields_none(self):
        e = TickerEntry(raw={}, fetched_at=_now())
        assert e.bid is None
        assert e.ask is None
        assert e.last is None

    def test_ticker_is_stale_true(self):
        e = TickerEntry(raw={}, fetched_at=_now() - timedelta(seconds=120))
        assert e.is_stale(60) is True

    def test_ticker_is_stale_false(self):
        e = TickerEntry(raw={}, fetched_at=_now() - timedelta(seconds=10))
        assert e.is_stale(60) is False

    def test_funding_entry_stale(self):
        e = FundingRateEntry(rate=_make_funding_rate(), fetched_at=_now() - timedelta(seconds=200))
        assert e.is_stale(180) is True


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


class TestMarketDataHub:
    def test_get_ticker_unknown_exchange_returns_none(self):
        hub = MarketDataHub(adapters={})
        assert hub.get_ticker("foo", InstrumentType.PERPETUAL, Symbol("BTC", "USDT")) is None

    def test_health_empty_when_no_adapters(self):
        hub = MarketDataHub(adapters={})
        assert hub.health() == {}

    def test_health_per_exchange(self):
        hub = MarketDataHub(adapters={"binance": MagicMock(), "okx": MagicMock()})
        h = hub.health()
        assert "binance" in h and "okx" in h
        assert h["binance"]["ticker_count"] == 0
        assert h["binance"]["consecutive_failures"] == 0

    @pytest.mark.asyncio
    async def test_fetch_tickers_populates_cache(self):
        adapter = MagicMock()
        client = MagicMock()
        client.fetch_tickers = AsyncMock(return_value={
            "BTC/USDT": _ticker(bid=60000, ask=60001),
            "ETH/USDT": _ticker(bid=3000, ask=3001),
        })
        adapter._clients = {InstrumentType.SPOT: client}
        hub = MarketDataHub(adapters={"binance": adapter})
        await hub._fetch_tickers_once("binance")
        # cache 应有两个条目
        tickers = hub.get_tickers("binance", InstrumentType.SPOT)
        assert "BTC/USDT" in tickers
        assert tickers["BTC/USDT"].bid == Decimal("60000")
        assert hub._cache["binance"].consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_fetch_tickers_failure_increments_failures(self):
        adapter = MagicMock()
        client = MagicMock()
        client.fetch_tickers = AsyncMock(side_effect=RuntimeError("network"))
        adapter._clients = {InstrumentType.SPOT: client}
        hub = MarketDataHub(adapters={"okx": adapter})
        await hub._fetch_tickers_once("okx")
        assert hub._cache["okx"].consecutive_failures == 1

    def test_get_ticker_returns_none_when_stale(self):
        hub = MarketDataHub(adapters={"binance": MagicMock()})
        # 注入 ancient ticker
        hub._cache["binance"].tickers[(InstrumentType.PERPETUAL.value, "BTC/USDT")] = TickerEntry(
            raw=_ticker(), fetched_at=_now() - timedelta(seconds=600),
        )
        assert hub.get_ticker(
            "binance", InstrumentType.PERPETUAL, Symbol("BTC", "USDT"),
            max_age_seconds=60,
        ) is None

    def test_get_tickers_filters_stale(self):
        hub = MarketDataHub(adapters={"binance": MagicMock()})
        cache = hub._cache["binance"]
        # 1 fresh, 1 stale
        cache.tickers[(InstrumentType.SPOT.value, "BTC/USDT")] = TickerEntry(
            raw=_ticker(), fetched_at=_now(),
        )
        cache.tickers[(InstrumentType.SPOT.value, "OLD/USDT")] = TickerEntry(
            raw=_ticker(), fetched_at=_now() - timedelta(seconds=600),
        )
        out = hub.get_tickers("binance", InstrumentType.SPOT, max_age_seconds=60)
        assert "BTC/USDT" in out
        assert "OLD/USDT" not in out

    def test_get_funding_rate_basic(self):
        hub = MarketDataHub(adapters={"binance": MagicMock()})
        cache = hub._cache["binance"]
        cache.funding_rates["BTC/USDT"] = FundingRateEntry(
            rate=_make_funding_rate(), fetched_at=_now(),
        )
        e = hub.get_funding_rate("binance", Symbol("BTC", "USDT"))
        assert e is not None
        assert e.rate.rate == Decimal("0.0001")

    @pytest.mark.asyncio
    async def test_fetch_funding_skipped_when_no_method(self):
        adapter = MagicMock()
        client = MagicMock(spec=[])  # 无 fetch_funding_rates
        adapter._clients = {InstrumentType.PERPETUAL: client}
        hub = MarketDataHub(adapters={"binance": adapter})
        await hub._fetch_funding_once("binance")
        # 不崩；funding count 应仍 0
        assert hub._cache["binance"].last_funding_count == 0

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self):
        adapter = MagicMock()
        client = MagicMock()
        client.fetch_tickers = AsyncMock(return_value={})
        adapter._clients = {InstrumentType.SPOT: client}
        hub = MarketDataHub(
            adapters={"binance": adapter},
            ticker_interval_seconds=0.05,
            funding_interval_seconds=0.05,
        )
        await hub.start()
        assert hub.is_running
        await hub.stop()
        assert not hub.is_running


class TestFundingPerSymbolFallback:
    """Bybit linear 等不支持 fetch_funding_rates() bulk → fallback per-symbol。"""

    @pytest.mark.asyncio
    async def test_bulk_fail_triggers_per_symbol_fallback(self):
        adapter = MagicMock()
        perp_client = MagicMock()
        # bulk 抛错（模拟 Bybit linear）
        perp_client.fetch_funding_rates = AsyncMock(
            side_effect=Exception("does not support linear markets"),
        )
        # per-symbol 成功
        async def fetch_one(sym):
            return {"fundingRate": 0.0001, "fundingTimestamp": 1_700_000_000_000}
        perp_client.fetch_funding_rate = AsyncMock(side_effect=fetch_one)
        adapter._clients = {InstrumentType.PERPETUAL: perp_client}
        hub = MarketDataHub(adapters={"bybit": adapter})
        # 预填 perp ticker cache（为 fallback 提供 symbols）
        cache = hub._cache["bybit"]
        for sym in ["BTC/USDT", "ETH/USDT"]:
            cache.tickers[(InstrumentType.PERPETUAL.value, sym)] = TickerEntry(
                raw={"quoteVolume": 1_000_000}, fetched_at=_now(),
            )
        await hub._fetch_funding_once("bybit")
        # bulk 失败但 per-symbol 成功 → cache 应有 funding
        assert cache.last_funding_count > 0
        assert perp_client.fetch_funding_rate.await_count >= 1

    @pytest.mark.asyncio
    async def test_no_perp_tickers_no_fallback(self):
        adapter = MagicMock()
        perp_client = MagicMock()
        perp_client.fetch_funding_rates = AsyncMock(side_effect=Exception("nope"))
        perp_client.fetch_funding_rate = AsyncMock(return_value={"fundingRate": 0})
        adapter._clients = {InstrumentType.PERPETUAL: perp_client}
        hub = MarketDataHub(adapters={"bybit": adapter})
        # ticker cache 空 → fallback 也无来源
        await hub._fetch_funding_once("bybit")
        perp_client.fetch_funding_rate.assert_not_awaited()
