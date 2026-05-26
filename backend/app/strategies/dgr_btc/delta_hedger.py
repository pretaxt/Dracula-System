"""
dgr_btc/delta_hedger.py
=======================
Delta 对冲引擎 — 计算组合 Delta, 判断 SELL/BUY pair 是否合规。

doc §8 规则:
  - Delta = spot_qty - |short_qty|
  - 上限 +0.2 / 下限 -0.2
  - 每对操作 Delta 变化 ±0.04 (2 × qty_per_grid)
  - 触限则该方向整对跳过, 等价格反向

不 import hedged_grid 任何代码（完全独立）。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from app.strategies.dgr_btc.types import Position, Side


_ZERO = Decimal("0")


class HedgeAction(str, Enum):
    """对一笔意图操作的裁决。"""

    APPROVE = "APPROVE"
    SKIP_BUY = "SKIP_BUY"
    SKIP_SELL = "SKIP_SELL"
    SKIP_ADD_SHORT = "SKIP_ADD_SHORT"
    SKIP_CLOSE_SHORT = "SKIP_CLOSE_SHORT"
    REJECT_LIMIT = "REJECT_LIMIT"
    REJECT_TREND = "REJECT_TREND"


@dataclass
class DeltaState:
    spot_qty: Decimal
    short_qty: Decimal
    delta: Decimal
    delta_pct: Decimal
    within_limits: bool

    def __str__(self) -> str:
        return (
            f"Delta(spot={self.spot_qty:+.4f}, "
            f"short={self.short_qty:.4f}, "
            f"Δ={self.delta:+.4f}, {self.delta_pct:+.1%})"
        )


class DeltaHedger:
    """Delta 对冲器（doc §8）.

    paired_inverse 用法:
      - 触发 SELL pair (上穿) 前调 evaluate_sell_pair → 触限 skip
      - 触发 BUY pair (下穿) 前调 evaluate_buy_pair → 触限 skip
    """

    def __init__(
        self,
        upper_limit: Decimal,
        lower_limit: Decimal,
        max_spot: Decimal,
        max_short: Decimal,
        target: Decimal = _ZERO,
        rebalance_threshold: Decimal = Decimal("0.05"),
    ):
        self.upper_limit = upper_limit
        self.lower_limit = lower_limit
        self.max_spot = max_spot
        self.max_short = max_short
        self.target = target
        self.rebalance_threshold = rebalance_threshold

    def calc_state(
        self, spot_pos: Position, perp_pos: Position
    ) -> DeltaState:
        spot_qty = spot_pos.quantity
        short_qty = abs(perp_pos.quantity) if perp_pos.quantity < 0 else _ZERO
        delta = spot_qty - short_qty
        delta_pct = (delta / self.max_spot) if self.max_spot > 0 else _ZERO
        within = self.lower_limit <= delta <= self.upper_limit
        return DeltaState(
            spot_qty=spot_qty,
            short_qty=short_qty,
            delta=delta,
            delta_pct=delta_pct,
            within_limits=within,
        )

    def evaluate_buy_pair(
        self, spot_pos: Position, perp_pos: Position, qty: Decimal
    ) -> HedgeAction:
        """买入对: 买现货 + 减空（价格下跌触发）.

        Returns:
            REJECT_LIMIT: 现货 + qty 会超 max_spot
            SKIP_BUY: 操作后 delta 会超 upper_limit
            APPROVE: 通过
        """
        eps = Decimal("0.00000001")
        if spot_pos.quantity + qty > self.max_spot + eps:
            return HedgeAction.REJECT_LIMIT
        new_spot = spot_pos.quantity + qty
        if perp_pos.quantity < 0:
            new_short = max(_ZERO, abs(perp_pos.quantity) - qty)
        else:
            new_short = _ZERO
        new_delta = new_spot - new_short
        if new_delta > self.upper_limit:
            return HedgeAction.SKIP_BUY
        return HedgeAction.APPROVE

    def evaluate_sell_pair(
        self, spot_pos: Position, perp_pos: Position, qty: Decimal
    ) -> HedgeAction:
        """卖出对: 卖现货 + 加空（价格上涨触发）.

        Returns:
            REJECT_LIMIT: short + qty 会超 max_short
            SKIP_SELL: 现货不够卖 / delta 会跌破 lower_limit
            APPROVE: 通过
        """
        eps = Decimal("0.00000001")
        if perp_pos.quantity <= 0:
            new_short = abs(perp_pos.quantity) + qty
        else:
            new_short = qty
        if new_short > self.max_short + eps:
            return HedgeAction.REJECT_LIMIT
        if spot_pos.quantity - qty < -eps:
            return HedgeAction.SKIP_SELL
        new_spot = spot_pos.quantity - qty
        new_delta = new_spot - new_short
        if new_delta < self.lower_limit:
            return HedgeAction.SKIP_SELL
        return HedgeAction.APPROVE

    def needs_rebalance(self, state: DeltaState) -> bool:
        return abs(state.delta - self.target) > self.rebalance_threshold

    def rebalance_amount(self, state: DeltaState) -> tuple[Decimal, Side]:
        diff = state.delta - self.target
        if diff > 0:
            return abs(diff), Side.SELL
        else:
            return abs(diff), Side.BUY
