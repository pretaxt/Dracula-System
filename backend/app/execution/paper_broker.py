"""纸交易经纪商

在没有真实 API 连接的情况下模拟订单成交，用于策略验证和回测。

成交模型：
- 使用订单簿当前价格作为参考
- 买单在 ask 价格成交，卖单在 bid 价格成交
- 在参考价上叠加可配置滑点（bps）
- 手续费按名义价值的固定比例收取

用法::

    broker = PaperBroker(slippage_bps=Decimal("2"), fee_rate=Decimal("0.0004"))
    result = await broker.execute(request)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.exchanges.models import InstrumentType, OrderBook, Side, Symbol


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class OrderRequest:
    """提交给 broker 的单条订单请求。"""

    symbol: Symbol
    side: Side
    size: Decimal                       # 基础货币数量
    reference_price: Decimal            # 参考价（通常来自订单簿 ask/bid）
    exchange: str = "paper"
    reduce_only: bool = False           # 是否为平仓单
    client_order_id: str = ""
    instrument_type: InstrumentType = InstrumentType.SPOT
    # D.2.c — 现货保证金（做空）支持。仅 instrument_type=SPOT 时有意义
    margin_mode: str | None = None      # None=现货普通；'cross' / 'isolated' = 保证金模式
    side_effect: str | None = None      # 'MARGIN_BUY'=借币卖空开仓；'AUTO_REPAY'=买回还币平仓
    # 永续 Hedge 双向持仓模式必填（账户开 dualSidePosition=true 时）：'LONG' / 'SHORT'
    position_side: str | None = None


@dataclass
class OrderResult:
    """broker 返回的成交结果。"""

    request: OrderRequest
    filled: bool
    avg_price: Decimal
    filled_size: Decimal
    fees: Decimal                       # 手续费 (USD)
    slippage_bps: Decimal               # 实际滑点 (bps)
    filled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str = ""
    leg_already_closed: bool = False    # perp 已被交易所强平，跳过该腿 PnL 计算

    @property
    def notional_usd(self) -> Decimal:
        return self.avg_price * self.filled_size

    @property
    def is_success(self) -> bool:
        return self.filled and not self.error


# ---------------------------------------------------------------------------
# 纸交易 broker
# ---------------------------------------------------------------------------


class PaperBroker:
    """模拟成交的纸交易经纪商。

    Parameters
    ----------
    slippage_bps:
        固定滑点（基点），买单价格上浮，卖单价格下移。默认 2 bps（0.02%）。
    fee_rate:
        手续费率（名义价值的比例）。默认 0.04%（Taker fee）。
    """

    def __init__(
        self,
        slippage_bps: Decimal = Decimal("2"),
        fee_rate: Decimal = Decimal("0.0004"),
    ) -> None:
        self.slippage_bps = slippage_bps
        self.fee_rate = fee_rate
        self._history: list[OrderResult] = []

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def execute(self, request: OrderRequest) -> OrderResult:
        """模拟执行一条订单并返回成交结果。"""
        avg_price = self._apply_slippage(request.reference_price, request.side)
        fees = avg_price * request.size * self.fee_rate
        result = OrderResult(
            request=request,
            filled=True,
            avg_price=avg_price,
            filled_size=request.size,
            fees=fees,
            slippage_bps=self.slippage_bps,
        )
        self._history.append(result)
        return result

    async def execute_pair(
        self,
        spot_request: OrderRequest,
        perp_request: OrderRequest,
    ) -> tuple[OrderResult, OrderResult]:
        """同时执行现货和永续两条腿（纸交易无执行顺序风险）。"""
        spot_result = await self.execute(spot_request)
        perp_result = await self.execute(perp_request)
        return spot_result, perp_result

    @property
    def history(self) -> list[OrderResult]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _apply_slippage(self, reference_price: Decimal, side: Side) -> Decimal:
        """买单价格上浮 slippage_bps；卖单价格下移 slippage_bps。"""
        factor = self.slippage_bps / Decimal("10000")
        if side == Side.BUY:
            return (reference_price * (Decimal("1") + factor)).quantize(Decimal("0.00000001"))
        else:
            return (reference_price * (Decimal("1") - factor)).quantize(Decimal("0.00000001"))

    @staticmethod
    def reference_price_from_book(orderbook: OrderBook, side: Side) -> Decimal:
        """从订单簿取最优参考价：买单用 ask，卖单用 bid。"""
        if side == Side.BUY:
            if not orderbook.asks:
                raise ValueError(f"订单簿 asks 为空: {orderbook.symbol}")
            return orderbook.asks[0][0]
        else:
            if not orderbook.bids:
                raise ValueError(f"订单簿 bids 为空: {orderbook.symbol}")
            return orderbook.bids[0][0]
