"""单元测试 — backtest/engine.py（BacktestEngine 主循环）"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.models import BacktestConfig, FundingPeriod
from app.exchanges.models import Symbol
from app.risk.limits import RiskLimits
from app.risk.models import ExitReason

BTC = Symbol("BTC", "USDT")
_T0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _config(
    initial: str = "10000",
    size: str = "500",
    max_positions: int = 5,
    min_apr_pct: str = "10",
    stop_loss_pct: str = "5",
    max_hold_hours: str = "168",
    slippage_bps: str = "0",
    fee_rate: str = "0",
) -> BacktestConfig:
    limits = RiskLimits(
        max_positions=max_positions,
        max_total_notional_usd=Decimal("100000"),
        max_position_size_usd=Decimal("10000"),
        min_position_size_usd=Decimal("10"),
        stop_loss_pct=Decimal(stop_loss_pct),
        max_hold_hours=Decimal(max_hold_hours),
        min_apr_pct=Decimal(min_apr_pct),
    )
    return BacktestConfig(
        symbol=BTC,
        exchange="binance",
        initial_capital_usd=Decimal(initial),
        size_per_trade_usd=Decimal(size),
        risk_limits=limits,
        slippage_bps=Decimal(slippage_bps),
        fee_rate=Decimal(fee_rate),
    )


def _periods(
    n: int,
    funding_rate: str = "0.0003",
    spot_price: str = "60000",
    perp_price: str = "60000",
    start: datetime = _T0,
) -> list[FundingPeriod]:
    """生成 n 个等间隔（8h）的 FundingPeriod。"""
    return [
        FundingPeriod(
            symbol=BTC,
            exchange="binance",
            timestamp=start + timedelta(hours=8 * i),
            funding_rate=Decimal(funding_rate),
            spot_price=Decimal(spot_price),
            perp_price=Decimal(perp_price),
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 基础运行
# ---------------------------------------------------------------------------


class TestEngineRun:
    @pytest.mark.asyncio
    async def test_empty_periods_returns_empty_result(self):
        engine = BacktestEngine(_config())
        result = await engine.run([])
        assert result.equity_curve == []
        assert result.all_positions == []

    @pytest.mark.asyncio
    async def test_equity_curve_length_equals_period_count(self):
        engine = BacktestEngine(_config())
        result = await engine.run(_periods(10))
        assert len(result.equity_curve) == 10

    @pytest.mark.asyncio
    async def test_equity_curve_timestamps_match_periods(self):
        engine = BacktestEngine(_config())
        periods = _periods(5)
        result = await engine.run(periods)
        for pt, p in zip(result.equity_curve, periods):
            assert pt.timestamp == p.timestamp

    @pytest.mark.asyncio
    async def test_unsorted_periods_are_sorted_by_engine(self):
        """引擎应自动对乱序 periods 排序。"""
        engine = BacktestEngine(_config())
        periods = list(reversed(_periods(5)))
        result = await engine.run(periods)
        timestamps = [pt.timestamp for pt in result.equity_curve]
        assert timestamps == sorted(timestamps)

    @pytest.mark.asyncio
    async def test_result_config_is_preserved(self):
        cfg = _config(initial="5000", size="200")
        engine = BacktestEngine(cfg)
        result = await engine.run(_periods(3))
        assert result.config is cfg


# ---------------------------------------------------------------------------
# 开仓行为
# ---------------------------------------------------------------------------


class TestOpenBehavior:
    @pytest.mark.asyncio
    async def test_opens_position_when_rate_above_threshold(self):
        # apr_pct ≈ 32.85% >> min_apr_pct 10%
        engine = BacktestEngine(_config(max_positions=1))
        result = await engine.run(_periods(3))
        assert result.total_trades >= 1

    @pytest.mark.asyncio
    async def test_no_position_when_rate_below_threshold(self):
        # 极低费率，年化 << min_apr_pct
        engine = BacktestEngine(_config(min_apr_pct="99"))
        result = await engine.run(_periods(5, funding_rate="0.0000001"))
        assert result.total_trades == 0

    @pytest.mark.asyncio
    async def test_max_positions_limits_concurrent_trades(self):
        engine = BacktestEngine(_config(max_positions=2))
        result = await engine.run(_periods(10))
        assert all(pt.open_positions <= 2 for pt in result.equity_curve)

    @pytest.mark.asyncio
    async def test_all_positions_closed_at_end(self):
        """回测结束后所有仓位均已平仓。"""
        engine = BacktestEngine(_config(max_positions=3))
        result = await engine.run(_periods(5))
        open_at_end = [p for p in result.all_positions if p.is_open]
        assert open_at_end == []


# ---------------------------------------------------------------------------
# 资金费结算
# ---------------------------------------------------------------------------


class TestFundingSettlement:
    @pytest.mark.asyncio
    async def test_funding_accumulates_on_open_position(self):
        engine = BacktestEngine(_config(max_positions=1, fee_rate="0"))
        result = await engine.run(_periods(5, funding_rate="0.0003"))
        assert result.total_funding_usd > Decimal("0")

    @pytest.mark.asyncio
    async def test_zero_funding_rate_yields_no_income(self):
        engine = BacktestEngine(_config(fee_rate="0", min_apr_pct="0"))
        result = await engine.run(_periods(5, funding_rate="0"))
        assert result.total_funding_usd == Decimal("0")

    @pytest.mark.asyncio
    async def test_equity_increases_with_positive_funding(self):
        """正资金费率 + 无滑点 + 无手续费 → 权益末值高于初始值。"""
        engine = BacktestEngine(
            _config(max_positions=1, slippage_bps="0", fee_rate="0")
        )
        result = await engine.run(_periods(10, funding_rate="0.0003"))
        assert result.final_equity_usd > Decimal("10000")

    @pytest.mark.asyncio
    async def test_funding_proportional_to_position_size(self):
        """资金费收入应与仓位名义价值正相关。"""
        engine_small = BacktestEngine(
            _config(size="500", max_positions=1, fee_rate="0")
        )
        engine_large = BacktestEngine(
            _config(size="2000", max_positions=1, fee_rate="0")
        )
        result_small = await engine_small.run(_periods(5, funding_rate="0.0003"))
        result_large = await engine_large.run(_periods(5, funding_rate="0.0003"))
        assert result_large.total_funding_usd > result_small.total_funding_usd


# ---------------------------------------------------------------------------
# 风控触发平仓
# ---------------------------------------------------------------------------


class TestRiskTriggeredClose:
    @pytest.mark.asyncio
    async def test_stop_loss_closes_position(self):
        """止损触发：亏损超过极低阈值时平仓，exit_reason = STOP_LOSS。"""
        engine = BacktestEngine(
            _config(
                stop_loss_pct="0.001",  # 极低止损线
                slippage_bps="5",       # 滑点制造即时亏损
                fee_rate="0.001",       # 手续费加剧亏损
                max_positions=1,
            )
        )
        result = await engine.run(_periods(5))
        stop_loss_exits = [
            p for p in result.all_positions
            if p.exit_reason == ExitReason.STOP_LOSS
        ]
        assert len(stop_loss_exits) >= 1

    @pytest.mark.asyncio
    async def test_max_hold_time_closes_position(self):
        """超时触发：持仓超过 max_hold_hours 时平仓，exit_reason = MAX_HOLD_TIME。"""
        # max_hold_hours=16 = 2 个 8h 周期，运行 6 期应触发
        engine = BacktestEngine(
            _config(max_positions=1, max_hold_hours="16", fee_rate="0")
        )
        result = await engine.run(_periods(6))
        timeout_exits = [
            p for p in result.all_positions
            if p.exit_reason == ExitReason.MAX_HOLD_TIME
        ]
        assert len(timeout_exits) >= 1


# ---------------------------------------------------------------------------
# 绩效指标集成
# ---------------------------------------------------------------------------


class TestMetricsIntegration:
    @pytest.mark.asyncio
    async def test_total_trades_matches_all_positions_count(self):
        engine = BacktestEngine(_config(max_positions=2))
        result = await engine.run(_periods(10))
        assert result.total_trades == len(result.all_positions)

    @pytest.mark.asyncio
    async def test_no_fees_means_zero_total_fees(self):
        engine = BacktestEngine(_config(fee_rate="0", slippage_bps="0"))
        result = await engine.run(_periods(5))
        assert result.total_fees_usd == Decimal("0")

    @pytest.mark.asyncio
    async def test_equity_curve_records_open_positions(self):
        engine = BacktestEngine(_config(max_positions=1))
        result = await engine.run(_periods(5))
        assert any(pt.open_positions > 0 for pt in result.equity_curve)

    @pytest.mark.asyncio
    async def test_positive_return_with_high_funding_no_costs(self):
        """高资金费率 + 零成本场景，总收益率应为正。"""
        engine = BacktestEngine(
            _config(
                max_positions=1,
                slippage_bps="0",
                fee_rate="0",
                initial="10000",
                size="1000",
            )
        )
        result = await engine.run(_periods(30, funding_rate="0.001"))
        assert result.total_return_pct > Decimal("0")
