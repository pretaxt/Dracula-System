"""回测数据模型

FundingPeriod   — 历史资金费率结算周期快照
BacktestConfig  — 回测配置参数
EquityPoint     — 资金曲线上的一个时间点
BacktestResult  — 回测完整结果（含绩效指标）
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.exchanges.models import Symbol
from app.risk.limits import RiskLimits
from app.risk.models import Position, PositionStatus

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


# ---------------------------------------------------------------------------
# 历史数据
# ---------------------------------------------------------------------------


@dataclass
class FundingPeriod:
    """单个资金费率结算周期的历史快照。

    通常每 8 小时一条（Binance 等交易所标准）。
    spot_price / perp_price 取结算时刻成交价，用于模拟订单簿。
    """

    symbol: Symbol
    exchange: str
    timestamp: datetime       # 结算时间（UTC）
    funding_rate: Decimal     # 当期资金费率，如 0.0001 = 0.01%
    spot_price: Decimal       # 结算时刻现货价
    perp_price: Decimal       # 结算时刻永续合约价

    @property
    def apr_pct(self) -> Decimal:
        """年化收益率百分比（8h 间隔 × 3 × 365 × 100）。"""
        return self.funding_rate * Decimal("3") * Decimal("365") * _HUNDRED


# ---------------------------------------------------------------------------
# 回测配置
# ---------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    """回测参数。

    Parameters
    ----------
    symbol:
        交易对，如 Symbol("BTC", "USDT")。
    exchange:
        交易所名称，如 "binance"。
    initial_capital_usd:
        起始资金（USD）。
    size_per_trade_usd:
        每笔仓位名义价值（USD）。
    risk_limits:
        复用 Week 5 RiskLimits。
    funding_interval_hours:
        资金费率结算间隔（小时），默认 8。
    slippage_bps:
        滑点（基点），默认 2 bps。
    fee_rate:
        手续费率（名义价值比例），默认 0.04%。
    """

    symbol: Symbol
    exchange: str
    initial_capital_usd: Decimal
    size_per_trade_usd: Decimal
    risk_limits: RiskLimits
    funding_interval_hours: int = 8
    slippage_bps: Decimal = Decimal("2")
    fee_rate: Decimal = Decimal("0.0004")


# ---------------------------------------------------------------------------
# 资金曲线
# ---------------------------------------------------------------------------


@dataclass
class EquityPoint:
    """资金曲线上的单个时间点。"""

    timestamp: datetime
    equity_usd: Decimal
    open_positions: int


# ---------------------------------------------------------------------------
# 回测结果
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """回测完整结果，包含资金曲线和绩效指标。

    Parameters
    ----------
    config:
        本次回测配置。
    equity_curve:
        按时间排序的资金曲线，每个结算周期一个点。
    all_positions:
        回测期间创建的全部仓位（含已平仓及仍开仓的）。
    """

    config: BacktestConfig
    equity_curve: list[EquityPoint] = field(default_factory=list)
    all_positions: list[Position] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 基础统计
    # ------------------------------------------------------------------

    @property
    def total_trades(self) -> int:
        """总开仓次数。"""
        return len(self.all_positions)

    @property
    def closed_positions(self) -> list[Position]:
        return [p for p in self.all_positions if p.status == PositionStatus.CLOSED]

    @property
    def total_fees_usd(self) -> Decimal:
        return sum((p.fees_paid for p in self.all_positions), _ZERO)

    @property
    def total_funding_usd(self) -> Decimal:
        return sum((p.funding_received for p in self.all_positions), _ZERO)

    # ------------------------------------------------------------------
    # 收益指标
    # ------------------------------------------------------------------

    @property
    def final_equity_usd(self) -> Decimal:
        if not self.equity_curve:
            return self.config.initial_capital_usd
        return self.equity_curve[-1].equity_usd

    @property
    def total_return_pct(self) -> Decimal:
        """总收益率（%）。"""
        if self.config.initial_capital_usd == _ZERO:
            return _ZERO
        return (
            (self.final_equity_usd - self.config.initial_capital_usd)
            / self.config.initial_capital_usd
            * _HUNDRED
        )

    @property
    def periods_days(self) -> Decimal:
        """回测跨越的天数。"""
        if len(self.equity_curve) < 2:
            return Decimal("1")
        delta = self.equity_curve[-1].timestamp - self.equity_curve[0].timestamp
        return Decimal(str(max(delta.total_seconds() / 86400, 1)))

    @property
    def annualized_return_pct(self) -> Decimal:
        """年化收益率（%），简单线性外推。"""
        return self.total_return_pct * Decimal("365") / self.periods_days

    # ------------------------------------------------------------------
    # 最大回撤
    # ------------------------------------------------------------------

    @property
    def max_drawdown_pct(self) -> Decimal:
        """最大回撤（%）。"""
        if not self.equity_curve:
            return _ZERO
        peak = self.equity_curve[0].equity_usd
        max_dd = _ZERO
        for pt in self.equity_curve:
            if pt.equity_usd > peak:
                peak = pt.equity_usd
            if peak > _ZERO:
                dd = (peak - pt.equity_usd) / peak * _HUNDRED
                if dd > max_dd:
                    max_dd = dd
        return max_dd

    # ------------------------------------------------------------------
    # 夏普比率
    # ------------------------------------------------------------------

    @property
    def sharpe_ratio(self) -> Decimal:
        """夏普比率（无风险利率 = 0，基于每期权益变化率）。

        年化因子 = sqrt(periods_per_year)，8h 间隔时为 sqrt(1095)。
        """
        if len(self.equity_curve) < 2:
            return _ZERO
        returns: list[float] = []
        for i in range(1, len(self.equity_curve)):
            prev = float(self.equity_curve[i - 1].equity_usd)
            curr = float(self.equity_curve[i].equity_usd)
            if prev > 0:
                returns.append((curr - prev) / prev)
        if len(returns) < 2:
            return _ZERO
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(variance)
        if std == 0:
            return _ZERO
        periods_per_year = math.sqrt(24 / self.config.funding_interval_hours * 365)
        try:
            return Decimal(str(round(mean / std * periods_per_year, 4)))
        except InvalidOperation:
            return _ZERO

    # ------------------------------------------------------------------
    # 胜率
    # ------------------------------------------------------------------

    @property
    def win_rate_pct(self) -> Decimal:
        """已平仓仓位的胜率（%）。净盈利 = realized_pnl + funding - fees > 0 为赢。"""
        closed = self.closed_positions
        if not closed:
            return _ZERO
        wins = sum(
            1
            for p in closed
            if (p.realized_pnl + p.funding_received - p.fees_paid) > _ZERO
        )
        return Decimal(str(wins)) / Decimal(str(len(closed))) * _HUNDRED
