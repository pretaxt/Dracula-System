"""单元测试 — backtest/models.py（BacktestResult 绩效指标）"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.backtest.models import (
    BacktestConfig,
    BacktestResult,
    EquityPoint,
    FundingPeriod,
)
from app.exchanges.models import Symbol
from app.risk.limits import RiskLimits
from app.risk.models import ExitReason, Position

BTC = Symbol("BTC", "USDT")
_T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _config(initial: str = "10000", size: str = "500") -> BacktestConfig:
    return BacktestConfig(
        symbol=BTC,
        exchange="binance",
        initial_capital_usd=Decimal(initial),
        size_per_trade_usd=Decimal(size),
        risk_limits=RiskLimits(),
    )


def _equity_curve(*values: str, start: datetime = _T0) -> list[EquityPoint]:
    """按 8 小时间隔生成资金曲线。"""
    return [
        EquityPoint(
            timestamp=start + timedelta(hours=8 * i),
            equity_usd=Decimal(v),
            open_positions=0,
        )
        for i, v in enumerate(values)
    ]


def _closed_pos(net_pnl: str, notional: str = "500") -> Position:
    pos = Position(
        strategy_instance="test",
        symbol=BTC,
        notional_usd=Decimal(notional),
    )
    pos.mark_open()
    pos.mark_closed(ExitReason.MANUAL, realized_pnl=Decimal(net_pnl))
    return pos


# ---------------------------------------------------------------------------
# FundingPeriod.apr_pct
# ---------------------------------------------------------------------------


class TestFundingPeriodAprPct:
    def test_positive_rate(self):
        period = FundingPeriod(
            symbol=BTC,
            exchange="binance",
            timestamp=_T0,
            funding_rate=Decimal("0.0001"),
            spot_price=Decimal("42000"),
            perp_price=Decimal("42010"),
        )
        # 0.0001 × 3 × 365 × 100 = 10.95
        assert period.apr_pct == Decimal("10.95")

    def test_zero_rate(self):
        period = FundingPeriod(
            symbol=BTC,
            exchange="binance",
            timestamp=_T0,
            funding_rate=Decimal("0"),
            spot_price=Decimal("42000"),
            perp_price=Decimal("42000"),
        )
        assert period.apr_pct == Decimal("0")


# ---------------------------------------------------------------------------
# BacktestResult — 空结果默认值
# ---------------------------------------------------------------------------


class TestBacktestResultEmpty:
    def test_final_equity_defaults_to_initial_capital(self):
        r = BacktestResult(config=_config("10000"))
        assert r.final_equity_usd == Decimal("10000")

    def test_total_return_zero(self):
        r = BacktestResult(config=_config("10000"))
        assert r.total_return_pct == Decimal("0")

    def test_total_trades_zero(self):
        r = BacktestResult(config=_config())
        assert r.total_trades == 0

    def test_total_fees_zero(self):
        r = BacktestResult(config=_config())
        assert r.total_fees_usd == Decimal("0")

    def test_total_funding_zero(self):
        r = BacktestResult(config=_config())
        assert r.total_funding_usd == Decimal("0")

    def test_max_drawdown_zero(self):
        r = BacktestResult(config=_config())
        assert r.max_drawdown_pct == Decimal("0")

    def test_sharpe_zero_insufficient_data(self):
        r = BacktestResult(config=_config())
        assert r.sharpe_ratio == Decimal("0")

    def test_win_rate_zero_no_trades(self):
        r = BacktestResult(config=_config())
        assert r.win_rate_pct == Decimal("0")


# ---------------------------------------------------------------------------
# total_return_pct
# ---------------------------------------------------------------------------


class TestTotalReturn:
    def test_positive_return(self):
        r = BacktestResult(config=_config("10000"))
        r.equity_curve = _equity_curve("10000", "10500")
        assert r.total_return_pct == Decimal("5")

    def test_negative_return(self):
        r = BacktestResult(config=_config("10000"))
        r.equity_curve = _equity_curve("10000", "9000")
        assert r.total_return_pct == Decimal("-10")

    def test_zero_return(self):
        r = BacktestResult(config=_config("10000"))
        r.equity_curve = _equity_curve("10000", "10000")
        assert r.total_return_pct == Decimal("0")


# ---------------------------------------------------------------------------
# max_drawdown_pct
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_no_drawdown_monotonic_increase(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve("10000", "10100", "10200")
        assert r.max_drawdown_pct == Decimal("0")

    def test_simple_drawdown_10_pct(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve("10000", "9000", "9500")
        assert r.max_drawdown_pct == Decimal("10")

    def test_drawdown_measured_from_new_peak(self):
        r = BacktestResult(config=_config())
        # 峰值 11000 → 9900，回撤 = (11000-9900)/11000
        r.equity_curve = _equity_curve("10000", "11000", "9900")
        expected = (Decimal("11000") - Decimal("9900")) / Decimal("11000") * Decimal("100")
        assert r.max_drawdown_pct == expected

    def test_single_point_no_drawdown(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve("10000")
        assert r.max_drawdown_pct == Decimal("0")


# ---------------------------------------------------------------------------
# sharpe_ratio
# ---------------------------------------------------------------------------


class TestSharpeRatio:
    def test_positive_sharpe_for_steady_gains(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve(
            "10000", "10010", "10020", "10030", "10040", "10050"
        )
        assert r.sharpe_ratio > Decimal("0")

    def test_zero_sharpe_for_flat_curve(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve("10000", "10000", "10000", "10000")
        assert r.sharpe_ratio == Decimal("0")

    def test_sharpe_needs_at_least_two_points(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve("10000")
        assert r.sharpe_ratio == Decimal("0")

    def test_negative_sharpe_for_declining_curve(self):
        r = BacktestResult(config=_config())
        r.equity_curve = _equity_curve(
            "10000", "9990", "9980", "9970", "9960", "9950"
        )
        assert r.sharpe_ratio < Decimal("0")


# ---------------------------------------------------------------------------
# win_rate_pct
# ---------------------------------------------------------------------------


class TestWinRate:
    def test_all_winners(self):
        r = BacktestResult(config=_config())
        r.all_positions = [_closed_pos("10"), _closed_pos("5")]
        assert r.win_rate_pct == Decimal("100")

    def test_all_losers(self):
        r = BacktestResult(config=_config())
        r.all_positions = [_closed_pos("-10"), _closed_pos("-5")]
        assert r.win_rate_pct == Decimal("0")

    def test_fifty_percent_win_rate(self):
        r = BacktestResult(config=_config())
        r.all_positions = [
            _closed_pos("10"),
            _closed_pos("-5"),
            _closed_pos("3"),
            _closed_pos("-1"),
        ]
        assert r.win_rate_pct == Decimal("50")

    def test_open_positions_excluded(self):
        r = BacktestResult(config=_config())
        open_pos = Position(
            strategy_instance="s1", symbol=BTC, notional_usd=Decimal("500")
        )
        open_pos.mark_open()
        r.all_positions = [open_pos, _closed_pos("10")]
        assert r.win_rate_pct == Decimal("100")


# ---------------------------------------------------------------------------
# annualized_return_pct
# ---------------------------------------------------------------------------


class TestAnnualizedReturn:
    def test_annualized_scales_by_days(self):
        r = BacktestResult(config=_config("10000"))
        r.equity_curve = [
            EquityPoint(timestamp=_T0, equity_usd=Decimal("10000"), open_positions=0),
            EquityPoint(
                timestamp=_T0 + timedelta(days=180),
                equity_usd=Decimal("10500"),
                open_positions=0,
            ),
        ]
        # 总收益 5%，持续 180 天 → 年化 = 5% × 365/180
        expected = Decimal("5") * Decimal("365") / Decimal("180")
        assert r.annualized_return_pct == expected

    def test_annualized_one_day_equals_total_return(self):
        r = BacktestResult(config=_config("10000"))
        r.equity_curve = [
            EquityPoint(timestamp=_T0, equity_usd=Decimal("10000"), open_positions=0),
            EquityPoint(
                timestamp=_T0 + timedelta(days=365),
                equity_usd=Decimal("11000"),
                open_positions=0,
            ),
        ]
        # 持续 365 天，年化 = 总收益
        assert r.annualized_return_pct == r.total_return_pct
