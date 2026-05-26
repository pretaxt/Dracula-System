"""Tests for dgr_btc.grid_manager — build + update_price + recenter rebuild + trend."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.strategies.dgr_btc.grid_manager import GridManager, GridTrigger
from app.strategies.dgr_btc.types import Side


def test_build_levels_basic():
    """从 center=80000, width=15%, step=250 构造 levels."""
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    # bounds: 68000 .. 92000 (80000 ± 15%)
    assert grid.lower_bound == Decimal("68000")
    assert grid.upper_bound == Decimal("92000")
    # 总 levels: (92000-68000)/250 + 1 = 97
    assert len(grid.levels) == 97
    # 第一档 = lower_bound
    assert grid.levels[0].price == Decimal("68000.00")
    # 最后一档 = upper_bound
    assert grid.levels[-1].price == Decimal("92000.00")
    # step 正确
    assert grid.levels[1].price - grid.levels[0].price == Decimal("250.00")


def test_invalid_params():
    with pytest.raises(ValueError):
        GridManager(
            lower_bound=Decimal("100"),
            upper_bound=Decimal("50"),  # upper < lower
            step_usdt=Decimal("10"),
            qty_per_grid=Decimal("0.1"),
        )
    with pytest.raises(ValueError):
        GridManager(
            lower_bound=Decimal("50"),
            upper_bound=Decimal("100"),
            step_usdt=Decimal("0"),  # step <= 0
            qty_per_grid=Decimal("0.1"),
        )


def test_update_price_first_call_no_trigger():
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    trigger = grid.update_price(Decimal("80000"))
    assert trigger is None
    assert grid.last_price == Decimal("80000")


def test_update_price_up_cross():
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    grid.update_price(Decimal("80000"))
    trigger = grid.update_price(Decimal("80300"))
    assert trigger is not None
    assert trigger.direction == Side.SELL
    # 80000 → 80300 跨越了 80250 这一档
    assert Decimal("80250.00") in trigger.crossed_grids
    assert grid.consecutive_direction_grids == 1
    assert grid.last_direction == Side.SELL


def test_update_price_down_cross():
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    grid.update_price(Decimal("80000"))
    trigger = grid.update_price(Decimal("79700"))
    assert trigger is not None
    assert trigger.direction == Side.BUY
    # 80000 → 79700 跨越了 79750 这一档
    assert Decimal("79750.00") in trigger.crossed_grids
    assert grid.last_direction == Side.BUY


def test_trend_counter_increments_same_direction():
    """连续上穿 5 次 → consecutive = 5."""
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    grid.update_price(Decimal("80000"))
    grid.update_price(Decimal("80300"))  # +1 grid
    grid.update_price(Decimal("80550"))  # +1
    grid.update_price(Decimal("80800"))  # +1
    grid.update_price(Decimal("81050"))  # +1
    grid.update_price(Decimal("81300"))  # +1
    assert grid.consecutive_direction_grids == 5
    assert grid.is_trending(5) is True
    assert grid.is_trending(6) is False


def test_trend_counter_resets_on_reverse():
    """反向触发 → trend 计数 reset."""
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    grid.update_price(Decimal("80000"))
    grid.update_price(Decimal("80300"))  # up 1
    grid.update_price(Decimal("80550"))  # up 2
    grid.update_price(Decimal("80800"))  # up 3
    assert grid.consecutive_direction_grids == 3
    grid.update_price(Decimal("80500"))  # down → reset
    assert grid.consecutive_direction_grids == 1
    assert grid.last_direction == Side.BUY


def test_rebuild_around_recenter():
    """recenter rebuild: 重算 bounds + 重置 trend + last_price 同步。"""
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    # 制造 trending 状态
    grid.update_price(Decimal("80000"))
    grid.update_price(Decimal("80300"))
    grid.update_price(Decimal("80550"))
    assert grid.consecutive_direction_grids == 2
    # recenter to 90000
    grid.rebuild_around(new_center=Decimal("90000"), width_pct=Decimal("0.15"))
    # bounds: 76500 .. 103500
    assert grid.lower_bound == Decimal("76500")
    assert grid.upper_bound == Decimal("103500")
    # trend reset
    assert grid.consecutive_direction_grids == 0
    assert grid.last_direction is None
    # last_price 同步 new_center
    assert grid.last_price == Decimal("90000")
    # next tick 在 90250 应该触发 SELL pair（90000 → 90250 跨 1 格）
    trigger = grid.update_price(Decimal("90250"))
    assert trigger is not None
    assert trigger.direction == Side.SELL


def test_stats_dict_includes_bounds():
    grid = GridManager.from_center(
        center=Decimal("80000"),
        width_pct=Decimal("0.15"),
        step_usdt=Decimal("250"),
        qty_per_grid=Decimal("0.02"),
    )
    stats = grid.stats()
    # floor_to_step 后是 integer Decimal: "68000" 不带小数
    assert stats["lower_bound"] == "68000"
    assert stats["upper_bound"] == "92000"
    assert stats["trend_count"] == 0
