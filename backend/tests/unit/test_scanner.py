"""单元测试 — strategies/funding_rate/scanner.py

使用 Mock 替换 ExchangeAdapter,不需要真实 API 连接。
"""
from __future__ import annotations

from decimal import Decimal
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exchanges.models import (
    FundingRate,
    InstrumentType,
    OrderBook,
    Symbol,
)
from app.strategies.funding_rate.scanner import (
    FundingRateScanner,
    ScannerConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BTC = Symbol("BTC", "USDT")
ETH = Symbol("ETH", "USDT")


def _make_funding(symbol: Symbol, rate: str, exchange: str = "binance") -> FundingRate:
    return FundingRate(
        symbol=symbol,
        exchange=exchange,
        rate=Decimal(rate),
        next_funding_time=1700064000000,
        funding_interval_hours=8,
    )


def _make_orderbook(symbol: Symbol, depth_usd: float = 100_000.0) -> OrderBook:
    """深度约为 depth_usd 的简化单档订单簿"""
    price = Decimal("60000")
    qty = Decimal(str(depth_usd)) / price
    return OrderBook(
        symbol=symbol,
        bids=[(price - Decimal("1"), qty)],
        asks=[(price, qty)],
        timestamp=1700000000000,
    )


def _make_adapter(
    funding_rate: str = "0.0001",
    depth_usd: float = 100_000.0,
    history: List[str] | None = None,
) -> MagicMock:
    adapter = MagicMock()
    adapter.supported_instruments = [InstrumentType.SPOT, InstrumentType.PERPETUAL]
    adapter.fetch_funding_rate = AsyncMock(
        return_value=_make_funding(BTC, funding_rate)
    )
    adapter.fetch_orderbook = AsyncMock(
        return_value=_make_orderbook(BTC, depth_usd)
    )
    if history is not None:
        adapter.fetch_funding_rate_history = AsyncMock(
            return_value=[_make_funding(BTC, r) for r in history]
        )
    return adapter


def _default_config(**overrides) -> ScannerConfig:
    base = dict(
        min_apr_pct=Decimal("10"),
        min_orderbook_depth_usd=Decimal("10000"),
        max_spread_bps=Decimal("50"),   # wide — spread never blocks in unit tests
        lookback_periods=3,
        min_positive_periods=2,
    )
    base.update(overrides)
    return ScannerConfig(**base)


# ---------------------------------------------------------------------------
# ScannerConfig
# ---------------------------------------------------------------------------


class TestScannerConfig:
    def test_from_yaml_defaults(self):
        cfg = ScannerConfig.from_yaml({})
        assert cfg.min_apr_pct == Decimal("10.0")
        assert cfg.min_orderbook_depth_usd == Decimal("10000")
        assert cfg.lookback_periods == 9

    def test_from_yaml_override(self):
        cfg = ScannerConfig.from_yaml({
            "entry": {
                "min_apr_pct": 15.0,
                "min_orderbook_depth_usd": 50000,
            },
            "scanning": {
                "max_spread_bps": 5,
                "funding_history_check": {
                    "lookback_periods": 12,
                    "min_positive_periods": 10,
                },
            },
        })
        assert cfg.min_apr_pct == Decimal("15.0")
        assert cfg.min_orderbook_depth_usd == Decimal("50000")
        assert cfg.max_spread_bps == Decimal("5")
        assert cfg.lookback_periods == 12
        assert cfg.min_positive_periods == 10


# ---------------------------------------------------------------------------
# FundingRateScanner
# ---------------------------------------------------------------------------


class TestFundingRateScanner:
    @pytest.mark.asyncio
    async def test_returns_opportunity_when_all_pass(self):
        adapter = _make_adapter(
            funding_rate="0.0001",                          # APR ~10.95% > 10%
            depth_usd=100_000,
            history=["0.0001", "0.0001", "0.0001"],        # 3/3 positive
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        results = await scanner.scan()

        assert len(results) == 1
        opp = results[0]
        assert opp.exchange == "binance"
        assert opp.symbol == BTC
        assert opp.apr_pct > Decimal("10")

    @pytest.mark.asyncio
    async def test_filters_low_apr(self):
        adapter = _make_adapter(
            funding_rate="0.000050",    # APR ~5.5% < 10% threshold
            depth_usd=100_000,
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        assert await scanner.scan() == []

    @pytest.mark.asyncio
    async def test_filters_negative_funding(self):
        adapter = _make_adapter(funding_rate="-0.0001", depth_usd=100_000)
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        assert await scanner.scan() == []

    @pytest.mark.asyncio
    async def test_filters_insufficient_depth(self):
        adapter = _make_adapter(
            funding_rate="0.0001",
            depth_usd=100,              # tiny — below 10000 threshold
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        assert await scanner.scan() == []

    @pytest.mark.asyncio
    async def test_filters_unstable_history(self):
        adapter = _make_adapter(
            funding_rate="0.0001",
            depth_usd=100_000,
            history=["0.0001", "-0.0002", "-0.0002"],  # only 1/3 positive, need 2
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        assert await scanner.scan() == []

    @pytest.mark.asyncio
    async def test_sorted_by_apr_descending(self):
        """多币种时按 APR 降序返回"""
        adapter = MagicMock()
        adapter.supported_instruments = [InstrumentType.SPOT, InstrumentType.PERPETUAL]

        btc_funding = _make_funding(BTC, "0.0003")   # APR ~32.85%
        eth_funding = _make_funding(ETH, "0.0001")   # APR ~10.95%

        async def mock_funding(symbol):
            return btc_funding if symbol == BTC else eth_funding

        adapter.fetch_funding_rate = mock_funding
        adapter.fetch_orderbook = AsyncMock(
            return_value=_make_orderbook(BTC, 200_000)
        )
        # Explicitly set to None so getattr(..., None) returns None rather than
        # an un-awaitable MagicMock attribute
        adapter.fetch_funding_rate_history = None

        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[ETH, BTC],         # ETH listed first intentionally
            config=_default_config(min_apr_pct=Decimal("5"), min_positive_periods=1),
        )
        results = await scanner.scan()

        assert len(results) == 2
        assert results[0].symbol == BTC   # higher APR first
        assert results[1].symbol == ETH

    @pytest.mark.asyncio
    async def test_exchange_error_does_not_propagate(self):
        """单个交易所报错不影响整体扫描"""
        from app.exchanges.errors import NetworkError

        adapter = MagicMock()
        adapter.supported_instruments = [InstrumentType.SPOT, InstrumentType.PERPETUAL]
        adapter.fetch_funding_rate = AsyncMock(
            side_effect=NetworkError("timeout", exchange="binance")
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        results = await scanner.scan()
        assert results == []

    @pytest.mark.asyncio
    async def test_skips_exchange_without_perpetual(self):
        """不支持永续合约的适配器被跳过,不调用 fetch_funding_rate"""
        adapter = MagicMock()
        adapter.supported_instruments = [InstrumentType.SPOT]
        adapter.fetch_funding_rate = AsyncMock()

        scanner = FundingRateScanner(
            adapters={"spot_only": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        results = await scanner.scan()
        adapter.fetch_funding_rate.assert_not_called()
        assert results == []

    @pytest.mark.asyncio
    async def test_no_history_method_passes_history_check(self):
        """适配器没有 fetch_funding_rate_history 时,历史检查视为通过"""
        adapter = _make_adapter(
            funding_rate="0.0001",
            depth_usd=100_000,
            history=None,   # no history method attached
        )
        # Remove any history method that might have been set
        if hasattr(adapter, "fetch_funding_rate_history"):
            del adapter.fetch_funding_rate_history

        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),
        )
        results = await scanner.scan()
        assert len(results) == 1


# ---------------------------------------------------------------------------
# 候选展示门槛 vs 入场门槛（双阈值架构）
# ---------------------------------------------------------------------------


class TestScanThresholdAndPassesEntry:
    """scan_threshold_apr_pct 控制 UI 候选范围；min_apr_pct 控制实盘开仓。"""

    def test_effective_scan_threshold_falls_back_to_min_apr_pct(self):
        cfg = ScannerConfig(
            min_apr_pct=Decimal("25"),
            scan_threshold_apr_pct=Decimal("0"),  # 0 = 回退
        )
        assert cfg.effective_scan_threshold == Decimal("25")

    def test_effective_scan_threshold_uses_explicit_value(self):
        cfg = ScannerConfig(
            min_apr_pct=Decimal("25"),
            scan_threshold_apr_pct=Decimal("5"),
        )
        assert cfg.effective_scan_threshold == Decimal("5")

    def test_from_yaml_reads_scan_threshold(self):
        cfg = ScannerConfig.from_yaml({
            "entry": {
                "min_apr_pct": 25.0,
                "scan_threshold_apr_pct": 5.0,
            },
        })
        assert cfg.scan_threshold_apr_pct == Decimal("5.0")
        assert cfg.min_apr_pct == Decimal("25.0")

    def test_from_yaml_default_scan_threshold_is_zero(self):
        cfg = ScannerConfig.from_yaml({"entry": {"min_apr_pct": 25.0}})
        assert cfg.scan_threshold_apr_pct == Decimal("0")
        assert cfg.effective_scan_threshold == Decimal("25.0")

    @pytest.mark.asyncio
    async def test_candidate_above_scan_below_entry_marked_not_passes(self):
        """APR ∈ [scan_threshold, min_apr_pct) → 进 opps_list，passes_entry=False"""
        adapter = _make_adapter(
            funding_rate="0.000050",   # APR ~5.475%
            depth_usd=100_000,
            history=["0.0001", "0.0001", "0.0001"],
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(
                min_apr_pct=Decimal("25"),
                scan_threshold_apr_pct=Decimal("5"),
                min_positive_periods=2,
            ),
        )
        results = await scanner.scan()
        assert len(results) == 1
        assert results[0].passes_entry is False
        assert results[0].apr_pct < Decimal("25")
        assert results[0].apr_pct >= Decimal("5")

    @pytest.mark.asyncio
    async def test_opp_above_entry_marked_passes(self):
        """APR ≥ min_apr_pct → passes_entry=True"""
        adapter = _make_adapter(
            funding_rate="0.0003",   # APR ~32.85%
            depth_usd=100_000,
            history=["0.0003", "0.0003", "0.0003"],
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(
                min_apr_pct=Decimal("25"),
                scan_threshold_apr_pct=Decimal("5"),
                min_positive_periods=2,
            ),
        )
        results = await scanner.scan()
        assert len(results) == 1
        assert results[0].passes_entry is True

    @pytest.mark.asyncio
    async def test_below_scan_threshold_filtered_out(self):
        """APR < scan_threshold → 完全跳过，不进 opps_list"""
        adapter = _make_adapter(
            funding_rate="0.00001",   # APR ~1.095% < scan_threshold=5
            depth_usd=100_000,
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(
                min_apr_pct=Decimal("25"),
                scan_threshold_apr_pct=Decimal("5"),
            ),
        )
        results = await scanner.scan()
        assert results == []

    @pytest.mark.asyncio
    async def test_legacy_no_scan_threshold_keeps_old_behavior(self):
        """scan_threshold=0 时回退用 min_apr_pct（向下兼容）"""
        adapter = _make_adapter(
            funding_rate="0.000050",   # APR ~5.475% < 10% old threshold
            depth_usd=100_000,
        )
        scanner = FundingRateScanner(
            adapters={"binance": adapter},
            symbols=[BTC],
            config=_default_config(),  # min_apr_pct=10, scan_threshold=0
        )
        # Should be filtered out (旧行为：低于 min_apr_pct 直接扔)
        assert await scanner.scan() == []
