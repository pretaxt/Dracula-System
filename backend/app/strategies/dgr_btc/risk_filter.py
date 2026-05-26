"""
dgr_btc/risk_filter.py
======================
风控引擎 — 7 维度市场/组合状态检查, 触发风控等级。

risk 规则（来自 Codex config.risk 段, doc 没列在 §2 但实际生效）:
  - trend_grids_threshold = 5  ⭐ 核心 alpha 保护
  - hourly_vol_threshold = 1.5  → DEFENSIVE (只减仓不加仓)
  - margin_ratio_min = 0.35
  - funding_filter_enabled = True
  - max_daily_loss_pct = 0.05 → EMERGENCY pause
  - max_drawdown_pct = 0.15 → EMERGENCY pause

风控原则: fail-safe, 触发后默认收缩而非扩张。

不 import hedged_grid 任何代码（完全独立）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from app.strategies.dgr_btc.types import MarketState, PortfolioSnapshot


_ZERO = Decimal("0")


class RiskLevel(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"        # 减速
    DEFENSIVE = "DEFENSIVE"    # 只减仓不加仓
    EMERGENCY = "EMERGENCY"    # 紧急停机


class RiskTrigger(str, Enum):
    TREND_BREAKOUT = "TREND_BREAKOUT"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_MARGIN = "LOW_MARGIN"
    NEGATIVE_FUNDING = "NEGATIVE_FUNDING"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"


@dataclass
class RiskReport:
    level: RiskLevel
    triggers: list[RiskTrigger] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def should_pause(self) -> bool:
        return self.level == RiskLevel.EMERGENCY

    @property
    def can_open(self) -> bool:
        return self.level in (RiskLevel.NORMAL, RiskLevel.CAUTION)

    @property
    def can_close(self) -> bool:
        return self.level != RiskLevel.EMERGENCY


class RiskFilter:
    """多维度风控检查（每 tick 由 strategy_core 调 evaluate）。"""

    def __init__(
        self,
        trend_grids_threshold: int,
        hourly_vol_threshold: Decimal,
        margin_ratio_min: Decimal,
        funding_filter_enabled: bool,
        funding_threshold: Decimal,
        min_orderbook_depth: Decimal,
        max_daily_loss_pct: Decimal,
        max_drawdown_pct: Decimal,
        initial_equity: Decimal,
    ):
        self.trend_grids_threshold = trend_grids_threshold
        self.hourly_vol_threshold = hourly_vol_threshold
        self.margin_ratio_min = margin_ratio_min
        self.funding_filter_enabled = funding_filter_enabled
        self.funding_threshold = funding_threshold
        self.min_orderbook_depth = min_orderbook_depth
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct

        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.day_start_equity = initial_equity
        self.day_start_time = datetime.now(timezone.utc)
        self.last_report: Optional[RiskReport] = None

    def evaluate(
        self,
        snapshot: PortfolioSnapshot,
        market: MarketState,
        consecutive_trend_grids: int,
        margin_ratio: Decimal,
    ) -> RiskReport:
        triggers: list[RiskTrigger] = []
        messages: list[str] = []

        # 1. 趋势
        if consecutive_trend_grids >= self.trend_grids_threshold:
            triggers.append(RiskTrigger.TREND_BREAKOUT)
            messages.append(f"连续 {consecutive_trend_grids} 格单向移动")

        # 2. 高波动
        if market.realized_vol_1h > self.hourly_vol_threshold:
            triggers.append(RiskTrigger.HIGH_VOLATILITY)
            messages.append(
                f"1H 波动 {market.realized_vol_1h:.2%} > "
                f"阈值 {self.hourly_vol_threshold:.2%}"
            )

        # 3. 保证金
        if margin_ratio < self.margin_ratio_min:
            triggers.append(RiskTrigger.LOW_MARGIN)
            messages.append(
                f"保证金率 {margin_ratio:.2%} < {self.margin_ratio_min:.2%}"
            )

        # 4. Funding
        if (
            self.funding_filter_enabled
            and market.funding_rate < self.funding_threshold
        ):
            triggers.append(RiskTrigger.NEGATIVE_FUNDING)
            messages.append(f"Funding {market.funding_rate:.4%} 低于阈值")

        # 5. 流动性
        if market.bid_depth_usdt > 0 and market.ask_depth_usdt > 0:
            min_depth = min(market.bid_depth_usdt, market.ask_depth_usdt)
            if min_depth < self.min_orderbook_depth:
                triggers.append(RiskTrigger.LOW_LIQUIDITY)
                messages.append(f"盘口深度不足 ${min_depth:,.0f}")

        # 6. 日内亏损（按 UTC 日期切片）
        now = datetime.now(timezone.utc)
        if now.date() > self.day_start_time.date():
            self.day_start_equity = snapshot.total_equity
            self.day_start_time = now

        if self.day_start_equity > 0:
            daily_loss = (
                snapshot.total_equity - self.day_start_equity
            ) / self.day_start_equity
            if daily_loss < -self.max_daily_loss_pct:
                triggers.append(RiskTrigger.DAILY_LOSS_LIMIT)
                messages.append(f"单日亏损 {daily_loss:.2%}")

        # 7. 最大回撤
        if snapshot.total_equity > self.peak_equity:
            self.peak_equity = snapshot.total_equity
        if self.peak_equity > 0:
            drawdown = (
                snapshot.total_equity - self.peak_equity
            ) / self.peak_equity
            if drawdown < -self.max_drawdown_pct:
                triggers.append(RiskTrigger.MAX_DRAWDOWN)
                messages.append(f"累计回撤 {drawdown:.2%}")

        level = self._compute_level(triggers)
        report = RiskReport(level=level, triggers=triggers, messages=messages)
        self.last_report = report
        return report

    def _compute_level(self, triggers: list[RiskTrigger]) -> RiskLevel:
        emergency = {RiskTrigger.DAILY_LOSS_LIMIT, RiskTrigger.MAX_DRAWDOWN}
        if any(t in emergency for t in triggers):
            return RiskLevel.EMERGENCY
        defensive = {RiskTrigger.LOW_MARGIN, RiskTrigger.HIGH_VOLATILITY}
        if any(t in defensive for t in triggers):
            return RiskLevel.DEFENSIVE
        if triggers:
            return RiskLevel.CAUTION
        return RiskLevel.NORMAL

    def reset_trend_state(self) -> None:
        """趋势恢复后由 GridManager.reset_trend() 处理（这里占位）。"""
