"""Tests for Phase E.3 basis-aware snapshot + dgr_engine perp_klines."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pandas as pd

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy
from app.strategies.dgr_btc.types import MarketState
from app.backtest.dgr_engine import DgrBtcBacktestEngine


@pytest.fixture
def cfg() -> DgrBtcStrategyConfig:
    return DgrBtcStrategyConfig.from_yaml({})


# ============================================================
# PortfolioSnapshot — 新字段填充
# ============================================================

class TestSnapshotBasisFields:
    def test_zero_basis_when_prices_equal(self, cfg: DgrBtcStrategyConfig) -> None:
        s = DgrBtcStrategy(cfg, start_price=Decimal("75000"))
        market = MarketState(
            timestamp=datetime.now(timezone.utc),
            spot_price=Decimal("75000"),
            perp_price=Decimal("75000"),
        )
        snap = s.get_snapshot(market)
        assert snap.spot_mark_price == Decimal("75000")
        assert snap.perp_mark_price == Decimal("75000")
        assert snap.basis_bps == Decimal("0")
        # 兼容: mark_price 仍 == spot_mark_price
        assert snap.mark_price == snap.spot_mark_price

    def test_positive_basis_perp_above_spot(self, cfg: DgrBtcStrategyConfig) -> None:
        s = DgrBtcStrategy(cfg, start_price=Decimal("75000"))
        market = MarketState(
            timestamp=datetime.now(timezone.utc),
            spot_price=Decimal("75000"),
            perp_price=Decimal("75100"),
        )
        snap = s.get_snapshot(market)
        # (75100 - 75000) / 75000 * 10000 = 13.333...
        assert snap.spot_mark_price == Decimal("75000")
        assert snap.perp_mark_price == Decimal("75100")
        expected = (Decimal("75100") - Decimal("75000")) / Decimal("75000") * Decimal(10000)
        assert snap.basis_bps == expected
        assert snap.basis_bps > Decimal("13")
        assert snap.basis_bps < Decimal("14")

    def test_negative_basis_perp_below_spot(self, cfg: DgrBtcStrategyConfig) -> None:
        s = DgrBtcStrategy(cfg, start_price=Decimal("75000"))
        market = MarketState(
            timestamp=datetime.now(timezone.utc),
            spot_price=Decimal("75000"),
            perp_price=Decimal("74900"),
        )
        snap = s.get_snapshot(market)
        assert snap.basis_bps < Decimal("0")
        assert snap.basis_bps > Decimal("-15")

    def test_basis_zero_safety_when_spot_zero(self, cfg: DgrBtcStrategyConfig) -> None:
        s = DgrBtcStrategy(cfg, start_price=Decimal("75000"))
        market = MarketState(
            timestamp=datetime.now(timezone.utc),
            spot_price=Decimal("0"),
            perp_price=Decimal("100"),
        )
        snap = s.get_snapshot(market)
        assert snap.basis_bps == Decimal("0")  # 防 div0


# ============================================================
# dgr_engine — perp_klines 参数 byte-equal 兼容
# ============================================================

def _make_kline_df(n: int = 100, start_price: float = 75000.0) -> pd.DataFrame:
    rows = []
    base_ts = datetime(2026, 5, 23, 0, 0, tzinfo=timezone.utc)
    for i in range(n):
        p = start_price + (i % 3 - 1) * 50
        rows.append({
            "timestamp": base_ts + timedelta(minutes=i),
            "open": p, "high": p, "low": p, "close": p, "volume": 1.0,
        })
    return pd.DataFrame(rows)


class TestEngineBackwardCompat:
    def test_no_perp_klines_runs_normally(self, cfg: DgrBtcStrategyConfig) -> None:
        klines = _make_kline_df(100)
        engine = DgrBtcBacktestEngine(cfg, klines, funding=None)
        result = engine.run()
        assert result.initial_equity > 0
        assert engine.has_perp_data is False
        assert engine._perp_prices is None

    def test_with_perp_klines_loads_data(self, cfg: DgrBtcStrategyConfig) -> None:
        klines = _make_kline_df(100)
        # perp 比 spot 高 50 USDT (basis = +0.067%)
        perp = klines.copy()
        perp["close"] = perp["close"] + 50.0
        engine = DgrBtcBacktestEngine(
            cfg, klines, funding=None, perp_klines=perp,
        )
        result = engine.run()
        assert engine.has_perp_data is True
        assert engine._perp_prices is not None
        assert len(engine._perp_prices) == 100
        # 第一个 perp price 应该比 spot[0]=74950 高 50 = 75000
        expected_perp_0 = float(klines.iloc[0]["close"]) + 50.0
        assert float(engine._perp_prices[0]) == pytest.approx(expected_perp_0)

    def test_perp_klines_with_missing_timestamps_falls_back(
        self, cfg: DgrBtcStrategyConfig
    ) -> None:
        klines = _make_kline_df(100)
        # perp 只覆盖前 50 根
        perp = klines.iloc[:50].copy()
        perp["close"] = perp["close"] + 50.0
        engine = DgrBtcBacktestEngine(
            cfg, klines, funding=None, perp_klines=perp,
        )
        engine.run()  # 不应 crash
        # 后 50 根 perp_prices 是 NaN, loop 会 fallback spot price
        assert engine.has_perp_data is True
        # merge_asof tolerance=2min: 超出会变 NaN
        import math
        assert any(math.isnan(p) for p in engine._perp_prices[60:])
