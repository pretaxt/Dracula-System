"""#02 perp-basis scanner 单元测试。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.core.market_data_hub import FundingRateEntry, MarketDataHub
from app.exchanges.models import FundingRate, Symbol
from app.strategies.perp_basis.scanner import (
    PerpBasisScanner,
    PerpBasisScannerConfig,
    all_pairs,
)


def _make_fr(symbol_str: str, exchange: str, rate: str = "0.0001",
             interval: int = 8) -> FundingRate:
    base, _, quote = symbol_str.partition("/")
    return FundingRate(
        symbol=Symbol(base, quote or "USDT"),
        exchange=exchange,
        rate=Decimal(rate),
        next_funding_time=1_700_000_000_000,
        funding_interval_hours=interval,
    )


def _hub_with(adapters_dict, fundings: dict[tuple[str, str], FundingRate]):
    """Build hub with pre-populated funding cache."""
    hub = MarketDataHub(adapters=adapters_dict)
    now = datetime.now(timezone.utc)
    for (ex, sym_str), fr in fundings.items():
        hub._cache[ex].funding_rates[sym_str] = FundingRateEntry(
            rate=fr, fetched_at=now,
        )
    return hub


# ---------------------------------------------------------------------------
# all_pairs
# ---------------------------------------------------------------------------


class TestAllPairs:
    def test_two(self):
        assert all_pairs(["binance", "okx"]) == [("binance", "okx")]

    def test_five_combinations(self):
        pairs = all_pairs(["binance", "okx", "bitget", "bybit", "htx"])
        assert len(pairs) == 10  # C(5,2)

    def test_dedupe_and_lower(self):
        pairs = all_pairs(["BINANCE", "binance", "OKX"])
        assert pairs == [("binance", "okx")]

    def test_empty(self):
        assert all_pairs([]) == []


# ---------------------------------------------------------------------------
# scanner
# ---------------------------------------------------------------------------


class TestPerpBasisScanner:
    def test_returns_empty_when_no_data(self):
        hub = _hub_with({"binance": object(), "okx": object()}, {})
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        scanner = PerpBasisScanner(hub, cfg)
        assert scanner.scan() == []

    def test_finds_diff_above_threshold(self):
        # binance APR 10.95% (rate 0.0001 × 1095), okx APR 32.85% (rate 0.0003)
        # diff = 32.85 - 10.95 = 21.9% > 3% threshold
        hub = _hub_with(
            {"binance": object(), "okx": object()},
            {
                ("binance", "BTC/USDT"): _make_fr("BTC/USDT", "binance", "0.0001"),
                ("okx", "BTC/USDT"): _make_fr("BTC/USDT", "okx", "0.0003"),
            },
        )
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        opps = PerpBasisScanner(hub, cfg).scan()
        assert len(opps) == 1
        opp = opps[0]
        assert opp.symbol == "BTC/USDT"
        assert opp.long_exchange == "binance"   # 低 funding
        assert opp.short_exchange == "okx"      # 高 funding
        assert opp.diff_apr_pct > Decimal("21")
        assert opp.diff_apr_pct < Decimal("22")

    def test_skips_below_threshold(self):
        # 两边 funding 几乎相同 → diff < 3%
        hub = _hub_with(
            {"binance": object(), "okx": object()},
            {
                ("binance", "BTC/USDT"): _make_fr("BTC/USDT", "binance", "0.0001"),
                ("okx", "BTC/USDT"): _make_fr("BTC/USDT", "okx", "0.00012"),
            },
        )
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        assert PerpBasisScanner(hub, cfg).scan() == []

    def test_one_side_missing_skipped(self):
        # binance 有，okx 无
        hub = _hub_with(
            {"binance": object(), "okx": object()},
            {("binance", "BTC/USDT"): _make_fr("BTC/USDT", "binance", "0.001")},
        )
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        assert PerpBasisScanner(hub, cfg).scan() == []

    def test_negative_funding_handled(self):
        # binance -0.0002 (negative APR), okx +0.0003 → diff 大
        hub = _hub_with(
            {"binance": object(), "okx": object()},
            {
                ("binance", "BTC/USDT"): _make_fr("BTC/USDT", "binance", "-0.0002"),
                ("okx", "BTC/USDT"): _make_fr("BTC/USDT", "okx", "0.0003"),
            },
        )
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        opps = PerpBasisScanner(hub, cfg).scan()
        assert len(opps) == 1
        # binance APR ~ -21.9, okx ~ 32.85 → diff ~ 54.75
        assert opps[0].long_exchange == "binance"
        assert opps[0].short_exchange == "okx"
        assert opps[0].diff_apr_pct > Decimal("54")

    def test_sorted_descending(self):
        hub = _hub_with(
            {"binance": object(), "okx": object()},
            {
                ("binance", "BTC/USDT"): _make_fr("BTC/USDT", "binance", "0.0001"),
                ("okx", "BTC/USDT"): _make_fr("BTC/USDT", "okx", "0.0002"),  # diff small
                ("binance", "ETH/USDT"): _make_fr("ETH/USDT", "binance", "0.0001"),
                ("okx", "ETH/USDT"): _make_fr("ETH/USDT", "okx", "0.0010"),  # diff large
            },
        )
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC", "ETH"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
        )
        opps = PerpBasisScanner(hub, cfg).scan()
        assert len(opps) == 2
        # ETH diff 大 → 排第一
        assert opps[0].symbol == "ETH/USDT"
        assert opps[1].symbol == "BTC/USDT"

    def test_max_opportunities_cap(self):
        # 5 标的全部满足，cap 至 2
        fundings = {}
        for sym in ["BTC", "ETH", "SOL", "BNB", "XRP"]:
            fundings[("binance", f"{sym}/USDT")] = _make_fr(f"{sym}/USDT", "binance", "0.0001")
            fundings[("okx", f"{sym}/USDT")] = _make_fr(f"{sym}/USDT", "okx", "0.001")
        hub = _hub_with({"binance": object(), "okx": object()}, fundings)
        cfg = PerpBasisScannerConfig(
            candidate_symbols=["BTC", "ETH", "SOL", "BNB", "XRP"],
            exchange_pairs=[("binance", "okx")],
            min_diff_apr_pct=Decimal("3.0"),
            max_opportunities=2,
        )
        opps = PerpBasisScanner(hub, cfg).scan()
        assert len(opps) == 2
