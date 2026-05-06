"""订单执行器

编排 Delta 中性仓位的完整开/平仓流程：

1. 开仓（open_delta_neutral）：
   - RiskGuard 检查通过 → 创建 Position → 执行现货多 + 永续空两条腿
     → 更新腿价格 → mark_open → 持久化

2. 平仓（close_position）：
   - 执行现货空 + 永续多（反向平仓单）→ 计算已实现盈亏
     → mark_closed → 持久化

3. 持仓监控（check_all_positions）：
   - 逐一调用 RiskGuard.check_position → 返回触发止损/超时的仓位列表

用法::

    executor = OrderExecutor(broker=PaperBroker(), manager=PositionManager(),
                             guard=RiskGuard())
    pos = await executor.open_delta_neutral(opportunity, size_usd=Decimal("500"))
    await executor.close_position(pos.id, ExitReason.FUNDING_REVERSAL)
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.core.logging import get_logger
from app.execution.paper_broker import OrderRequest, OrderResult, PaperBroker
from app.exchanges.models import InstrumentType, Side
from app.risk.limits import RiskGuard, RiskLimitError
from app.risk.models import ExitReason, Position, PositionLeg
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.scanner import FundingRateOpportunity

logger = get_logger(__name__)


class OrderExecutor:
    """将扫描机会转化为实际（或模拟）持仓的执行编排器。"""

    def __init__(
        self,
        broker: PaperBroker,
        manager: PositionManager,
        guard: RiskGuard,
        strategy_instance: str = "funding_rate_main",
    ) -> None:
        self._broker = broker
        self._manager = manager
        self._guard = guard
        self._strategy_instance = strategy_instance

    # ------------------------------------------------------------------
    # 开仓
    # ------------------------------------------------------------------

    async def open_delta_neutral(
        self,
        opportunity: FundingRateOpportunity,
        size_usd: Decimal,
    ) -> Position:
        """开一个 Delta 中性仓位（现货多 + 永续空）。

        Raises
        ------
        RiskLimitError
            风控检查失败时抛出。
        """
        # 1. 风控检查
        self._guard.assert_can_open(
            positions=self._manager.open_positions,
            new_notional=size_usd,
            apr_pct=opportunity.apr_pct,
        )

        symbol = opportunity.symbol
        exchange = opportunity.exchange

        # 2. 从订单簿计算参考价和下单数量
        spot_ask = PaperBroker.reference_price_from_book(
            opportunity.spot_orderbook, Side.BUY
        )
        perp_bid = PaperBroker.reference_price_from_book(
            opportunity.perp_orderbook, Side.SELL
        )
        quantity = (size_usd / spot_ask).quantize(Decimal("0.00000001"))

        # 3. 在内存创建仓位
        pos = self._manager.create(
            strategy_instance=self._strategy_instance,
            symbol=symbol,
            notional_usd=size_usd,
            target_apr_pct=opportunity.apr_pct,
        )

        # 4. 执行两条腿
        spot_req = OrderRequest(
            symbol=symbol,
            side=Side.BUY,
            size=quantity,
            reference_price=spot_ask,
            exchange=exchange,
        )
        perp_req = OrderRequest(
            symbol=symbol,
            side=Side.SELL,
            size=quantity,
            reference_price=perp_bid,
            exchange=exchange,
            reduce_only=False,
        )
        spot_result, perp_result = await self._broker.execute_pair(spot_req, perp_req)

        # 5. 将成交价写入腿
        pos.add_leg(PositionLeg(
            exchange=exchange,
            symbol=symbol,
            instrument_type=InstrumentType.SPOT,
            side=Side.BUY,
            size=spot_result.filled_size,
            entry_price=spot_result.avg_price,
        ))
        pos.add_leg(PositionLeg(
            exchange=exchange,
            symbol=symbol,
            instrument_type=InstrumentType.PERPETUAL,
            side=Side.SELL,
            size=perp_result.filled_size,
            entry_price=perp_result.avg_price,
        ))

        # 6. 记录手续费并标记开仓
        total_fees = spot_result.fees + perp_result.fees
        self._manager.record_fees(pos.id, total_fees)
        pos.mark_open()

        # 7. 持久化
        await self._manager.save(pos)

        logger.info(
            "position_opened",
            position_id=pos.id,
            symbol=str(symbol),
            size_usd=str(size_usd),
            spot_price=str(spot_result.avg_price),
            perp_price=str(perp_result.avg_price),
            fees=str(total_fees),
        )
        return pos

    # ------------------------------------------------------------------
    # 平仓
    # ------------------------------------------------------------------

    async def close_position(
        self,
        position_id: str,
        reason: ExitReason = ExitReason.MANUAL,
    ) -> Position:
        """平仓：对每条腿执行反向订单，计算已实现盈亏。

        Raises
        ------
        KeyError
            仓位不存在时抛出。
        """
        pos = self._manager.get(position_id)
        if pos is None:
            raise KeyError(f"仓位不存在: {position_id}")

        close_fees = Decimal("0")
        realized_pnl = Decimal("0")

        for leg in pos.legs:
            close_side = leg.side.opposite()
            req = OrderRequest(
                symbol=leg.symbol,
                side=close_side,
                size=leg.size,
                reference_price=leg.entry_price,  # 纸交易以建仓价为参考
                exchange=leg.exchange,
                reduce_only=True,
            )
            result: OrderResult = await self._broker.execute(req)
            close_fees += result.fees

            # 计算该腿已实现盈亏
            price_diff = result.avg_price - leg.entry_price
            if leg.side == Side.SELL:
                price_diff = -price_diff
            realized_pnl += price_diff * leg.size

        self._manager.record_fees(pos.id, close_fees)
        self._manager.close(position_id=pos.id, reason=reason, realized_pnl=realized_pnl)
        await self._manager.save(pos)

        logger.info(
            "position_closed",
            position_id=pos.id,
            reason=reason.value,
            realized_pnl=str(realized_pnl),
            close_fees=str(close_fees),
        )
        return pos

    # ------------------------------------------------------------------
    # 持仓监控
    # ------------------------------------------------------------------

    def check_all_positions(
        self,
        as_of: "datetime | None" = None,
    ) -> list[tuple[Position, list]]:
        """检查所有开仓，返回有风控违规的 (position, violations) 列表。

        Parameters
        ----------
        as_of:
            持仓时长的参考时间（默认使用系统时钟）。
            回测时传入模拟周期时间戳。
        """
        flagged = []
        for pos in self._manager.open_positions:
            violations = self._guard.check_position(pos, as_of=as_of)
            if violations:
                flagged.append((pos, violations))
        return flagged
