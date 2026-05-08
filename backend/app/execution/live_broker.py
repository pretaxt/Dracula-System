"""实盘经纪商

通过 BinanceAdapter 执行真实订单，接口与 PaperBroker 完全相同，
可无缝替换。

执行顺序（开仓）:
  1. 先现货多单（Spot BUY Market）
  2. 再永续空单（USDM SELL Market，ISOLATED 5x）
  3. 若永续失败 → 自动反向平掉现货（紧急回卷），再上抛异常

平仓安全网:
  - 永续 reduce_only 订单若交易所报错"仓位不存在"（已被强平），
    返回 leg_already_closed=True，跳过该腿 PnL 计算，继续关闭现货

用法::

    broker = LiveBroker(
        adapter=BinanceAdapter(api_key=..., api_secret=...),
        fee_rate=Decimal("0.0002"),
        perp_leverage=Decimal("5"),
    )
    spot_r, perp_r = await broker.execute_pair(spot_req, perp_req)
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.core.logging import get_logger
from app.exchanges.errors import InsufficientBalanceError, OrderRejectedError
from app.exchanges.models import InstrumentType, OrderType, Side
from app.execution.paper_broker import OrderRequest, OrderResult

logger = get_logger(__name__)

# 永续 close 时若仓位已不存在，Binance 可能返回这些字符串（小写匹配）
_ALREADY_CLOSED = (
    "position is not exist",
    "position side does not match",
    "does not exist",
    "reduceonly order is rejected",
    "no need to change margin",
)


class LiveBroker:
    """实盘经纪商 — 调用真实交易所 API 下单。

    Parameters
    ----------
    adapter:
        已初始化的 BinanceAdapter（含有效 API key / secret）。
    fee_rate:
        估算手续费率（用于 OrderResult.fees 统计，不影响实际费用）。
    perp_leverage:
        永续 SHORT 目标杠杆倍数（开仓前自动设置 ISOLATED + leverage）。
    """

    def __init__(
        self,
        adapter,
        fee_rate: Decimal = Decimal("0.0002"),
        perp_leverage: Decimal = Decimal("5"),
    ) -> None:
        self._adapter = adapter
        self.fee_rate = fee_rate
        self._perp_leverage = perp_leverage
        self._leverage_initialized: set[str] = set()

    # ------------------------------------------------------------------
    # 公开 API（与 PaperBroker 接口一致）
    # ------------------------------------------------------------------

    async def execute(self, request: OrderRequest) -> OrderResult:
        """执行单条订单，返回 OrderResult。

        对永续 reduce_only 订单，若仓位已不存在（被强平），
        返回 leg_already_closed=True 而非抛出异常。
        """
        instrument = request.instrument_type

        # 开仓永续前先保证 ISOLATED + 目标杠杆 + USDM 钱包保证金充足
        if instrument == InstrumentType.PERPETUAL and not request.reduce_only:
            await self._ensure_perp_leverage(request.symbol)
            await self._ensure_perp_margin(request)

        try:
            order = await self._adapter.place_order(
                symbol=request.symbol,
                instrument=instrument,
                side=request.side,
                order_type=OrderType.MARKET,
                size=self._round_qty(request.symbol, instrument, request.size),
                reduce_only=request.reduce_only,
                client_order_id=request.client_order_id or None,
            )
            avg_price = order.avg_fill_price or request.reference_price
            fees = avg_price * order.filled * self.fee_rate
            logger.info(
                "live_order_filled",
                symbol=str(request.symbol),
                side=request.side.value,
                instrument=instrument.value,
                avg_price=str(avg_price),
                filled=str(order.filled),
            )
            return OrderResult(
                request=request,
                filled=True,
                avg_price=avg_price,
                filled_size=order.filled,
                fees=fees,
                slippage_bps=Decimal("0"),
                filled_at=datetime.now(UTC),
            )

        except (OrderRejectedError, InsufficientBalanceError) as exc:
            if request.reduce_only and instrument == InstrumentType.PERPETUAL:
                err_lower = str(exc).lower()
                if any(phrase in err_lower for phrase in _ALREADY_CLOSED):
                    logger.warning(
                        "perp_already_closed_skipping",
                        symbol=str(request.symbol),
                        error=str(exc),
                    )
                    return OrderResult(
                        request=request,
                        filled=False,
                        avg_price=request.reference_price,
                        filled_size=Decimal("0"),
                        fees=Decimal("0"),
                        slippage_bps=Decimal("0"),
                        filled_at=datetime.now(UTC),
                        leg_already_closed=True,
                    )
            raise

    async def execute_pair(
        self,
        spot_request: OrderRequest,
        perp_request: OrderRequest,
    ) -> tuple[OrderResult, OrderResult]:
        """先现货后永续执行两条腿。永续失败时自动回卷现货。"""
        await self._ensure_perp_leverage(spot_request.symbol)

        spot_result = await self.execute(spot_request)

        try:
            perp_result = await self.execute(perp_request)
        except Exception as exc:
            logger.error(
                "perp_open_failed_unwinding_spot",
                symbol=str(spot_request.symbol),
                error=str(exc),
            )
            await self._unwind_spot(spot_request, spot_result)
            raise RuntimeError(
                f"perp open failed for {spot_request.symbol}, "
                f"spot unwound. Original: {exc}"
            ) from exc

        return spot_result, perp_result

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _ensure_perp_margin(self, request: OrderRequest) -> None:
        """开 perp 仓前确保 USDM 钱包 USDT 充足；不足则从 spot 自动划转。

        计算逻辑：
          required_margin = notional / leverage
          target = required_margin × buffer  (buffer=1.2 含手续费/滑点缓冲)
          shortfall = target − current_perp_usdt
          划转金额 = max(shortfall + 1 USDT 余裕, 0)

        失败时仅 warning 不抛——后续 place_order 自己尝试，
        若真不够交易所会返回明确错误，被 LiveBroker.execute_pair 捕获 → unwind spot。
        """
        try:
            usdm_client = self._adapter._clients.get(InstrumentType.PERPETUAL)
            spot_client = self._adapter._clients.get(InstrumentType.SPOT)
            if usdm_client is None or spot_client is None:
                return

            notional = request.size * request.reference_price
            required_margin = notional / self._perp_leverage
            target = required_margin * Decimal("1.2")  # 20% 缓冲

            raw = await usdm_client.fetch_balance()
            current = Decimal(str((raw.get("total") or {}).get("USDT") or 0))

            if current >= target:
                logger.debug(
                    "perp_margin_sufficient",
                    symbol=str(request.symbol),
                    current=str(round(current, 2)),
                    target=str(round(target, 2)),
                )
                return

            shortfall = target - current
            transfer_amount = shortfall + Decimal("1")  # +$1 兜底数值精度

            await spot_client.transfer(
                "USDT", float(transfer_amount), "spot", "future"
            )
            logger.info(
                "perp_margin_topped_up",
                symbol=str(request.symbol),
                from_account="spot",
                to_account="future",
                amount=str(round(transfer_amount, 2)),
                required=str(round(required_margin, 2)),
                before=str(round(current, 2)),
            )
        except Exception as exc:
            logger.warning(
                "perp_margin_topup_failed",
                symbol=str(request.symbol),
                error=str(exc)[:200],
            )

    async def _ensure_perp_leverage(self, symbol) -> None:
        """第一次对某 symbol 下永续单前，设置 ISOLATED 模式 + 目标杠杆。"""
        sym_key = str(symbol)
        if sym_key in self._leverage_initialized:
            return
        try:
            usdm = self._adapter._clients[InstrumentType.PERPETUAL]
            ccxt_sym = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
            try:
                await usdm.set_margin_mode("isolated", ccxt_sym)
            except Exception as e:
                logger.debug("set_margin_mode_skipped", symbol=sym_key, reason=str(e))
            await usdm.set_leverage(int(self._perp_leverage), ccxt_sym)
            self._leverage_initialized.add(sym_key)
            logger.info("perp_leverage_set", symbol=sym_key, leverage=str(self._perp_leverage))
        except Exception as exc:
            logger.warning("perp_leverage_set_failed", symbol=sym_key, error=str(exc))

    def _round_qty(self, symbol, instrument: InstrumentType, qty: Decimal) -> Decimal:
        """用 CCXT amount_to_precision 将数量对齐到交易所 lot size。"""
        try:
            client = self._adapter._clients[instrument]
            ccxt_sym = symbol.to_ccxt()
            rounded = client.amount_to_precision(ccxt_sym, float(qty))
            return Decimal(str(rounded))
        except Exception:
            return qty

    async def _unwind_spot(
        self, original_req: OrderRequest, spot_result: OrderResult
    ) -> None:
        """回卷现货：对已成交的现货数量执行市价卖单。"""
        if spot_result.filled_size <= 0:
            return
        try:
            unwind_req = OrderRequest(
                symbol=original_req.symbol,
                side=Side.SELL,
                size=spot_result.filled_size,
                reference_price=spot_result.avg_price,
                exchange=original_req.exchange,
                reduce_only=False,
                instrument_type=InstrumentType.SPOT,
            )
            await self.execute(unwind_req)
            logger.info("spot_unwind_success", symbol=str(original_req.symbol))
        except Exception as unwind_exc:
            logger.error(
                "spot_unwind_failed",
                symbol=str(original_req.symbol),
                error=str(unwind_exc),
            )
