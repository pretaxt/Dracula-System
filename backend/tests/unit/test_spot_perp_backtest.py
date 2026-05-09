"""spot_perp 回测引擎单测 — 覆盖关键决策路径。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.spot_perp_engine import SpotPerpBacktestEngine
from app.backtest.spot_perp_models import (
    BasisSnapshot,
    SpotPerpBacktestConfig,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ts(minutes: int) -> datetime:
    """从一个固定 epoch + N 分钟。"""
    return datetime(2026, 5, 9, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _snap(minutes: int, spot: float, perp: float, sym: str = "BTC/USDT", ex: str = "binance") -> BasisSnapshot:
    return BasisSnapshot(
        timestamp=_ts(minutes),
        symbol=sym,
        exchange=ex,
        spot_price=Decimal(str(spot)),
        perp_price=Decimal(str(perp)),
    )


def _config(**overrides) -> SpotPerpBacktestConfig:
    base = dict(
        initial_capital_usd=Decimal("1000"),
        notional_per_position=Decimal("100"),
        max_concurrent=2,
        entry_pct=Decimal("0.30"),
        exit_pct=Decimal("0.10"),
        max_hold_hours=Decimal("12"),
        min_hold_minutes=Decimal("5"),
        stop_basis_widening_pct=Decimal("0.50"),
        peak_window_minutes=Decimal("0"),  # 默认禁用 peak 检查方便测试
        min_peak_dropoff_pct=Decimal("0"),
        slippage_pct=Decimal("0"),
        fee_rate=Decimal("0"),
        direction_filter="both",
    )
    base.update(overrides)
    return SpotPerpBacktestConfig(**base)


# ---------------------------------------------------------------------------
# BasisSnapshot 派生属性
# ---------------------------------------------------------------------------


class TestBasisSnapshot:
    def test_premium_basis(self):
        s = _snap(0, 100, 100.5)
        assert s.basis_pct == Decimal("0.5")
        assert s.direction == "premium"

    def test_discount_basis(self):
        s = _snap(0, 100, 99.5)
        assert s.basis_pct == Decimal("-0.5")
        assert s.direction == "discount"

    def test_zero_spot(self):
        s = _snap(0, 0, 100)
        assert s.basis_pct == Decimal("0")


# ---------------------------------------------------------------------------
# 入场决策
# ---------------------------------------------------------------------------


class TestEntry:
    def test_premium_above_threshold_opens(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        # basis 0.40% > entry 0.30% → 开仓；末尾强平
        result = engine.run([_snap(0, 100, 100.4)])
        assert result.total_trades == 1
        assert result.closed[0].direction == "premium"
        assert result.closed[0].exit_reason == "final_force_close"

    def test_below_threshold_skipped(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        engine.run([_snap(0, 100, 100.20)])  # 0.20% < 0.30%
        assert engine._open == {}
        assert engine._skipped == 1

    def test_direction_filter_premium_only(self):
        cfg = _config(direction_filter="premium")
        engine = SpotPerpBacktestEngine(cfg)
        # discount 0.40% 不开
        engine.run([_snap(0, 100, 99.6)])
        assert engine._open == {}

    def test_max_concurrent_caps(self):
        cfg = _config(max_concurrent=1)
        engine = SpotPerpBacktestEngine(cfg)
        result = engine.run([
            _snap(0, 100, 100.4, sym="BTC/USDT"),
            _snap(0, 200, 200.8, sym="ETH/USDT"),
        ])
        # 只能开 1 个 → 最终强平 1 笔
        assert result.total_trades == 1


# ---------------------------------------------------------------------------
# 退出决策
# ---------------------------------------------------------------------------


class TestExit:
    def test_basis_convergence_closes(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        snaps = [
            _snap(0, 100, 100.40),     # 入场 0.40% premium
            _snap(10, 100, 100.05),    # 10min 后基差 0.05% < exit 0.10% → 收敛
        ]
        result = engine.run(snaps)
        assert result.total_trades == 1
        assert result.closed[0].exit_reason == "basis_convergence"

    def test_basis_stop_widens_and_closes(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        snaps = [
            _snap(0, 100, 100.40),     # 入场 0.40%
            _snap(10, 100, 100.95),    # 10min 后扩大 0.55% ≥ stop 0.50% → basis_stop
        ]
        result = engine.run(snaps)
        # 第 1 笔 basis_stop 平 + 同 ts 又开第 2 笔（0.95% > 0.30 threshold）→ final_force_close
        # 至少要有一笔 basis_stop
        reasons = [t.exit_reason for t in result.closed]
        assert "basis_stop" in reasons

    def test_max_hold_force_close(self):
        cfg = _config(max_hold_hours=Decimal("0.1"))  # 6 min max hold
        engine = SpotPerpBacktestEngine(cfg)
        snaps = [
            _snap(0, 100, 100.40),    # 入场
            _snap(7, 100, 100.40),    # 7min 后基差不变（无收敛/无扩大）→ max_hold
        ]
        result = engine.run(snaps)
        # 第 1 笔 max_hold 平掉，同 ts 又开第 2 笔 → final_force_close
        reasons = [t.exit_reason for t in result.closed]
        assert "max_hold" in reasons

    def test_min_hold_blocks_premature_convergence(self):
        cfg = _config(min_hold_minutes=Decimal("10"))
        engine = SpotPerpBacktestEngine(cfg)
        snaps = [
            _snap(0, 100, 100.40),    # 入场
            _snap(3, 100, 100.05),    # 3min 后即收敛但 min_hold=10 不让平
            _snap(5, 100, 100.05),    # 5min 仍未到
            _snap(11, 100, 100.05),   # 11min 后才允许收敛平仓
        ]
        result = engine.run(snaps)
        assert result.total_trades == 1
        assert result.closed[0].held_hours >= Decimal(str(11/60 - 0.001))


# ---------------------------------------------------------------------------
# Peak dropoff（防接飞刀）
# ---------------------------------------------------------------------------


class TestPeakDropoff:
    def test_no_dropoff_blocks_entry(self):
        cfg = _config(
            peak_window_minutes=Decimal("10"),
            min_peak_dropoff_pct=Decimal("0.05"),
        )
        engine = SpotPerpBacktestEngine(cfg)
        # 单笔基差 0.40% → cache 写入后 dropoff=0 < 0.05 → 拒绝
        result = engine.run([_snap(0, 100, 100.40)])
        # 0 笔成交 + 1 笔被 dropoff 拒
        assert result.total_trades == 0
        assert result.rejected_count >= 1

    def test_dropoff_allows_entry_after_peak(self):
        cfg = _config(
            peak_window_minutes=Decimal("10"),
            min_peak_dropoff_pct=Decimal("0.05"),
        )
        engine = SpotPerpBacktestEngine(cfg)
        # ETH 基差先冲 0.80%（peak 拒）然后回落到 0.40%（dropoff 0.40 ≥ 0.05 → 开）
        snaps = [
            _snap(0, 200, 201.6, sym="ETH/USDT"),
            _snap(2, 200, 200.80, sym="ETH/USDT"),
        ]
        result = engine.run(snaps)
        assert result.total_trades == 1   # 末尾 force-close
        assert result.rejected_count >= 1  # peak 时刻被拒


# ---------------------------------------------------------------------------
# Result 指标
# ---------------------------------------------------------------------------


class TestResultMetrics:
    def test_full_cycle_pnl_and_metrics(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        snaps = [
            _snap(0, 100, 100.40),     # 入场 premium 0.40%
            _snap(10, 100, 100.05),    # 收敛平仓
            _snap(20, 100, 100.40),    # 第二笔入场（同 symbol）
            _snap(30, 100, 100.05),    # 收敛平仓
        ]
        result = engine.run(snaps)
        assert result.total_trades == 2
        s = result.summary()
        assert "total_pnl_usd" in s
        assert s["total_trades"] == 2
        assert "by_exit_reason" in s

    def test_force_close_at_end(self):
        cfg = _config()
        engine = SpotPerpBacktestEngine(cfg)
        # 入场后没有任何退出条件触发 → 末尾强平
        result = engine.run([
            _snap(0, 100, 100.40),
        ])
        assert result.total_trades == 1
        assert result.closed[0].exit_reason == "final_force_close"
