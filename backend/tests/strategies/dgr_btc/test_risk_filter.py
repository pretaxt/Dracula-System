"""Tests for dgr_btc.risk_filter — multi-dim triggers + levels."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.strategies.dgr_btc.risk_filter import (
    RiskFilter,
    RiskLevel,
    RiskTrigger,
)
from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    PortfolioSnapshot,
    Position,
)


def _make_filter(initial_equity=Decimal("10000")) -> RiskFilter:
    return RiskFilter(
        trend_grids_threshold=5,
        hourly_vol_threshold=Decimal("1.5"),
        margin_ratio_min=Decimal("0.35"),
        funding_filter_enabled=True,
        funding_threshold=Decimal("0"),
        min_orderbook_depth=Decimal("50000"),
        max_daily_loss_pct=Decimal("0.05"),
        max_drawdown_pct=Decimal("0.15"),
        initial_equity=initial_equity,
    )


def _make_market(vol=Decimal("0.5"), funding_rate=Decimal("0.0001"), depth=Decimal("100000")) -> MarketState:
    return MarketState(
        timestamp=datetime.now(timezone.utc),
        spot_price=Decimal("80000"),
        perp_price=Decimal("80000"),
        funding_rate=funding_rate,
        bid_depth_usdt=depth,
        ask_depth_usdt=depth,
        realized_vol_1h=vol,
    )


def _make_snapshot(equity=Decimal("10000")) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        timestamp=datetime.now(timezone.utc),
        spot_position=Position(symbol="BTC/USDT", market=MarketType.SPOT, quantity=Decimal("0.05")),
        perp_position=Position(symbol="BTC/USDT:USDT", market=MarketType.PERP, quantity=Decimal("-0.05")),
        cash_usdt=Decimal("6000"),
        mark_price=Decimal("80000"),
        delta=Decimal("0"),
        total_equity=equity,
    )


def test_normal_returns_normal():
    rf = _make_filter()
    report = rf.evaluate(
        snapshot=_make_snapshot(),
        market=_make_market(),
        consecutive_trend_grids=0,
        margin_ratio=Decimal("1.0"),
    )
    assert report.level == RiskLevel.NORMAL
    assert report.should_pause is False


def test_trend_breakout_triggers_caution():
    """连续 5 单方向 → TREND_BREAKOUT trigger; 但单 trend 仅 CAUTION."""
    rf = _make_filter()
    report = rf.evaluate(
        snapshot=_make_snapshot(),
        market=_make_market(),
        consecutive_trend_grids=5,
        margin_ratio=Decimal("1.0"),
    )
    assert RiskTrigger.TREND_BREAKOUT in report.triggers
    assert report.level == RiskLevel.CAUTION


def test_high_vol_triggers_defensive():
    rf = _make_filter()
    report = rf.evaluate(
        snapshot=_make_snapshot(),
        market=_make_market(vol=Decimal("1.6")),  # > 1.5
        consecutive_trend_grids=0,
        margin_ratio=Decimal("1.0"),
    )
    assert RiskTrigger.HIGH_VOLATILITY in report.triggers
    assert report.level == RiskLevel.DEFENSIVE


def test_low_margin_triggers_defensive():
    rf = _make_filter()
    report = rf.evaluate(
        snapshot=_make_snapshot(),
        market=_make_market(),
        consecutive_trend_grids=0,
        margin_ratio=Decimal("0.30"),  # < 0.35
    )
    assert RiskTrigger.LOW_MARGIN in report.triggers
    assert report.level == RiskLevel.DEFENSIVE


def test_max_drawdown_triggers_emergency():
    rf = _make_filter()
    # 先记录 peak = 10000, 然后净值跌到 8400 (回撤 -16% > -15%)
    rf.peak_equity = Decimal("10000")
    report = rf.evaluate(
        snapshot=_make_snapshot(equity=Decimal("8400")),
        market=_make_market(),
        consecutive_trend_grids=0,
        margin_ratio=Decimal("1.0"),
    )
    assert RiskTrigger.MAX_DRAWDOWN in report.triggers
    assert report.level == RiskLevel.EMERGENCY
    assert report.should_pause is True


def test_daily_loss_triggers_emergency():
    rf = _make_filter()
    # day_start_equity = 10000, current = 9400 → daily_loss -6% > -5%
    rf.day_start_equity = Decimal("10000")
    report = rf.evaluate(
        snapshot=_make_snapshot(equity=Decimal("9400")),
        market=_make_market(),
        consecutive_trend_grids=0,
        margin_ratio=Decimal("1.0"),
    )
    assert RiskTrigger.DAILY_LOSS_LIMIT in report.triggers
    assert report.level == RiskLevel.EMERGENCY


def test_negative_funding_triggers_caution():
    rf = _make_filter()
    report = rf.evaluate(
        snapshot=_make_snapshot(),
        market=_make_market(funding_rate=Decimal("-0.0001")),  # < 0
        consecutive_trend_grids=0,
        margin_ratio=Decimal("1.0"),
    )
    assert RiskTrigger.NEGATIVE_FUNDING in report.triggers
    assert report.level == RiskLevel.CAUTION
