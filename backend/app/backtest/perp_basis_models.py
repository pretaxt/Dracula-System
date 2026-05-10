"""#02 跨所 funding 差套利回测数据模型

`PerpFundingSnapshot` 是单个时间点 (symbol, exchange) 的 funding rate 快照。
回测引擎按时间推进，每个 funding 结算周期检查跨所 diff_apr，开/平仓累计 PnL。

策略基本逻辑：
  - 同一 symbol，A 交易所 funding APR 高（多头付空头），B 低（甚至负）
  - 在 A 卖空 perp（收 funding），B 买多 perp（付小或收负 funding）
  - delta-neutral 跨所对冲
  - 收 (apr_A - apr_B) × notional / 8760 每小时

退出：max_hold / diff_apr 转负 / 强制收敛
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ---------------------------------------------------------------------------
# 历史 funding 数据点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerpFundingSnapshot:
    """某交易所某 symbol 在某 funding 结算时刻的 rate + 价格。"""

    timestamp: datetime
    symbol: str               # "FIL/USDT"
    exchange: str             # "binance" / "okx" / ...
    funding_rate: Decimal     # per-period rate（如 0.0001 = 0.01%/period）
    funding_interval_hours: int   # 8 / 4 / 1
    perp_price: Decimal       # 该时刻 mark price（计算 notional + PnL 用）

    @property
    def apr_pct(self) -> Decimal:
        """funding APR (%) = rate × (8760 / interval_hours) × 100"""
        if self.funding_interval_hours <= 0:
            return _ZERO
        periods_per_year = Decimal("8760") / Decimal(str(self.funding_interval_hours))
        return self.funding_rate * periods_per_year * _HUNDRED


# ---------------------------------------------------------------------------
# 跨所机会（一个 symbol，2 个交易所对）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrossExchangeOpportunity:
    """同一 symbol 在两个交易所的 funding 差机会。

    long_exchange:  funding 低 / 负 → 买多 perp（付小或收负 funding）
    short_exchange: funding 高 → 卖空 perp（收 funding）
    diff_apr_pct = short_apr - long_apr （> 0 才有套利空间）
    """

    timestamp: datetime
    symbol: str
    long_exchange: str
    short_exchange: str
    long_apr_pct: Decimal
    short_apr_pct: Decimal
    long_perp_price: Decimal
    short_perp_price: Decimal

    @property
    def diff_apr_pct(self) -> Decimal:
        return self.short_apr_pct - self.long_apr_pct


# ---------------------------------------------------------------------------
# 回测配置
# ---------------------------------------------------------------------------


@dataclass
class PerpBasisBacktestConfig:
    """#02 perp_basis 回测参数。"""

    initial_capital_usd: Decimal = Decimal("1000")
    notional_per_position: Decimal = Decimal("50")
    max_concurrent: int = 3

    # 入场门槛（diff_apr_pct，例如 30 = 30% APR 差）
    min_diff_apr_pct: Decimal = Decimal("30.0")

    # 退出
    max_hold_hours: Decimal = Decimal("48.0")    # 最长持仓
    exit_diff_apr_pct: Decimal = Decimal("5.0")  # diff 衰减到 ≤ 此值就平
    min_hold_hours: Decimal = Decimal("4.0")     # 最少持仓（防 funding 周期切换噪音）

    # 风控（绝对值过滤极端 funding，常见数据脏点）
    max_abs_apr_pct: Decimal = Decimal("500.0")  # 单腿 APR 绝对值 > 此 → 过滤（防 TIA HTX -99% 异常）
    min_volume_24h_usd: Decimal = Decimal("10_000_000")

    # 执行成本（单边）
    fee_rate: Decimal = Decimal("0.0004")        # taker
    slippage_pct: Decimal = Decimal("0.05")      # perp 单边滑点 0.05%


# ---------------------------------------------------------------------------
# 单笔回测交易
# ---------------------------------------------------------------------------


@dataclass
class PerpBasisTrade:
    """单笔跨所对冲交易（long_exchange 多 + short_exchange 空）。"""

    symbol: str
    long_exchange: str
    short_exchange: str
    open_at: datetime
    notional_usd: Decimal
    long_entry_price: Decimal
    short_entry_price: Decimal
    entry_diff_apr_pct: Decimal

    # 实时累计
    funding_collected: Decimal = _ZERO    # 累计 funding 差收入
    fees_paid: Decimal = _ZERO            # open + close fees
    realized_pnl: Decimal = _ZERO

    # close 状态
    closed_at: datetime | None = None
    long_exit_price: Decimal | None = None
    short_exit_price: Decimal | None = None
    exit_reason: str | None = None        # "diff_decay" / "max_hold" / "manual"

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    @property
    def held_hours(self) -> Decimal:
        end = self.closed_at if self.is_closed else self.open_at
        delta = (end - self.open_at).total_seconds() / 3600
        return Decimal(str(round(delta, 4)))


# ---------------------------------------------------------------------------
# 回测结果汇总
# ---------------------------------------------------------------------------


@dataclass
class PerpBasisEquityPoint:
    timestamp: datetime
    equity_usd: Decimal


@dataclass
class PerpBasisBacktestResult:
    """完整回测输出。"""

    config: PerpBasisBacktestConfig
    trades: list[PerpBasisTrade] = field(default_factory=list)
    equity_curve: list[PerpBasisEquityPoint] = field(default_factory=list)
    final_equity_usd: Decimal = _ZERO
    total_funding_collected: Decimal = _ZERO
    total_fees_paid: Decimal = _ZERO

    @property
    def num_trades(self) -> int:
        return sum(1 for t in self.trades if t.is_closed)

    @property
    def win_rate_pct(self) -> Decimal:
        closed = [t for t in self.trades if t.is_closed]
        if not closed:
            return _ZERO
        wins = sum(1 for t in closed if t.realized_pnl + t.funding_collected - t.fees_paid > 0)
        return Decimal(str(round(wins / len(closed) * 100, 2)))

    @property
    def total_pnl_usd(self) -> Decimal:
        return self.final_equity_usd - self.config.initial_capital_usd

    @property
    def total_pnl_pct(self) -> Decimal:
        if self.config.initial_capital_usd <= 0:
            return _ZERO
        return self.total_pnl_usd / self.config.initial_capital_usd * _HUNDRED
