"""
dgr_btc/broker_adapter.py
=========================
DgrBtcBrokerAdapter — Phase F LIVE 模式真实 binance API 适配层.

接口契约 (atomic_pair / maker_reprice / recenter_safety / reconciliation 用):
  - place_limit_maker(market, side, price, qty) → Trade  (raise RejectError if cross)
  - place_market_unwind(market_type, side, quantity) → Trade  (taker, 紧急平腿)
  - cancel_order(order_id) → None
  - fetch_open_orders() → list[dict]
  - get_spot_balance(asset) → Decimal
  - get_perp_position(symbol) → Decimal
  - place_pair_dry(spot_intent, perp_intent) → None  (DRY_RUN 只走 preflight)

底层: 复用 app/execution/live_broker.py LiveBroker + ccxt binance adapter
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.types import MarketType, OrderIntent, Side, Trade

logger = structlog.get_logger(__name__)


class RejectError(Exception):
    """Order rejection (maker cross / no fill / safety block / API error).

    NOTE: originally defined in maker_reprice.py; inlined here after maker_reprice
    was removed in P2 cleanup (revamp 2026-05-26).
    """

    def __init__(self, message: str, original_error: Exception | None = None):
        super().__init__(message)
        self.original_error = original_error


@dataclass
class DgrBtcBrokerConfig:
    """Broker 行为配置."""
    perp_leverage: Decimal = Decimal("10")
    fee_rate_spot: Decimal = Decimal("0.001")
    fee_rate_perp_maker: Decimal = Decimal("0.0002")
    fee_rate_perp_taker: Decimal = Decimal("0.0005")
    spot_symbol: str = "BTC/USDT"
    perp_symbol: str = "BTC/USDT:USDT"
    base_asset: str = "BTC"
    quote_asset: str = "USDT"


class DgrBtcBrokerAdapter:
    """统一 LIVE 模式 broker 接口.

    Args:
        adapter:    已初始化的 BinanceAdapter (含有效 API key/secret)
        cfg:        DgrBtcBrokerConfig (fee / symbols / leverage)
        safety:     LiveSafetyGuard | None (max_order_usd / daily_cap / kill_switch)
        dry_run:    True = preflight only, 不真发单 (testnet 验证用)
    """

    def __init__(
        self,
        adapter: Any,
        cfg: Optional[DgrBtcBrokerConfig] = None,
        safety: Any | None = None,
        dry_run: bool = False,
    ) -> None:
        from app.execution.live_broker import LiveBroker
        self.adapter = adapter
        self.cfg = cfg or DgrBtcBrokerConfig()
        self.safety = safety
        self.dry_run = dry_run
        self._broker = LiveBroker(
            adapter=adapter,
            fee_rate=self.cfg.fee_rate_perp_maker,
            perp_leverage=self.cfg.perp_leverage,
        )
        # 统计
        self.n_place_limit_maker = 0
        self.n_reject = 0
        self.n_market_unwind = 0
        self.n_cancel = 0

    # ------------------------------------------------------------------
    # 0. place_order(intent) — atomic_pair 期望的统一入口
    # ------------------------------------------------------------------

    async def place_order(self, intent) -> Trade:
        """Delegate OrderIntent → place_limit_maker.

        atomic_pair._execute_live calls broker.place_order(intent) → Trade.
        OrderIntent has: market (MarketType), side (Side), price, quantity, grid_level, reason.
        """
        return await self.place_limit_maker(
            market=intent.market,
            side=intent.side,
            price=intent.price,
            quantity=intent.quantity,
        )

    # ------------------------------------------------------------------
    # 1. LIMIT_MAKER 下单 (MakerReprice 用)
    # ------------------------------------------------------------------

    async def place_limit_maker(
        self,
        market: MarketType,
        side: Side,
        price: Decimal,
        quantity: Decimal,
        no_wait: bool = False,
    ) -> Trade:
        """挂 LIMIT_MAKER 单. 默认 poll 5s 等成交, no_wait=True 则立刻 return placeholder.

        Phase H.1: no_wait=True 用于 pre-place 模型 — 单挂上去等被撞,
        fill 检测在后续 tick 的 fetch_open_orders 路径完成 (sync_fills).
        """
        self.n_place_limit_maker += 1
        # C3 修复: safety reservation 预扣 — 后续 reject/fail 必须 rollback,
        # fill 时 record_filled commit 到 daily.
        notional_reserved = price * quantity
        if self.safety is not None:
            ok, reason = self.safety.check_pre_order(
                market_type=market, side=side, price=price, quantity=quantity,
            )
            if not ok:
                # check 失败时未预扣 (check_pre_order 内部在通过时才 reserve)
                raise RejectError(f"safety_block:{reason}")

        if self.dry_run:
            logger.info(
                "dgr_btc_broker_dry_run_limit_maker",
                market=market.value, side=side.value,
                price=str(price), qty=str(quantity), no_wait=no_wait,
            )
            return self._mock_trade(market, side, price, quantity, is_maker=True)

        # Phase G.2: 真 LIMIT_MAKER 直调 ccxt (post-only) - 绕开 LiveBroker MARKET 路径
        from app.exchanges.models import InstrumentType
        import asyncio as _aio
        # 触发 PM 探测 (设置 ccxt clients 的 portfolioMargin=True option)
        try:
            if hasattr(self.adapter, "is_pm_account"):
                await self.adapter.is_pm_account()
        except Exception:
            pass
        instrument = (
            InstrumentType.SPOT if market == MarketType.SPOT else InstrumentType.PERPETUAL
        )
        client = self.adapter._clients[instrument]
        ccxt_sym = (
            f"{self.cfg.base_asset}/{self.cfg.quote_asset}" if market == MarketType.SPOT
            else f"{self.cfg.base_asset}/{self.cfg.quote_asset}:{self.cfg.quote_asset}"
        )
        # Phase H.5 fix: prefix must be "dgr_" so fetch_open_orders filter matches.
        # Previously "dgr{uuid}" produced "dgrABC123…" which failed startswith("dgr_") check.
        client_order_id = f"dgr_{_uuid.uuid4().hex[:13]}"
        ccxt_side = "buy" if side == Side.BUY else "sell"
        amount_f = float(quantity)
        price_f = float(price)
        # Phase G.8: binance API 参数适配
        #   spot:  type=LIMIT_MAKER (无 timeInForce, post-only by design)
        #   perp:  type=LIMIT_MAKER + positionSide=SHORT (hedge mode 必填)
        # Phase G.12: spot 用 LIMIT_MAKER, perp 用 LIMIT+GTX (perp 不接受 LIMIT_MAKER via ccxt)
        if market == MarketType.SPOT:
            order_type = "LIMIT_MAKER"
            params = {"newClientOrderId": client_order_id}
            # Phase H.live: PM cross margin 模式下 spot BUY/SELL 必须显式声明借贷意图
            #   BUY  → MARGIN_BUY: 自动借 USDT (free USDT 不够时)
            #   SELL → AUTO_REPAY: 卖出所得 USDT 自动还借款 (有 USDT 借款时)
            # 无此参数时 binance 不会自动借, BUY 单遇 free 不足直接拒
            if side == Side.BUY:
                params["sideEffectType"] = "MARGIN_BUY"
            else:
                params["sideEffectType"] = "AUTO_REPAY"
        else:
            order_type = "LIMIT"
            params = {
                "newClientOrderId": client_order_id,
                "timeInForce": "GTX",          # binance 永续 post-only
                "positionSide": "SHORT",       # dgr_btc 永远 short perp
            }
        try:
            raw = await client.create_order(
                symbol=ccxt_sym,
                type=order_type,
                side=ccxt_side,
                amount=amount_f,
                price=price_f,
                params=params,
            )
        except Exception as e:
            err = str(e)
            # Phase G.12: 加 binance "would immediately match" / "match and take" 模式
            _err_lower = err.lower()
            if (
                "post-only" in _err_lower
                or "post only" in _err_lower
                or "would match" in _err_lower
                or "immediately match" in _err_lower
                or "match and take" in _err_lower
                or "-2010" in err
                or "-1013" in err
            ):
                self.n_reject += 1
                # C3: rollback safety reservation (单未进 binance)
                if self.safety is not None:
                    try:
                        self.safety.rollback_reservation(notional_reserved)
                    except Exception:
                        pass
                logger.info(
                    "dgr_btc_broker_post_only_rejected",
                    market=market.value, side=side.value, price=str(price),
                )
                raise RejectError(f"post_only_crossed: {err[:120]}", original_error=e) from e
            # 其他 binance 错误也 rollback
            if self.safety is not None:
                try:
                    self.safety.rollback_reservation(notional_reserved)
                except Exception:
                    pass
            logger.exception("dgr_btc_broker_create_order_failed", market=market.value)
            raise RejectError(f"create_order_failed: {err[:120]}", original_error=e) from e

        order_id = raw.get("id") or raw.get("orderId") or client_order_id

        # Phase H.1: no_wait 立刻 return placeholder Trade — 让 inflight_manager 跟踪
        if no_wait:
            logger.info(
                "dgr_btc_broker_no_wait_placed",
                order_id=order_id, market=market.value, side=side.value,
                price=str(price), qty=str(quantity),
            )
            return Trade(
                trade_id=f"pending_{order_id}",
                order_id=str(order_id),
                symbol=self._symbol_str(market),
                market=market,
                side=side,
                price=price,
                quantity=Decimal("0"),  # 0 表示 placed 但还没 fill
                fee=Decimal("0"),
                is_maker=True,
                timestamp=datetime.now(timezone.utc),
                grid_level=price,
            )

        # Poll until filled or timeout (5s)
        max_wait = 5.0
        poll_interval = 0.5
        waited = 0.0
        filled = Decimal("0")
        avg_price_dec = price
        last_status = "open"
        while waited < max_wait:
            await _aio.sleep(poll_interval)
            waited += poll_interval
            try:
                od = await client.fetch_order(order_id, ccxt_sym)
            except Exception as e:
                logger.debug("dgr_btc_broker_fetch_order_retry", err=str(e)[:80])
                continue
            last_status = (od.get("status") or "open").lower()
            filled = Decimal(str(od.get("filled") or "0"))
            if od.get("average"):
                try:
                    avg_price_dec = Decimal(str(od["average"]))
                except Exception:
                    pass
            if last_status in ("closed", "filled") and filled >= quantity * Decimal("0.99"):
                break
            if last_status in ("canceled", "rejected", "expired"):
                self.n_reject += 1
                # C3: 中途 cancel/reject (filled=0) → 全额 rollback
                if self.safety is not None and filled == 0:
                    try:
                        self.safety.rollback_reservation(notional_reserved)
                    except Exception:
                        pass
                # 若 partial fill, 已 fill 部分作为 commit (按 filled 量 record_filled)
                elif self.safety is not None and filled > 0:
                    try:
                        partial_notional = avg_price_dec * filled
                        # commit partial + rollback 剩余 reservation
                        self.safety.record_filled(partial_notional)
                        self.safety.rollback_reservation(notional_reserved - partial_notional)
                    except Exception:
                        pass
                raise RejectError(f"order_{last_status} filled={filled}")
        else:
            # Timeout — C2 修复: cancel 失败后必须二次 fetch_order 确认最终状态,
            # 否则 binance 端单仍活, partial fill 或后续成交会丢失 → 单腿暴露.
            cancel_ok = False
            try:
                await client.cancel_order(order_id, ccxt_sym)
                cancel_ok = True
                logger.info(
                    "dgr_btc_broker_timeout_canceled",
                    order_id=order_id, market=market.value, filled=str(filled),
                )
            except Exception as e:
                err_str = str(e).lower()
                # binance -2011 "Unknown order" = 单已成或已撤, 视为 cancel ok
                if "unknown order" in err_str or "-2011" in err_str:
                    cancel_ok = True
                    logger.info(
                        "dgr_btc_broker_timeout_cancel_already_gone",
                        order_id=order_id, market=market.value,
                    )
                else:
                    logger.warning(
                        "dgr_btc_broker_timeout_cancel_failed",
                        order_id=order_id, err=str(e)[:120],
                    )

            # 二次确认 final status — cancel 失败或 -2011 都要查
            try:
                od_final = await client.fetch_order(order_id, ccxt_sym)
                final_status = (od_final.get("status") or "open").lower()
                final_filled = Decimal(str(od_final.get("filled") or "0"))
                final_avg = Decimal(str(od_final.get("average") or avg_price_dec))
            except Exception as e:
                logger.exception("dgr_btc_broker_timeout_fetch_failed", order_id=order_id, err=str(e)[:120])
                final_status = "unknown"
                final_filled = filled
                final_avg = avg_price_dec

            if final_status == "open" and not cancel_ok:
                # 危险: 单仍活但 cancel 失败 → trigger kill switch 兜底
                logger.error(
                    "dgr_btc_broker_orphan_order_kill_trigger",
                    order_id=order_id, market=market.value,
                    filled=str(final_filled), status=final_status,
                )
                if self.safety is not None:
                    try:
                        # orphan 仍可能 fill, 保留 pending reservation (不 rollback)
                        # — 如真 fill, sync_fills 路径会 record_filled
                        self.safety.trigger_kill(f"broker_orphan_order_{order_id}")
                    except Exception:
                        logger.exception("dgr_btc_safety_trigger_kill_failed")
                self.n_reject += 1
                raise RejectError(
                    f"orphan_order_alive_after_cancel_fail order_id={order_id} filled={final_filled}",
                )

            # CRITICAL #2: final_status == "unknown" 分支兜底
            # fetch_order 自身抛异常 → 不知道单实际状态, 保留 reservation 占 cap +
            # trigger_kill 让运维介入. 这是 fail-loud, 不让漏单悄悄走.
            if final_status == "unknown":
                logger.error(
                    "dgr_btc_broker_timeout_unknown_kill_trigger",
                    order_id=order_id, market=market.value,
                    filled=str(final_filled), cancel_ok=cancel_ok,
                )
                if self.safety is not None:
                    try:
                        self.safety.trigger_kill(f"broker_unknown_status_{order_id}")
                    except Exception:
                        logger.exception("dgr_btc_safety_trigger_kill_failed")
                self.n_reject += 1
                raise RejectError(
                    f"unknown_status_after_cancel order_id={order_id} cancel_ok={cancel_ok}",
                )

            if final_filled > 0:
                # Partial fill: 返回真实成交 Trade, 让 strategy 吸收
                if self.safety is not None:
                    try:
                        self.safety.record_filled(final_avg * final_filled)
                    except Exception:
                        pass
                logger.warning(
                    "dgr_btc_broker_timeout_partial_fill_recovered",
                    order_id=order_id, market=market.value,
                    filled=str(final_filled), qty=str(quantity), avg=str(final_avg),
                )
                return Trade(
                    trade_id=f"live_{_uuid.uuid4().hex[:12]}",
                    order_id=str(order_id),
                    symbol=self._symbol_str(market),
                    market=market,
                    side=side,
                    price=final_avg,
                    quantity=final_filled,
                    fee=self._estimated_fee(market, final_avg, final_filled, is_maker=True),
                    is_maker=True,
                    timestamp=datetime.now(timezone.utc),
                    grid_level=price,
                )

            self.n_reject += 1
            # C3: 完全 timeout 无 fill (filled=0 且 cancel_ok) → 全额 rollback
            if self.safety is not None and final_filled == 0 and cancel_ok:
                try:
                    self.safety.rollback_reservation(notional_reserved)
                except Exception:
                    pass
            raise RejectError(f"no_fill_within_{int(max_wait)}s filled={final_filled}")

        # Filled successfully
        if self.safety is not None:
            self.safety.record_filled(avg_price_dec * filled)

        return Trade(
            trade_id=f"live_{_uuid.uuid4().hex[:12]}",
            order_id=str(order_id),
            symbol=self._symbol_str(market),
            market=market,
            side=side,
            price=avg_price_dec,
            quantity=filled,
            fee=self._estimated_fee(market, avg_price_dec, filled, is_maker=True),
            is_maker=True,
            timestamp=datetime.now(timezone.utc),
            grid_level=price,
        )

    # ------------------------------------------------------------------
    # 2. MARKET unwind (AtomicPair 单腿失败紧急平腿)
    # ------------------------------------------------------------------

    async def place_market_unwind(
        self,
        market_type: MarketType,
        side: Side,
        quantity: Decimal,
    ) -> Trade:
        """taker market 强平已成交腿. 失败 raise.

        C4 修复 (LIVE 审计): unwind 加独立 safety check, 豁免 kill_switch (kill 后仍需 unwind 平腿),
        但保留单笔上限 (max_order_usd × 5 = 兜底单腿不会太大) + per-hour 计数,
        避免异常 qty 触发任意大单.
        """
        self.n_market_unwind += 1

        # C4: 独立 safety check for unwind
        if self.safety is not None:
            ref_price_check = await self._fetch_mark_price(market_type)
            notional = ref_price_check * quantity
            # unwind 单笔上限 = max_order_usd × 5 (单腿可能比单笔大几倍, 但不能无限大)
            unwind_cap = self.safety.cfg.max_order_usd * Decimal("5")
            if notional > unwind_cap:
                logger.error(
                    "dgr_btc_unwind_blocked_oversized",
                    notional=str(notional), cap=str(unwind_cap),
                    market=market_type.value, side=side.value, qty=str(quantity),
                )
                raise RejectError(
                    f"unwind_oversized: ${notional} > cap ${unwind_cap}",
                )
            # 不检查 kill_switch (kill 后仍须能平单腿)

        if self.dry_run:
            logger.info(
                "dgr_btc_broker_dry_run_unwind",
                market=market_type.value, side=side.value, qty=str(quantity),
            )
            return self._mock_trade(market_type, side, Decimal("0"), quantity, is_maker=False)

        from app.execution.paper_broker import OrderRequest
        from app.exchanges.models import InstrumentType, Side as ExSide
        ex_side = ExSide.BUY if side == Side.BUY else ExSide.SELL
        symbol = self._build_symbol(market_type)
        instrument = (
            InstrumentType.SPOT if market_type == MarketType.SPOT else InstrumentType.PERPETUAL
        )
        # perp 平 short 用 reduce_only
        reduce_only = (
            market_type == MarketType.PERP and side == Side.BUY
        )
        # 用当前 mark 价做 reference (执行时 broker 会用 market order)
        ref_price = await self._fetch_mark_price(market_type)
        request = OrderRequest(
            symbol=symbol, side=ex_side, size=quantity, reference_price=ref_price,
            exchange="binance",
            client_order_id=f"unwind_{_uuid.uuid4().hex[:14]}",
            instrument_type=instrument,
            reduce_only=reduce_only,
        )
        result = await self._broker.execute(request)
        if not result or not getattr(result, "avg_price", None):
            raise RuntimeError("unwind_no_fill")

        return Trade(
            trade_id=f"unwind_{_uuid.uuid4().hex[:12]}",
            order_id=str(getattr(result, "order_id", "")) or f"unw_{_uuid.uuid4().hex[:8]}",
            symbol=self._symbol_str(market_type),
            market=market_type,
            side=side,
            price=Decimal(str(result.avg_price)),
            quantity=Decimal(str(getattr(result, "filled_size", None) or quantity)),
            fee=Decimal(str(getattr(result, "fees", None) or self._estimated_fee(market_type, ref_price, quantity, is_maker=False))),
            is_maker=False,
            timestamp=datetime.now(timezone.utc),
            grid_level=None,
        )

    # ------------------------------------------------------------------
    # 3. cancel / fetch_open_orders (RecenterCancelManager 用)
    # ------------------------------------------------------------------

    async def cancel_order(self, order_id: str) -> None:
        self.n_cancel += 1
        if self.dry_run:
            logger.info("dgr_btc_broker_dry_run_cancel", order_id=order_id)
            return
        # 注意: order_id 必须知道 symbol+instrument 才能撤. 简化: 都试 spot 再 perp
        for market in [MarketType.SPOT, MarketType.PERP]:
            try:
                sym = self._build_symbol(market)
                await self.adapter.cancel_order(order_id, sym)
                logger.info("dgr_btc_broker_canceled", order_id=order_id, market=market.value)
                return
            except Exception as e:
                logger.debug("dgr_btc_broker_cancel_try_failed", order_id=order_id, market=market.value, err=str(e)[:80])
        raise RuntimeError(f"cancel_order_not_found: {order_id}")

    async def fetch_open_orders(self) -> list:
        """直接调 ccxt client (绕过 adapter wrapper) 拿原始 dict.

        adapter.fetch_open_orders 返回 Order object 列表 — sync_fills 需要 dict.
        """
        if self.dry_run:
            return []
        from app.exchanges.models import InstrumentType
        # PM 触发
        try:
            if hasattr(self.adapter, "is_pm_account"):
                await self.adapter.is_pm_account()
        except Exception:
            pass
        out: list = []
        for market in [MarketType.SPOT, MarketType.PERP]:
            instrument = (
                InstrumentType.SPOT if market == MarketType.SPOT
                else InstrumentType.PERPETUAL
            )
            ccxt_sym = (
                f"{self.cfg.base_asset}/{self.cfg.quote_asset}"
                if market == MarketType.SPOT
                else f"{self.cfg.base_asset}/{self.cfg.quote_asset}:{self.cfg.quote_asset}"
            )
            try:
                client = self.adapter._clients[instrument]
                orders = await client.fetch_open_orders(ccxt_sym)
                for o in orders or []:
                    if isinstance(o, dict) and o.get("clientOrderId", "").startswith("dgr_"):
                        o["_dgr_market"] = market.value
                        out.append(o)
            except Exception as e:
                logger.warning(
                    "dgr_btc_broker_fetch_open_failed",
                    market=market.value, err=str(e)[:120],
                )
        return out

    async def fetch_order(self, order_id: str, market: MarketType) -> dict | None:
        """Phase H.2: 查单一订单最终状态（供 _sync_fills 用）。

        返回 ccxt order dict (含 status='closed'/'canceled'/'open', filled, average...) or None.
        """
        if self.dry_run:
            return None
        from app.exchanges.models import InstrumentType
        instrument = (
            InstrumentType.SPOT if market == MarketType.SPOT else InstrumentType.PERPETUAL
        )
        client = self.adapter._clients[instrument]
        ccxt_sym = (
            f"{self.cfg.base_asset}/{self.cfg.quote_asset}" if market == MarketType.SPOT
            else f"{self.cfg.base_asset}/{self.cfg.quote_asset}:{self.cfg.quote_asset}"
        )
        try:
            return await client.fetch_order(order_id, ccxt_sym)
        except Exception as e:
            logger.warning(
                "dgr_btc_broker_fetch_order_failed",
                order_id=order_id, market=market.value, err=str(e)[:120],
            )
            return None

    async def cancel_order_by_market(
        self, order_id: str, market: MarketType
    ) -> bool:
        """带 market hint 的 cancel（比 cancel_order 双试更高效，maintain 用）。"""
        self.n_cancel += 1
        if self.dry_run:
            logger.info("dgr_btc_broker_dry_run_cancel", order_id=order_id, market=market.value)
            return True
        try:
            sym = self._build_symbol(market)
            await self.adapter.cancel_order(order_id, sym)
            logger.info("dgr_btc_broker_canceled", order_id=order_id, market=market.value)
            return True
        except Exception as e:
            err = str(e).lower()
            # "unknown order" / "-2011" → 单已不存在，视作 cancel 成功
            if "unknown order" in err or "-2011" in err or "not found" in err:
                logger.info("dgr_btc_broker_cancel_already_gone", order_id=order_id)
                return True
            logger.warning(
                "dgr_btc_broker_cancel_failed",
                order_id=order_id, market=market.value, err=str(e)[:120],
            )
            return False

    # ------------------------------------------------------------------
    # 4. balance / position (ReconcileTask 用)
    # ------------------------------------------------------------------

    async def get_spot_balance(self, asset: str) -> Decimal:
        """读取 spot/margin 账户 BTC 余额.

        PM / Cross-Margin 用户: 走 sapi_get_margin_account (mirror hedged_grid).
        Classic spot 用户: fallback fetch_balance.
        """
        # 触发 PM 探测 (设置 portfolioMargin option 给 ccxt clients)
        try:
            if hasattr(self.adapter, "is_pm_account"):
                await self.adapter.is_pm_account()
        except Exception:
            pass
        # PM / Cross-Margin: query margin account
        try:
            from app.exchanges.models import InstrumentType  # noqa: PLC0415
            spot_client = self.adapter._clients[InstrumentType.SPOT]
            if hasattr(spot_client, "sapi_get_margin_account"):
                try:
                    ma = await spot_client.sapi_get_margin_account()
                    for a in ma.get("userAssets", []) or []:
                        if a.get("asset") == asset:
                            return Decimal(str(a.get("netAsset") or "0"))
                except Exception as e:
                    logger.debug(
                        "dgr_btc_broker_margin_balance_failed_fallback",
                        asset=asset,
                        err=str(e)[:120],
                    )
        except Exception as e:
            logger.debug("dgr_btc_broker_spot_client_unavailable", err=str(e)[:80])
        # Fallback: regular spot wallet
        try:
            bal = await self.adapter.fetch_balance()
            v = bal.get(asset, {}).get("free", 0) if isinstance(bal, dict) else 0
            return Decimal(str(v))
        except Exception as e:
            logger.warning("dgr_btc_broker_balance_fetch_failed", asset=asset, err=str(e)[:80])
            return Decimal("0")

    async def fetch_margin_borrowed_usdt(self) -> Decimal:
        """CRITICAL #3: 查 PM cross margin 账户当前 USDT 借款余额.

        来源: papi_get_balance() → asset=USDT → crossMarginBorrowed.
        失败时返回 Decimal("0") (保守: 让 safety guard 不要阻塞下单),
        但同时 log warning 让运维知道.
        """
        try:
            if hasattr(self.adapter, "is_pm_account"):
                await self.adapter.is_pm_account()
        except Exception:
            pass
        try:
            from app.exchanges.models import InstrumentType  # noqa: PLC0415
            spot_client = self.adapter._clients[InstrumentType.SPOT]
            if hasattr(spot_client, "papi_get_balance"):
                rows = await spot_client.papi_get_balance()
                for b in rows or []:
                    if b.get("asset") == "USDT":
                        return Decimal(str(b.get("crossMarginBorrowed") or "0"))
        except Exception as e:
            logger.warning(
                "dgr_btc_broker_fetch_borrow_failed", err=str(e)[:120],
            )
        return Decimal("0")

    async def get_perp_position(self, symbol: str) -> Decimal:
        """读取 perp BTC 持仓量 (negative = short).

        PM 用户: 走 papi_get_um_positionrisk (mirror hedged_grid).
        Classic 用户: fallback fetch_positions.
        symbol 接受 ccxt 格式 "BTC/USDT:USDT"; PM endpoint 需要 "BTCUSDT".
        """
        try:
            if hasattr(self.adapter, "is_pm_account"):
                await self.adapter.is_pm_account()
        except Exception:
            pass
        # PM: papi position risk
        try:
            from app.exchanges.models import InstrumentType  # noqa: PLC0415
            spot_client = self.adapter._clients[InstrumentType.SPOT]
            if hasattr(spot_client, "papi_get_um_positionrisk"):
                try:
                    # ccxt "BTC/USDT:USDT" → papi "BTCUSDT"
                    papi_sym = symbol.split(":")[0].replace("/", "")
                    raw = await spot_client.papi_get_um_positionrisk()
                    for p in raw or []:
                        if p.get("symbol") == papi_sym:
                            amt = Decimal(str(p.get("positionAmt") or "0"))
                            return amt  # negative = short, 0 = flat, positive = long
                    return Decimal("0")
                except Exception as e:
                    logger.debug(
                        "dgr_btc_broker_papi_position_failed_fallback",
                        symbol=symbol,
                        err=str(e)[:120],
                    )
        except Exception as e:
            logger.debug("dgr_btc_broker_spot_client_unavailable_perp", err=str(e)[:80])
        # Fallback: regular USDT-M fetch_positions
        try:
            positions = await self.adapter.fetch_positions([symbol])
            for p in positions or []:
                if isinstance(p, dict) and p.get("symbol") == symbol:
                    contracts = p.get("contracts", 0)
                    side = p.get("side", "")
                    if side == "short":
                        return -Decimal(str(contracts))
                    return Decimal(str(contracts))
            return Decimal("0")
        except Exception as e:
            logger.warning("dgr_btc_broker_position_fetch_failed", symbol=symbol, err=str(e)[:80])
            return Decimal("0")

    # ------------------------------------------------------------------
    # 5. DRY_RUN pair check
    # ------------------------------------------------------------------

    async def place_pair_dry(
        self,
        spot_intent: OrderIntent,
        perp_intent: OrderIntent,
    ) -> None:
        logger.info(
            "dgr_btc_broker_pair_dry",
            spot=f"{spot_intent.side.value}@{spot_intent.price}",
            perp=f"{perp_intent.side.value}@{perp_intent.price}",
            qty=str(spot_intent.quantity),
        )

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _build_symbol(self, market: MarketType):
        from app.exchanges.models import Symbol
        # Symbol 不含 instrument_type, 类型由 OrderRequest.instrument_type 单独传
        return Symbol(base=self.cfg.base_asset, quote=self.cfg.quote_asset)

    def _symbol_str(self, market: MarketType) -> str:
        return self.cfg.spot_symbol if market == MarketType.SPOT else self.cfg.perp_symbol

    async def _fetch_mark_price(self, market: MarketType) -> Decimal:
        try:
            sym = self._symbol_str(market)
            ticker = await self.adapter.fetch_ticker(sym)
            return Decimal(str(ticker.get("last", 0)))
        except Exception:
            return Decimal("0")

    def _estimated_fee(self, market: MarketType, price: Decimal, qty: Decimal, is_maker: bool) -> Decimal:
        notional = price * qty
        if market == MarketType.SPOT:
            return notional * self.cfg.fee_rate_spot
        rate = self.cfg.fee_rate_perp_maker if is_maker else self.cfg.fee_rate_perp_taker
        return notional * rate

    def _mock_trade(
        self,
        market: MarketType, side: Side,
        price: Decimal, quantity: Decimal, is_maker: bool,
    ) -> Trade:
        return Trade(
            trade_id=f"dry_{_uuid.uuid4().hex[:12]}",
            order_id=f"dry_{_uuid.uuid4().hex[:8]}",
            symbol=self._symbol_str(market),
            market=market, side=side, price=price, quantity=quantity,
            fee=self._estimated_fee(market, price, quantity, is_maker),
            is_maker=is_maker,
            timestamp=datetime.now(timezone.utc),
            grid_level=price if price > 0 else None,
        )

    def stats(self) -> dict:
        return {
            "n_place_limit_maker": self.n_place_limit_maker,
            "n_reject": self.n_reject,
            "n_market_unwind": self.n_market_unwind,
            "n_cancel": self.n_cancel,
            "dry_run": self.dry_run,
        }
