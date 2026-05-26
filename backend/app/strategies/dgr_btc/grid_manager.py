"""
dgr_btc/grid_manager.py
=======================
动态网格管理器 — dynamic bounds + recenter rebuild。

vs hedged_grid GridManager 核心增量:
  ✅ `rebuild_around(new_center, width_pct, step)` 方法
     recenter 时按新 center 重算 bounds, 重建 levels,
     重置 last_price / last_grid_index / trend 状态

不 import hedged_grid 任何代码 (完全独立)。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Optional

from app.strategies.dgr_btc.types import GridLevel, Side


_ZERO = Decimal("0")
_ONE = Decimal("1")


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """math.floor(value/step)*step using Decimal — mirror Codex apply_dynamic_grid."""
    return (value / step).quantize(_ONE, rounding=ROUND_FLOOR) * step


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """math.ceil(value/step)*step using Decimal — mirror Codex apply_dynamic_grid."""
    return (value / step).quantize(_ONE, rounding=ROUND_CEILING) * step


@dataclass
class GridTrigger:
    """网格触发事件 — strategy_core 据此生成 OrderIntent。"""

    from_price: Decimal
    to_price: Decimal
    crossed_grids: list[Decimal]  # 被穿越的网格价位
    direction: Side  # SELL=上穿, BUY=下穿
    n_grids: int


class GridManager:
    """动态网格状态管理器（支持 recenter 重建）。

    paired_inverse 核心机制 (single-grid pairing):
      - 每个网格价位 P 维护"对手挂单对" (SELL@P 高位 + BUY@P 低位)
      - 上穿 P → SELL@P 成交 → 翻转为 BUY@P 等价格回落
      - 下穿 P → BUY@P 成交 → 翻转为 SELL@P 等价格回升
      - 关键: 成交价 = grid_price, 与穿越方向匹配

    Dynamic bounds:
      - 启动: lower = center*(1-width_pct), upper = center*(1+width_pct)
      - recenter (12% deviation triggered):
        撤旧挂单 → 重算 center/lower/upper → 重建 levels → reset 趋势
    """

    def __init__(
        self,
        lower_bound: Decimal,
        upper_bound: Decimal,
        step_usdt: Decimal,
        qty_per_grid: Decimal,
    ):
        if upper_bound <= lower_bound or step_usdt <= 0:
            raise ValueError("网格参数非法")
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound
        self.step_usdt = step_usdt
        self.qty_per_grid = qty_per_grid
        # 构造档位
        self.levels: list[GridLevel] = self._build_levels()
        # 价格状态
        self.last_price: Optional[Decimal] = None
        self.last_grid_index: Optional[int] = None
        # 趋势监测
        self.consecutive_direction_grids: int = 0
        self.last_direction: Optional[Side] = None

    @classmethod
    def from_center(
        cls,
        center: Decimal,
        width_pct: Decimal,
        step_usdt: Decimal,
        qty_per_grid: Decimal,
    ) -> "GridManager":
        """按 center × width_pct 构造（doc §3 启动逻辑）.

        ⭐ 关键: 必须用 floor/ceil 把 lower/upper 取整到 step 整数倍
        (mirror Codex apply_dynamic_grid 行为), 否则 grid levels 会偏移于
        整数 step lattice, 导致大量穿越识别错误。
        """
        raw_lower = center * (_ONE - width_pct)
        raw_upper = center * (_ONE + width_pct)
        lower = _floor_to_step(raw_lower, step_usdt)
        upper = _ceil_to_step(raw_upper, step_usdt)
        return cls(
            lower_bound=lower,
            upper_bound=upper,
            step_usdt=step_usdt,
            qty_per_grid=qty_per_grid,
        )

    def _build_levels(self) -> list[GridLevel]:
        levels = []
        price = self.lower_bound
        idx = 0
        eps = Decimal("0.000001")
        while price <= self.upper_bound + eps:
            levels.append(GridLevel(price=price.quantize(Decimal("0.01")), index=idx))
            price += self.step_usdt
            idx += 1
        return levels

    def n_grids(self) -> int:
        return len(self.levels) - 1

    def find_grid_index(self, price: Decimal) -> int:
        """价格所属网格区间索引（向下取整）。"""
        if price <= self.lower_bound:
            return 0
        if price >= self.upper_bound:
            return len(self.levels) - 1
        return int((price - self.lower_bound) / self.step_usdt)

    def update_price(self, new_price: Decimal) -> Optional[GridTrigger]:
        """接收新价格 → 返回触发事件（如有穿越）。"""
        if self.last_price is None:
            self.last_price = new_price
            self.last_grid_index = self.find_grid_index(new_price)
            return None

        new_idx = self.find_grid_index(new_price)

        # 未穿越
        if new_idx == self.last_grid_index:
            return None

        # 计算被穿越的网格价位
        assert self.last_grid_index is not None  # ensured above
        if new_idx > self.last_grid_index:
            crossed = [
                self.levels[i].price
                for i in range(self.last_grid_index + 1, new_idx + 1)
            ]
            direction = Side.SELL
        else:
            crossed = [
                self.levels[i].price
                for i in range(self.last_grid_index - 1, new_idx - 1, -1)
            ]
            direction = Side.BUY

        n_crossed = abs(new_idx - self.last_grid_index)

        # 趋势计数
        if self.last_direction == direction:
            self.consecutive_direction_grids += n_crossed
        else:
            self.consecutive_direction_grids = n_crossed
            self.last_direction = direction

        trigger = GridTrigger(
            from_price=self.last_price,
            to_price=new_price,
            crossed_grids=crossed,
            direction=direction,
            n_grids=n_crossed,
        )

        # 标记 fill_count + last_filled_side
        eps = Decimal("0.000001")
        for p in crossed:
            for lvl in self.levels:
                if abs(lvl.price - p) < eps:
                    lvl.fill_count += 1
                    lvl.last_filled_side = direction
                    break

        self.last_price = new_price
        self.last_grid_index = new_idx
        return trigger

    def rebuild_around(
        self,
        new_center: Decimal,
        width_pct: Decimal,
    ) -> None:
        """Recenter 重建（doc §10）:
        - 撤旧挂单（在 paper/live broker 层处理；这里只动 grid 状态）
        - 重算 lower / upper （floor/ceil 到 step 整数倍, mirror Codex）
        - 重建 levels
        - 重置 trend 计数 + last_grid_index
        - last_price 同步到 new_center（避免 next tick 误判穿越）
        """
        raw_lower = new_center * (_ONE - width_pct)
        raw_upper = new_center * (_ONE + width_pct)
        self.lower_bound = _floor_to_step(raw_lower, self.step_usdt)
        self.upper_bound = _ceil_to_step(raw_upper, self.step_usdt)
        self.levels = self._build_levels()
        self.last_price = new_center
        self.last_grid_index = self.find_grid_index(new_center)
        self.reset_trend()

    def is_trending(self, threshold: int) -> bool:
        return self.consecutive_direction_grids >= threshold

    def reset_trend(self) -> None:
        self.consecutive_direction_grids = 0
        self.last_direction = None

    def stats(self) -> dict:
        total_fills = sum(lvl.fill_count for lvl in self.levels)
        active_levels = sum(1 for lvl in self.levels if lvl.fill_count > 0)
        return {
            "n_grids": self.n_grids(),
            "step": str(self.step_usdt),
            "total_fills": total_fills,
            "active_levels": active_levels,
            "last_price": str(self.last_price) if self.last_price else None,
            "trend_count": self.consecutive_direction_grids,
            "lower_bound": str(self.lower_bound),
            "upper_bound": str(self.upper_bound),
        }
