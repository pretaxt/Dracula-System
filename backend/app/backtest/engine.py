"""回测引擎

时间驱动主循环：按历史 FundingPeriod 序列逐期推进，模拟
Delta 中性套利策略（现货多 + 永续空）的完整生命周期。

每个结算周期依次执行：
  1. 对已开仓位结算资金费率收入
  2. 风控检查 → 触发止损/超时则平仓
  3. 若有新机会且风控通过 → 开仓
  4. 更新资金曲线

回测不写数据库（PositionManager.save 被跳过），所有状态保存在内存中。

用法::

    config = BacktestConfig(
        symbol=Symbol("BTC", "USDT"),
        exchange="binance",
        initial_capital_usd=Decimal("10000"),
        size_per_trade_usd=Decimal("500"),
        risk_limits=RiskLimits(...),
    )
    engine = BacktestEngine(config)
    result = await engine.run(periods)
    print(result.total_return_pct, result.sharpe_ratio)
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence
from unittest.mock import AsyncMock

from app.backtest.models import BacktestConfig, BacktestResult, EquityPoint, FundingPeriod
from app.core.logging import get_logger
from app.exchanges.models import FundingRate, OrderBook, Side
from app.execution.order_executor import OrderExecutor
from app.execution.paper_broker import PaperBroker
from app.risk.limits import RiskGuard, RiskLimitError
from app.risk.models import ExitReason, Position
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.scanner import FundingRateOpportunity

logger = get_logger(__name__)

_ZERO = Decimal("0")


class BacktestEngine:
    """资金费率套利策略回测引擎。

    Parameters
    ----------
    config:
        回测配置（起始资金、每笔规模、风控参数等）。
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self._broker = PaperBroker(
            slippage_bps=config.slippage_bps,
            fee_rate=config.fee_rate,
        )
        self._manager = PositionManager()
        # 回测不写 DB：将 save 替换为空操作
        self._manager.save = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self._guard = RiskGuard(limits=config.risk_limits)
        self._executor = OrderExecutor(
            broker=self._broker,
            manager=self._manager,
            guard=self._guard,
        )

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def run(self, periods: Sequence[FundingPeriod]) -> BacktestResult:
        """执行回测，返回完整结果。

        Parameters
        ----------
        periods:
            历史资金费率序列（引擎内部会按 timestamp 升序排序）。
        """
        sorted_periods = sorted(periods, key=lambda p: p.timestamp)
        result = BacktestResult(config=self._config)

        for period in sorted_periods:
            await self._settle_funding(period)
            await self._check_funding_flip_exit(period)
            await self._check_and_close_violations(period)
            await self._try_open(period)
            equity = self._compute_equity(period)
            result.equity_curve.append(
                EquityPoint(
                    timestamp=period.timestamp,
                    equity_usd=equity,
                    open_positions=len(self._manager.open_positions),
                )
            )

        # 回测结束：强制平掉剩余开仓（以最后一期价格结算）
        if sorted_periods:
            last = sorted_periods[-1]
            for pos in list(self._manager.open_positions):
                await self._close(pos, last, ExitReason.MANUAL)

        result.all_positions = list(self._manager.all_positions)
        return result

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    async def _settle_funding(self, period: FundingPeriod) -> None:
        """对每个开仓仓位结算当期资金费率。

        Delta 中性策略：永续空单在 rate > 0 时收取资金费率。
        funding_income = perp_leg.size × period.perp_price × period.funding_rate
        """
        for pos in self._manager.open_positions:
            perp_leg = next((l for l in pos.legs if l.side == Side.SELL), None)
            if perp_leg is None:
                continue
            funding = perp_leg.size * period.perp_price * period.funding_rate
            self._manager.record_funding(pos.id, funding)

    async def _check_funding_flip_exit(self, period: FundingPeriod) -> None:
        """资金费率翻负保护：当前周期费率 <= 0 时，平掉该 symbol 的所有开仓。

        理由：策略只在正费率下盈利；持仓期间费率翻负会持续支付资金费给多头，
        早退出可避免后续负费率累计损失。
        """
        if period.funding_rate > _ZERO:
            return
        for pos in list(self._manager.open_positions):
            if pos.symbol != period.symbol:
                continue
            await self._close(pos, period, ExitReason.FUNDING_REVERSAL)

    async def _check_and_close_violations(self, period: FundingPeriod) -> None:
        """风控检查，对触发规则的仓位执行平仓。

        使用模拟周期时间戳作为 as_of，确保 max_hold_hours
        按历史时间计算而非系统时钟。
        """
        flagged = self._executor.check_all_positions(as_of=period.timestamp)
        for pos, violations in flagged:
            reason = self._violation_to_reason(violations)
            await self._close(pos, period, reason)

    async def _try_open(self, period: FundingPeriod) -> None:
        """若当期资金费率满足进场条件，尝试开新仓；风控拦截时静默跳过。

        与生产 paper trading 一致：同一 symbol 已有开仓则跳过（避免并行重复仓位）。
        开仓成功后将 opened_at 修正为模拟时间戳，确保 holding_hours
        以历史时间而非系统时钟计算。
        """
        if self._manager.get_by_symbol(period.symbol):
            return
        opportunity = self._make_opportunity(period)
        try:
            pos = await self._executor.open_delta_neutral(
                opportunity,
                size_usd=self._config.size_per_trade_usd,
            )
            # 回测时间修正：用模拟周期时间替代系统时钟
            pos.opened_at = period.timestamp
        except RiskLimitError:
            pass  # 风控拦截，本期跳过

    async def _close(
        self, pos: Position, period: FundingPeriod, reason: ExitReason
    ) -> None:
        """执行平仓，安全处理仓位已关闭的情况。"""
        try:
            await self._executor.close_position(pos.id, reason=reason)
        except KeyError:
            pass  # 已平仓，安全忽略

    def _compute_equity(self, period: FundingPeriod) -> Decimal:
        """当期权益 = 初始资金 + Σ(已实现盈亏 + 资金费 − 手续费 + 未实现盈亏)。"""
        equity = self._config.initial_capital_usd
        for pos in self._manager.all_positions:
            equity += pos.realized_pnl + pos.funding_received - pos.fees_paid
            if pos.is_open:
                equity += self._unrealized_pnl(pos, period)
        return equity

    @staticmethod
    def _unrealized_pnl(pos: Position, period: FundingPeriod) -> Decimal:
        """按当期价格 mark-to-market 计算未实现盈亏。"""
        pnl = _ZERO
        for leg in pos.legs:
            if leg.side == Side.BUY:
                pnl += (period.spot_price - leg.entry_price) * leg.size
            else:
                pnl += (leg.entry_price - period.perp_price) * leg.size
        return pnl

    def _make_opportunity(self, period: FundingPeriod) -> FundingRateOpportunity:
        """从历史快照构造 FundingRateOpportunity 供执行器使用。"""
        funding_rate_obj = FundingRate(
            symbol=period.symbol,
            exchange=period.exchange,
            rate=period.funding_rate,
            next_funding_time=int(period.timestamp.timestamp() * 1000),
            funding_interval_hours=self._config.funding_interval_hours,
        )
        spot_ob = OrderBook(
            symbol=period.symbol,
            bids=[(period.spot_price - Decimal("1"), Decimal("10"))],
            asks=[(period.spot_price, Decimal("10"))],
            timestamp=int(period.timestamp.timestamp() * 1000),
        )
        perp_ob = OrderBook(
            symbol=period.symbol,
            bids=[(period.perp_price, Decimal("10"))],
            asks=[(period.perp_price + Decimal("1"), Decimal("10"))],
            timestamp=int(period.timestamp.timestamp() * 1000),
        )
        return FundingRateOpportunity(
            symbol=period.symbol,
            exchange=period.exchange,
            funding_rate=funding_rate_obj,
            spot_orderbook=spot_ob,
            perp_orderbook=perp_ob,
        )

    @staticmethod
    def _violation_to_reason(violations: list) -> ExitReason:
        """将风控违规类型映射为平仓原因。"""
        rules = {v.rule for v in violations}
        # P1-6 新增 total_pnl_stop_loss（含 funding+fees 兜底），与价格止损同样映射 STOP_LOSS
        if "stop_loss_pct" in rules or "total_pnl_stop_loss" in rules:
            return ExitReason.STOP_LOSS
        if "max_hold_hours" in rules:
            return ExitReason.MAX_HOLD_TIME
        return ExitReason.RISK_LIMIT
