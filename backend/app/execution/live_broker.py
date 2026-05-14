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

from dataclasses import replace
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

# R8 (Wave 2): auto-rebalance 单次上限（防误划大额）
_MAX_AUTO_REBALANCE_USD = Decimal("100")
# 日累计上限 — 用 module-level 字典追踪（in-memory，重启清零）
_DAILY_REBALANCE_BUDGET_USD = Decimal("500")
_daily_rebalance_used: dict[str, Decimal] = {}  # date_str → amount


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

        # 现货保证金做空（D.2.c 贴水方向）开仓前确保 margin 钱包有抵押品
        if (
            instrument == InstrumentType.SPOT
            and getattr(request, "margin_mode", None)
            and getattr(request, "side_effect", None) == "MARGIN_BUY"
        ):
            await self._ensure_spot_margin(request)

        # X7 修复：spot SELL 自动 cap 到真实余额。Why：开仓时 fee 在 base asset 里
        # 扣（如 ARB 0.1%），DB leg.size 是请求量但实际持有量更小。close 时直接用
        # leg.size 卖会触发 -2010 InsufficientFunds → spot 单腿暴露。
        if instrument == InstrumentType.SPOT and request.side == Side.SELL:
            request = await self._cap_spot_sell_to_balance(request)

        try:
            order = await self._adapter.place_order(
                symbol=request.symbol,
                instrument=instrument,
                side=request.side,
                order_type=OrderType.MARKET,
                size=self._round_qty(request.symbol, instrument, request.size),
                reduce_only=request.reduce_only,
                client_order_id=request.client_order_id or None,
                margin_mode=getattr(request, "margin_mode", None),
                side_effect=getattr(request, "side_effect", None),
                position_side=getattr(request, "position_side", None),
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
        """先现货后永续执行两条腿。永续失败时自动回卷现货。

        P0 预检：在下任何单之前同时检查 spot quote 余额 + perp margin 余额，
        任一不够直接抛 InsufficientBalanceError，**零下单**。这是"禁止单腿持仓"
        的第一道防线 — 完全避免一腿成功一腿失败的场景。
        """
        await self._ensure_perp_leverage(spot_request.symbol)

        # R3 双腿 size 对齐 — 防 spot 12.48 / perp 12.00 这种 delta 漂移
        spot_request, perp_request = await self._align_pair_size(spot_request, perp_request)

        # P0 双腿余额预检 — 不通过则不下任何单
        await self._preflight_check_balances(spot_request, perp_request)

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

    async def _try_auto_rebalance_spot(
        self, quote_asset: str, shortfall: Decimal,
    ) -> bool:
        """R8: spot quote 余额不足时，自动从 cross-margin 划转。

        仅 Binance 实现（OKX UTA 共享钱包不需要）。带 3 重保护：
          1. 单次 ≤ MAX_AUTO_REBALANCE_USD ($100)
          2. 日累计 ≤ DAILY_REBALANCE_BUDGET_USD ($500)
          3. cross-margin 余额必须充足且划后留 buffer

        Returns
        -------
        bool: 是否成功划转（caller 据此决定是否重新 check 余额）
        """
        if self._adapter.exchange_id != "binance":
            return False
        spot_client = self._adapter._clients.get(InstrumentType.SPOT)
        if spot_client is None:
            return False

        # 计算划转金额：1.5x shortfall（buffer），但不超 MAX_AUTO_REBALANCE_USD
        transfer_amount = shortfall * Decimal("1.5")
        if transfer_amount > _MAX_AUTO_REBALANCE_USD:
            transfer_amount = _MAX_AUTO_REBALANCE_USD
        # 至少划 shortfall（无 buffer 也得满足）
        if transfer_amount < shortfall:
            logger.warning(
                "auto_rebalance_skip_above_cap",
                shortfall=str(shortfall),
                cap=str(_MAX_AUTO_REBALANCE_USD),
            )
            return False

        # 日累计 cap
        from datetime import date  # noqa: PLC0415
        today = date.today().isoformat()
        used = _daily_rebalance_used.get(today, Decimal("0"))
        if used + transfer_amount > _DAILY_REBALANCE_BUDGET_USD:
            logger.warning(
                "auto_rebalance_skip_daily_cap",
                used_today=str(used),
                budget=str(_DAILY_REBALANCE_BUDGET_USD),
            )
            return False

        try:
            # 1. 优先 cross-margin → spot
            ma = await spot_client.sapi_get_margin_account()
            margin_free = Decimal("0")
            for a in ma.get("userAssets", []):
                if a.get("asset") == quote_asset:
                    margin_free = Decimal(str(a.get("free") or 0))
                    break
            source = None
            transfer_type = None
            if margin_free >= transfer_amount:
                source = "cross-margin"
                transfer_type = "MARGIN_MAIN"
            else:
                # 2. cross-margin 不够 → 尝试 funding wallet (Pay)
                try:
                    fw = await spot_client.sapi_post_asset_get_funding_asset({})
                    funding_free = Decimal("0")
                    for a in (fw or []):
                        if a.get("asset") == quote_asset:
                            funding_free = Decimal(str(a.get("free") or 0))
                            break
                    if funding_free >= transfer_amount:
                        source = "funding"
                        transfer_type = "FUNDING_MAIN"
                except Exception:
                    pass

            if source is None:
                logger.warning(
                    "auto_rebalance_skip_no_source",
                    asset=quote_asset,
                    margin_free=str(margin_free),
                    needed=str(transfer_amount),
                )
                return False

            # 3. 执行划转
            result = await spot_client.sapi_post_asset_transfer({
                "type": transfer_type,
                "asset": quote_asset,
                "amount": str(transfer_amount),
            })
            _daily_rebalance_used[today] = used + transfer_amount
            logger.info(
                "auto_rebalance_spot_quote_success",
                source=source,
                asset=quote_asset,
                amount=str(transfer_amount),
                shortfall=str(shortfall),
                tran_id=result.get("tranId"),
                daily_used=str(_daily_rebalance_used[today]),
            )
            return True
        except Exception as e:
            logger.warning(
                "auto_rebalance_failed",
                asset=quote_asset, error=str(e),
            )
            # T12/T13: risk_event + Telegram
            try:
                from app.notifications import notify_reconcile_alert  # noqa: PLC0415
                from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
                await write_risk_event(
                    event_type="auto_rebalance_failed",
                    severity="high",
                    description=(
                        f"[{self._adapter.exchange_id}] cross-margin → spot {quote_asset} "
                        f"自动划转失败：{e}"
                    ),
                    action_taken="alert_only",
                    extra={"shortfall": str(shortfall), "transfer_amount": str(transfer_amount)},
                )
                notify_reconcile_alert(
                    alert_type="auto_rebalance_failed",
                    severity="high",
                    exchange=self._adapter.exchange_id,
                    symbol=quote_asset,
                    explanation=f"自动划转失败 ({e})，开仓将被 InsufficientBalance 拒",
                )
            except Exception:
                pass
            return False

    async def _cap_spot_sell_to_balance(self, request: OrderRequest) -> OrderRequest:
        """spot SELL 之前 fetch_balance 取真实 free 余额，cap 卖单数量。

        Why X7: 开仓时 spot fee 扣在 base asset 里（Binance spot taker 0.1%），
        DB leg.size=351.1 但真实持有 350.7489。close_position 用 leg.size 卖
        → -2010 InsufficientFunds → spot leg 单腿暴露。
        """
        try:
            spot_client = self._adapter._clients.get(InstrumentType.SPOT)
            if spot_client is None:
                return request
            base_asset = request.symbol.base
            raw_bal = await spot_client.fetch_balance()
            free = Decimal(str((raw_bal.get("free") or {}).get(base_asset) or 0))
            if free <= 0:
                return request  # 没余额让 broker 报错触发 reconciler
            if free >= request.size:
                return request  # 余额够，无需 cap
            try:
                precise = spot_client.amount_to_precision(
                    str(request.symbol), float(free)
                )
                capped = Decimal(str(precise))
            except Exception:
                capped = free
            logger.info(
                "spot_sell_capped_to_balance",
                symbol=str(request.symbol),
                requested=str(request.size),
                free_balance=str(free),
                capped_to=str(capped),
            )
            return replace(request, size=capped)
        except Exception:
            logger.warning(
                "spot_sell_cap_check_failed",
                symbol=str(request.symbol),
            )
            return request

    async def _align_pair_size(
        self,
        spot_request: OrderRequest,
        perp_request: OrderRequest,
    ) -> tuple[OrderRequest, OrderResult]:
        """对齐 spot/perp 两腿数量到两边交易所精度的最小公约数。

        Why R3: spot 可以小数（如 12.48），但 perp 通常按 contract 整数（12.00），
        OrderExecutor 计算出 12.48 后两腿用同一 size 字段，但 broker 真实下单时
        perp 被 ccxt amount_to_precision 截到 12 → spot 长 12.48 vs perp 短 12 →
        净 delta 0.48 个 base asset 暴露 = 非 delta-neutral。

        实现：分别用 spot/perp client 的 amount_to_precision 算各自精度，取 min。
        如不可获取（公开行情 only / 非 ccxt），保持原 size。
        """
        try:
            spot_client = self._adapter._clients.get(InstrumentType.SPOT)
            perp_client = self._adapter._clients.get(InstrumentType.PERPETUAL)
            sizes: list[Decimal] = []
            sym_str = str(spot_request.symbol)
            if spot_client is not None:
                try:
                    s = spot_client.amount_to_precision(sym_str, float(spot_request.size))
                    sizes.append(Decimal(str(s)))
                except Exception:
                    pass
            if perp_client is not None:
                try:
                    p = perp_client.amount_to_precision(sym_str, float(perp_request.size))
                    sizes.append(Decimal(str(p)))
                except Exception:
                    pass
            if not sizes:
                return spot_request, perp_request
            aligned = min(sizes)
            if aligned <= 0:
                return spot_request, perp_request
            if aligned != spot_request.size or aligned != perp_request.size:
                logger.info(
                    "pair_size_aligned",
                    symbol=sym_str,
                    original_spot=str(spot_request.size),
                    original_perp=str(perp_request.size),
                    aligned=str(aligned),
                )
            return (
                replace(spot_request, size=aligned),
                replace(perp_request, size=aligned),
            )
        except Exception:
            logger.exception("pair_size_align_failed", symbol=str(spot_request.symbol))
            return spot_request, perp_request

    async def _preflight_check_balances(
        self,
        spot_request: OrderRequest,
        perp_request: OrderRequest,
    ) -> None:
        """开仓前同时检查 spot quote 余额和 perp margin 余额，任一不够则 raise。

        Why: 防止一腿下单成功后另一腿失败留下单腿持仓 — 当余额可在事前判断时，
        宁可不下单（用户可补资金重试）也不开。预检过后再下单的失败概率极低
        （仅剩交易所瞬时 rate-limit / 价格剧变 / 受限标的等场景），unwind 是
        第二道防线。

        Raises
        ------
        InsufficientBalanceError
            spot quote 余额或 perp margin 余额不够开仓。
        """
        # 先尝试自动划转 perp margin（spot/cross-margin → USDM perp），让 perp 钱包凑齐
        try:
            await self._ensure_perp_margin(perp_request)
        except Exception:
            logger.warning(
                "preflight_perp_topup_attempt_failed",
                symbol=str(perp_request.symbol),
            )

        # 1. 检查 spot quote 余额
        spot_client = self._adapter._clients.get(InstrumentType.SPOT)
        if spot_client is not None:
            try:
                raw = await spot_client.fetch_balance()
                quote = spot_request.symbol.quote
                free = Decimal(str((raw.get("free") or {}).get(quote) or 0))
                notional = spot_request.size * spot_request.reference_price
                # 3× fee buffer 防滑点 + 手续费上下界
                required = notional * (Decimal("1") + self.fee_rate * Decimal("3"))
                if free < required:
                    # R8: 尝试 auto-rebalance（cross-margin → spot）
                    shortfall = required - free
                    if await self._try_auto_rebalance_spot(quote, shortfall):
                        # 重新读 free
                        raw = await spot_client.fetch_balance()
                        free = Decimal(str((raw.get("free") or {}).get(quote) or 0))
                    if free < required:
                        raise InsufficientBalanceError(
                            f"{self._adapter.exchange_id} spot {quote} insufficient: "
                            f"free={free} required={required:.4f} "
                            f"(notional={notional} fee_buffer={self.fee_rate * 3})"
                        )
            except InsufficientBalanceError:
                raise
            except Exception as e:
                logger.warning(
                    "preflight_spot_balance_check_failed",
                    symbol=str(spot_request.symbol),
                    error=str(e),
                )

        # 2. 检查 perp margin 余额（已经过一次 ensure 划转）
        perp_client = self._adapter._clients.get(InstrumentType.PERPETUAL)
        if perp_client is not None:
            try:
                raw = await perp_client.fetch_balance()
                free = Decimal(str((raw.get("total") or {}).get("USDT") or 0))
                notional = perp_request.size * perp_request.reference_price
                required = notional / self._perp_leverage
                # 5% buffer 防滑点 / liquidation precision
                required_with_buffer = required * Decimal("1.05")
                if free < required_with_buffer:
                    raise InsufficientBalanceError(
                        f"{self._adapter.exchange_id} perp USDT insufficient: "
                        f"free={free} required={required_with_buffer:.4f} "
                        f"(notional={notional} leverage={self._perp_leverage})"
                    )
            except InsufficientBalanceError:
                raise
            except Exception as e:
                logger.warning(
                    "preflight_perp_balance_check_failed",
                    symbol=str(perp_request.symbol),
                    error=str(e),
                )

        logger.debug(
            "preflight_balance_check_passed",
            symbol=str(spot_request.symbol),
        )

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    async def _ensure_perp_margin(self, request: OrderRequest) -> None:
        """开 perp 仓前确保有足够保证金；不足时调 adapter.top_up_perp_margin 补足。

        各交易所行为不同（adapter 自封装）：
          - Binance: spot/USDM 钱包隔离 → 真划转
          - OKX:    trading account 共享 spot+swap 余额 → no-op

        计算：
          required_margin = notional / leverage
          target = required_margin × 1.2 (20% buffer)
          shortfall = target − current_perp_usdt
          transfer = shortfall + 1 USDT (数值精度兜底)

        失败时仅 warning，让 place_order 自行尝试；若真不够交易所明确报错 →
        execute_pair 捕获 → unwind spot。
        """
        try:
            usdm_client = self._adapter._clients.get(InstrumentType.PERPETUAL)
            if usdm_client is None:
                return

            notional = request.size * request.reference_price
            required_margin = notional / self._perp_leverage
            target = required_margin * Decimal("1.2")

            # 优先用 adapter 自定义方法（htx UTA fetch_balance 报 4002，需走 v3 endpoint）
            fetch_perp = getattr(self._adapter, "fetch_perp_usdt_balance", None)
            if fetch_perp is not None:
                try:
                    current = await fetch_perp()
                except Exception:
                    current = Decimal("0")
            else:
                try:
                    raw = await usdm_client.fetch_balance()
                    current = Decimal(str((raw.get("total") or {}).get("USDT") or 0))
                except Exception:
                    current = Decimal("0")

            if current >= target:
                logger.debug(
                    "perp_margin_sufficient",
                    symbol=str(request.symbol),
                    current=str(round(current, 2)),
                    target=str(round(target, 2)),
                )
                return

            shortfall = target - current
            transfer_amount = shortfall + Decimal("1")

            # adapter 自己处理交易所差异（Binance 真划，OKX no-op）
            top_up = getattr(self._adapter, "top_up_perp_margin", None)
            if top_up is None:
                logger.warning("adapter_no_top_up_method", symbol=str(request.symbol))
                return
            await top_up(transfer_amount)
            logger.info(
                "perp_margin_topped_up",
                symbol=str(request.symbol),
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

    async def _ensure_spot_margin(self, request: OrderRequest) -> None:
        """D.2.c 贴水方向开 SHORT spot 前，确保现货保证金钱包有足够抵押品。

        Binance 现货全仓杠杆账户与 spot wallet 隔离。卖空前需要：
          collateral >= notional / max_leverage_ratio (默认按 5x 估算)
        缓冲 30%；不足时调 ``adapter.top_up_spot_margin`` 自动从 spot 划转。

        失败时仅 warning，让 place_order 自行尝试（Binance 会用 -2010 拒绝
        → execute_pair 捕获 → 跳过本次开仓，不写 row）。
        """
        try:
            adapter = self._adapter
            balance_fn = getattr(adapter, "fetch_spot_margin_usdt_balance", None)
            top_up = getattr(adapter, "top_up_spot_margin", None)
            if balance_fn is None or top_up is None:
                return  # adapter 不支持 spot margin 路径

            notional = request.size * request.reference_price
            # 5x 借币比例（Binance 全仓杠杆默认）：抵押 = notional / 5
            # 加 30% 缓冲应对滑点/利息累计
            required = notional / Decimal("5") * Decimal("1.3")

            current = await balance_fn()
            if current >= required:
                logger.debug(
                    "spot_margin_sufficient",
                    symbol=str(request.symbol),
                    current=str(round(current, 2)),
                    required=str(round(required, 2)),
                )
                return

            shortfall = required - current
            transfer_amount = shortfall + Decimal("1")  # +1 USDT 精度兜底
            await top_up(transfer_amount)
            logger.info(
                "spot_margin_topped_up",
                symbol=str(request.symbol),
                amount=str(round(transfer_amount, 2)),
                required=str(round(required, 2)),
                before=str(round(current, 2)),
            )
        except Exception as exc:
            logger.warning(
                "spot_margin_topup_failed",
                symbol=str(request.symbol),
                error=str(exc)[:200],
            )

    async def _ensure_perp_leverage(self, symbol) -> None:
        """第一次对某 symbol 下永续单前，设置 CROSS 模式 + 目标杠杆（2026-05-13 用户决策从 isolated 切换；持仓中 binance 拒改 except 已吃错）"""
        sym_key = str(symbol)
        if sym_key in self._leverage_initialized:
            return
        try:
            usdm = self._adapter._clients[InstrumentType.PERPETUAL]
            ccxt_sym = f"{symbol.base}/{symbol.quote}:{symbol.quote}"
            try:
                await usdm.set_margin_mode("cross", ccxt_sym)
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
        """回卷现货：对已成交的现货数量执行市价卖单。

        Why: spot 买单的 fee 在 base asset 里扣（如 FIL 0.1%），filled_size 不等于
        实际可卖余额。用 fetch_balance 取真实 free 持仓，并按交易所 amount precision
        向下取整，避免 -2010 "insufficient balance" 拒单导致 spot 裸多遗留。
        """
        if spot_result.filled_size <= 0:
            return
        try:
            spot_client = self._adapter._clients.get(InstrumentType.SPOT)
            base_asset = original_req.symbol.base
            sell_size = spot_result.filled_size
            if spot_client is not None:
                try:
                    raw_bal = await spot_client.fetch_balance()
                    free = Decimal(str((raw_bal.get("free") or {}).get(base_asset) or 0))
                    if free > 0:
                        sell_size = min(spot_result.filled_size, free)
                except Exception:
                    logger.warning(
                        "spot_unwind_balance_fetch_failed",
                        symbol=str(original_req.symbol),
                    )
                try:
                    precise = spot_client.amount_to_precision(
                        str(original_req.symbol), float(sell_size)
                    )
                    sell_size = Decimal(str(precise))
                except Exception:
                    pass
            if sell_size <= 0:
                logger.warning(
                    "spot_unwind_skipped_zero_size",
                    symbol=str(original_req.symbol),
                    filled_size=str(spot_result.filled_size),
                )
                return
            unwind_req = OrderRequest(
                symbol=original_req.symbol,
                side=Side.SELL,
                size=sell_size,
                reference_price=spot_result.avg_price,
                exchange=original_req.exchange,
                reduce_only=False,
                instrument_type=InstrumentType.SPOT,
            )
            await self.execute(unwind_req)
            logger.info(
                "spot_unwind_success",
                symbol=str(original_req.symbol),
                sell_size=str(sell_size),
                filled_size=str(spot_result.filled_size),
            )
        except Exception as unwind_exc:
            logger.error(
                "spot_unwind_failed",
                symbol=str(original_req.symbol),
                error=str(unwind_exc),
            )
            # R9: 写 risk_event — spot 单腿可能残留
            try:
                from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
                await write_risk_event(
                    event_type="spot_unwind_failed",
                    severity="critical",
                    description=(
                        f"[{original_req.exchange}/{original_req.symbol}] spot 反向卖回失败 — "
                        f"perp 失败后 unwind 也失败，spot 单腿暴露。需要 reconciler 接管。"
                    ),
                    action_taken="raised_to_executor",
                    extra={"error": str(unwind_exc), "spot_filled_size": str(spot_result.filled_size)},
                )
            except Exception:
                pass
