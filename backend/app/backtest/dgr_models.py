"""
backtest/dgr_models.py
======================
dgr_btc 回测结果数据类。

mirror hedged_grid_models pattern: 不重新实现策略, 用 DgrBtcStrategy 作 SUT,
回测引擎只做 K线 replay + fill simulation + funding 8h settlement。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.strategies.dgr_btc.types import (
    PortfolioSnapshot,
    Position,
    Trade,
)


_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


@dataclass
class EquityPoint:
    """每根 K (或采样) 的组合净值快照。"""

    timestamp: datetime
    equity_usdt: Decimal
    cash: Decimal
    spot_qty: Decimal
    perp_qty: Decimal  # 永续净仓位（空为负）
    delta: Decimal
    mark_price: Decimal


@dataclass
class RecenterRecord:
    """Recenter 事件记录（来自 strategy.recenter_events 镜像）。"""

    timestamp: datetime
    old_center: Decimal
    new_center: Decimal
    deviation_pct: Decimal


@dataclass
class DgrBtcBacktestResult:
    config_snapshot: dict  # config dump
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    recenters: list[RecenterRecord] = field(default_factory=list)
    initial_equity: Decimal = _ZERO
    final_equity: Decimal = _ZERO
    funding_paid: Decimal = _ZERO
    total_fees: Decimal = _ZERO
    n_grid_triggers: int = 0
    n_trend_pauses: int = 0
    final_spot_pos: Position | None = None
    final_perp_pos: Position | None = None

    def summary(self) -> dict:
        """统一 metrics 输出（mirror Codex BacktestEngine._compute_metrics 字段）。"""
        init = self.initial_equity
        pnl = self.final_equity - init
        ret_pct = (pnl / init * _HUNDRED) if init > 0 else _ZERO

        # max drawdown
        peak = init
        max_dd = _ZERO
        for p in self.equity_curve:
            if p.equity_usdt > peak:
                peak = p.equity_usdt
            if peak > 0:
                dd = (p.equity_usdt - peak) / peak * _HUNDRED
                if dd < max_dd:
                    max_dd = dd

        # ndays + 复利年化（与 Codex 一致）
        n_days = 0.0
        if len(self.equity_curve) >= 2:
            delta = self.equity_curve[-1].timestamp - self.equity_curve[0].timestamp
            n_days = max(delta.total_seconds() / 86400, 1.0)
        if n_days > 0 and self.final_equity > 0 and init > 0:
            from math import pow as mpow

            try:
                ratio = float(self.final_equity / init)
                ann_pct = (mpow(ratio, 365.0 / n_days) - 1.0) * 100.0
            except (OverflowError, ValueError):
                ann_pct = float("nan")
        else:
            ann_pct = 0.0

        n_recenters = len(self.recenters)
        return {
            "n_trades": len(self.trades),
            "initial_equity_usdt": str(round(init, 2)),
            "final_equity_usdt": str(round(self.final_equity, 2)),
            "total_pnl_usdt": str(round(pnl, 2)),
            "return_pct": str(round(ret_pct, 4)),
            "annualized_pct": str(round(Decimal(str(ann_pct)), 4)),
            "max_drawdown_pct": str(round(max_dd, 4)),
            "funding_paid_usdt": str(round(self.funding_paid, 4)),
            "total_fees_usdt": str(round(self.total_fees, 4)),
            "n_grid_triggers": self.n_grid_triggers,
            "n_trend_pauses": self.n_trend_pauses,
            "n_recenters": n_recenters,
            "n_days": round(n_days, 2),
            "final_spot_qty": (
                str(round(self.final_spot_pos.quantity, 6))
                if self.final_spot_pos
                else "0"
            ),
            "final_perp_qty": (
                str(round(self.final_perp_pos.quantity, 6))
                if self.final_perp_pos
                else "0"
            ),
            "final_delta": (
                str(
                    round(
                        self.final_spot_pos.quantity
                        - abs(self.final_perp_pos.quantity),
                        6,
                    )
                )
                if (
                    self.final_spot_pos
                    and self.final_perp_pos
                    and self.final_perp_pos.quantity < 0
                )
                else "0"
            ),
        }
