"""#04 期现套利回测数据模型

`BasisSnapshot` 是单个时间点 (symbol, exchange) 的现货+永续价格快照，
回测引擎按时间序列推进，模拟 entry/exit 决策与盈亏。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ---------------------------------------------------------------------------
# 历史数据点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BasisSnapshot:
    """单个时间点的现货 + 永续价格快照。

    回测引擎按 timestamp 升序遍历，每个 timestamp 可能有多个 symbol 的快照。
    """

    timestamp: datetime
    symbol: str               # "BTC/USDT"
    exchange: str             # "binance" / "okx"
    spot_price: Decimal
    perp_price: Decimal

    @property
    def basis_abs(self) -> Decimal:
        return self.perp_price - self.spot_price

    @property
    def basis_pct(self) -> Decimal:
        if self.spot_price <= 0:
            return _ZERO
        return (self.perp_price - self.spot_price) / self.spot_price * _HUNDRED

    @property
    def direction(self) -> str:
        """+ → premium（perp > spot），- → discount（perp < spot）。"""
        return "premium" if self.perp_price >= self.spot_price else "discount"


# ---------------------------------------------------------------------------
# 回测配置
# ---------------------------------------------------------------------------


@dataclass
class SpotPerpBacktestConfig:
    """spot_perp 回测参数（与生产 SpotPerpStrategyConfig 对齐）。"""

    initial_capital_usd: Decimal = Decimal("1000")
    notional_per_position: Decimal = Decimal("50")
    max_concurrent: int = 2
    # 入场
    entry_pct: Decimal = Decimal("0.30")             # 兜底
    entry_pct_premium: Decimal = Decimal("0")        # 0 = 回退用 entry_pct
    entry_pct_discount: Decimal = Decimal("0")
    direction_filter: str = "both"                   # "premium" | "discount" | "both"
    # 入场时机过滤（防接飞刀）
    peak_window_minutes: Decimal = Decimal("10")
    min_peak_dropoff_pct: Decimal = Decimal("0.05")
    # 退出
    exit_pct: Decimal = Decimal("0.10")              # 收敛平仓
    max_hold_hours: Decimal = Decimal("12")
    min_hold_minutes: Decimal = Decimal("5")
    stop_basis_widening_pct: Decimal = Decimal("0.50")
    # 执行成本
    slippage_pct: Decimal = Decimal("0.10")          # 单边滑点 0.10%
    fee_rate: Decimal = Decimal("0.0004")            # 单边 taker

    def entry_threshold(self, direction: str) -> Decimal:
        d = (direction or "").lower()
        if d == "discount" and self.entry_pct_discount > 0:
            return self.entry_pct_discount
        if d == "premium" and self.entry_pct_premium > 0:
            return self.entry_pct_premium
        return self.entry_pct


# ---------------------------------------------------------------------------
# 仓位
# ---------------------------------------------------------------------------


@dataclass
class SpotPerpTrade:
    """单笔回测交易（开仓 + 平仓）。"""

    symbol: str
    exchange: str
    direction: str               # premium / discount
    notional_usd: Decimal
    entry_time: datetime
    entry_basis_pct: Decimal
    entry_spot: Decimal
    entry_perp: Decimal
    exit_time: datetime | None = None
    exit_basis_pct: Decimal = _ZERO
    exit_spot: Decimal = _ZERO
    exit_perp: Decimal = _ZERO
    exit_reason: str = ""         # basis_convergence / basis_stop / max_hold / final_force_close
    fees_paid: Decimal = _ZERO    # 4 腿手续费 + 滑点
    realized_pnl: Decimal = _ZERO

    @property
    def held_hours(self) -> Decimal:
        if self.exit_time is None:
            return _ZERO
        return Decimal(str((self.exit_time - self.entry_time).total_seconds() / 3600))

    @property
    def is_closed(self) -> bool:
        return self.exit_time is not None


# ---------------------------------------------------------------------------
# 资金曲线
# ---------------------------------------------------------------------------


@dataclass
class SpotPerpEquityPoint:
    timestamp: datetime
    equity_usd: Decimal
    open_positions: int
    total_realized_pnl: Decimal
    total_unrealized_pnl: Decimal


# ---------------------------------------------------------------------------
# 回测结果
# ---------------------------------------------------------------------------


@dataclass
class SpotPerpBacktestResult:
    """spot_perp 回测完整结果。"""

    config: SpotPerpBacktestConfig
    trades: list[SpotPerpTrade] = field(default_factory=list)
    equity_curve: list[SpotPerpEquityPoint] = field(default_factory=list)
    rejected_count: int = 0       # 进入候选但被 dropoff/window 拒绝
    skipped_count: int = 0        # APR 不达标

    # ------------------------------------------------------------------
    # 统计指标
    # ------------------------------------------------------------------

    @property
    def closed(self) -> list[SpotPerpTrade]:
        return [t for t in self.trades if t.is_closed]

    @property
    def total_trades(self) -> int:
        return len(self.closed)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.closed if t.realized_pnl > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.closed if t.realized_pnl < 0)

    @property
    def win_rate_pct(self) -> Decimal:
        if not self.closed:
            return _ZERO
        return Decimal(str(self.winning_trades)) / Decimal(str(len(self.closed))) * _HUNDRED

    @property
    def total_pnl_usd(self) -> Decimal:
        return sum((t.realized_pnl for t in self.closed), _ZERO)

    @property
    def total_fees_usd(self) -> Decimal:
        return sum((t.fees_paid for t in self.closed), _ZERO)

    @property
    def avg_pnl_per_trade(self) -> Decimal:
        if not self.closed:
            return _ZERO
        return self.total_pnl_usd / Decimal(str(len(self.closed)))

    @property
    def avg_held_hours(self) -> Decimal:
        if not self.closed:
            return _ZERO
        return sum((t.held_hours for t in self.closed), _ZERO) / Decimal(str(len(self.closed)))

    @property
    def final_equity_usd(self) -> Decimal:
        if not self.equity_curve:
            return self.config.initial_capital_usd
        return self.equity_curve[-1].equity_usd

    @property
    def total_return_pct(self) -> Decimal:
        if self.config.initial_capital_usd <= 0:
            return _ZERO
        return (
            (self.final_equity_usd - self.config.initial_capital_usd)
            / self.config.initial_capital_usd
            * _HUNDRED
        )

    @property
    def max_drawdown_pct(self) -> Decimal:
        if not self.equity_curve:
            return _ZERO
        peak = self.equity_curve[0].equity_usd
        max_dd = _ZERO
        for p in self.equity_curve:
            if p.equity_usd > peak:
                peak = p.equity_usd
            if peak > 0:
                dd = (peak - p.equity_usd) / peak * _HUNDRED
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    @property
    def by_exit_reason(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.closed:
            out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
        return out

    def summary(self) -> dict:
        """flat dict 用于 JSON 序列化 / API 返回。"""
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": str(round(self.win_rate_pct, 2)),
            "total_pnl_usd": str(round(self.total_pnl_usd, 4)),
            "total_fees_usd": str(round(self.total_fees_usd, 4)),
            "avg_pnl_per_trade": str(round(self.avg_pnl_per_trade, 4)),
            "avg_held_hours": str(round(self.avg_held_hours, 2)),
            "final_equity_usd": str(round(self.final_equity_usd, 2)),
            "total_return_pct": str(round(self.total_return_pct, 4)),
            "max_drawdown_pct": str(round(self.max_drawdown_pct, 4)),
            "rejected_count": self.rejected_count,
            "skipped_count": self.skipped_count,
            "by_exit_reason": self.by_exit_reason,
        }
