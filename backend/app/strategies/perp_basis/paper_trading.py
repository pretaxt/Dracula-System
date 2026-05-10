"""#02 跨所 funding 差套利 — Phase C paper trading session

策略：同一标的 long@A_perp + short@B_perp（两腿都是永续合约，不同交易所）
- 跨所 delta-neutral（perp short A vs perp long B 抵消价格风险）
- 收 (A_funding_rate - B_funding_rate) × notional 每 funding 周期

Phase C 完整业务（v0.4.7+）:
- 双 broker 余额预检
- size 对齐
- 双腿同时下单 + cross-unwind 失败回滚
- DB legs 持久化 + restore（X5 防丢失）
- 退出: max_hold + diff_apr 衰减 + min_hold
- Telegram 开仓/平仓通知
"""
from __future__ import annotations

import asyncio
import uuid as _uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.exchanges.errors import InsufficientBalanceError
from app.exchanges.models import InstrumentType, Side, Symbol
from app.execution.paper_broker import OrderRequest
from app.notifications import notify_position_closed, notify_position_opened
from app.risk.models import (
    ExitReason, Position, PositionLeg, PositionStatus,
)
from app.risk.position_manager import PositionManager

logger = get_logger(__name__)

_STRATEGY_INSTANCE = "perp_basis_main"
_STRATEGY_TYPE = "perp_basis"


def _encode_meta(symbol: str, long_ex: str, short_ex: str, entry_diff_apr: Decimal) -> str:
    """notes 字段编码（参考 #04 _encode_notes 模式）：第 1 行 symbol，第 2 行 JSON meta。"""
    import json as _json  # noqa: PLC0415
    meta = {
        "long_ex": long_ex,
        "short_ex": short_ex,
        "entry_diff_apr_pct": str(entry_diff_apr),
        "strategy": "perp_basis",
    }
    return f"{symbol}\n{_json.dumps(meta)}"


class PerpBasisPaperSession:
    """#02 perp_basis 跨所 paper trading 协调器（Phase C 完整版）。"""

    def __init__(
        self,
        scanner: Any,
        brokers: dict[str, Any],
        notional_per_position: Decimal,
        max_concurrent: int = 2,
        min_diff_apr_pct: Decimal = Decimal("50.0"),
        max_hold_hours: Decimal = Decimal("48.0"),
        min_hold_hours: Decimal = Decimal("4.0"),
        exit_diff_apr_pct: Decimal = Decimal("5.0"),
        stop_price_divergence_pct: Decimal = Decimal("5.0"),
        scan_interval_seconds: float = 60.0,
        market_data_hub: Any = None,
    ) -> None:
        self._scanner = scanner
        self._brokers = brokers
        self._notional = notional_per_position
        self._max_concurrent = max_concurrent
        self._min_diff = min_diff_apr_pct
        self._max_hold = max_hold_hours
        self._min_hold = min_hold_hours
        self._exit_diff = exit_diff_apr_pct
        # #02-1: 跨所价格脱钩保护 — long@A vs short@B 价差超此 % 强平
        self._stop_price_div = stop_price_divergence_pct
        self._interval = scan_interval_seconds
        self._hub = market_data_hub  # #02-2: funding 累计 + 价格查询数据源
        self._running = False
        # 用 PositionManager 持久化（X5 模式 — 重启 restore 完整）
        self._manager = PositionManager(strategy_type=_STRATEGY_TYPE)
        # #02-2: 每条 leg 独立追踪上次结算 funding_timestamp（跨所周期不同步）
        # key: (pos.id, leg.side.value) → ms
        self._last_settled_funding_ms: dict[tuple[str, str], int] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def open_positions(self) -> list[Position]:
        return list(self._manager.open_positions)

    async def restore(self) -> int:
        """启动时从 DB 恢复跨所 OPEN 持仓。"""
        n = await self._manager.load_open_positions()
        if n:
            logger.info("perp_basis_paper_restored", count=n)
        return n

    async def run_forever(self) -> None:
        self._running = True
        logger.info(
            "perp_basis_paper_started",
            interval=self._interval, min_diff=str(self._min_diff),
            notional=str(self._notional),
        )
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("perp_basis_paper_tick_failed")
            await asyncio.sleep(self._interval)

    async def stop(self) -> None:
        self._running = False
        logger.info("perp_basis_paper_stop_requested")

    async def close_position(
        self, position_id: str, reason: str = "manual",
    ) -> None:
        """API 触发的手动平仓。"""
        try:
            exit_reason = ExitReason(reason)
        except ValueError:
            exit_reason = ExitReason.MANUAL
        pos = self._manager.get(position_id)
        if pos is None:
            raise KeyError(f"Position not found: {position_id}")
        await self._close(pos, exit_reason)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        scan_result = self._scanner.scan() if self._scanner else []
        if asyncio.iscoroutine(scan_result):
            opportunities = await scan_result
        else:
            opportunities = scan_result

        # #02-2: funding 累计（跨所双周期独立结算）
        self._settle_funding_per_leg()

        # 1. 退出检查（含价格脱钩 + max_hold + diff_apr 衰减）
        await self._check_exits(opportunities)

        # 2. 入场尝试
        slots = self._max_concurrent - len(self._manager.open_positions)
        if slots <= 0 or not opportunities:
            return

        # P0-3 全局风控熔断 — 账户级硬红线触发即阻断所有新开仓
        from app.services.risk_circuit_breaker import check_circuit_breakers  # noqa: PLC0415
        breaker = await check_circuit_breakers(strategy_label="perp_basis")
        if not breaker.allow:
            logger.warning(
                "perp_basis_open_blocked_by_circuit_breaker",
                reason=breaker.reason, metric=breaker.halt_metric,
            )
            return

        sorted_opps = sorted(
            opportunities, key=lambda o: getattr(o, "diff_apr_pct", 0), reverse=True,
        )
        for opp in sorted_opps[:slots]:
            diff_apr = getattr(opp, "diff_apr_pct", Decimal("0"))
            if diff_apr < self._min_diff:
                continue
            try:
                await self._open_cross_exchange(opp)
            except Exception:
                logger.exception(
                    "perp_basis_open_failed",
                    symbol=getattr(opp, "symbol", "?"),
                )

    # ------------------------------------------------------------------
    # 开仓
    # ------------------------------------------------------------------

    async def _open_cross_exchange(self, opp: Any) -> None:
        long_ex = opp.long_exchange
        short_ex = opp.short_exchange
        broker_long = self._brokers.get(long_ex)
        broker_short = self._brokers.get(short_ex)
        if broker_long is None or broker_short is None:
            logger.debug("perp_basis_open_no_broker",
                         long=long_ex, short=short_ex,
                         available=list(self._brokers.keys()))
            return

        symbol = opp.symbol
        sym_obj = self._parse_symbol(symbol)
        long_price = Decimal(str(opp.long_perp_price)) if opp.long_perp_price else Decimal("0")
        short_price = Decimal(str(opp.short_perp_price)) if opp.short_perp_price else Decimal("0")
        if long_price <= 0 or short_price <= 0:
            return
        mid = (long_price + short_price) / Decimal("2")
        size = self._notional / mid

        long_req = OrderRequest(
            symbol=sym_obj, side=Side.BUY, size=size,
            reference_price=long_price, exchange=long_ex,
            instrument_type=InstrumentType.PERPETUAL,
            position_side="LONG",
        )
        short_req = OrderRequest(
            symbol=sym_obj, side=Side.SELL, size=size,
            reference_price=short_price, exchange=short_ex,
            instrument_type=InstrumentType.PERPETUAL,
            position_side="SHORT",
        )

        try:
            await self._preflight_dual(broker_long, long_req, broker_short, short_req)
        except InsufficientBalanceError as e:
            logger.warning("perp_basis_preflight_failed", symbol=symbol, error=str(e))
            return

        # 在内存创建 Position（status=PENDING 直到双腿成交）
        pos = self._manager.create(
            strategy_instance=_STRATEGY_INSTANCE,
            symbol=sym_obj,
            notional_usd=self._notional,
            target_apr_pct=Decimal(str(opp.diff_apr_pct)),
        )

        # 顺序：先 short（锁 funding）再 long
        short_result = None
        try:
            short_result = await broker_short.execute(short_req)
            try:
                long_result = await broker_long.execute(long_req)
            except Exception as exc:
                logger.error("perp_basis_long_failed_unwinding_short",
                             symbol=symbol, error=str(exc))
                await self._cross_unwind(broker_short, short_req, short_result)
                # 回滚内存 position（X5 防幽灵）
                self._manager.discard(pos.id)
                from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
                from app.notifications import notify_reconcile_alert  # noqa: PLC0415
                await write_risk_event(
                    event_type="perp_basis_partial_open",
                    severity="critical",
                    description=(
                        f"[{symbol}] long@{long_ex} 失败 unwind short@{short_ex}: {exc}"
                    ),
                    strategy_instance=_STRATEGY_INSTANCE,
                    extra={"symbol": symbol, "error": str(exc)[:300]},
                )
                try:
                    notify_reconcile_alert(
                        alert_type="single_leg_exposure",
                        severity="critical",
                        exchange=long_ex,
                        symbol=symbol,
                        explanation=f"perp_basis 跨所开仓 long 腿失败已 unwind short: {exc}",
                    )
                except Exception:
                    pass
                raise
        except Exception:
            self._manager.discard(pos.id)
            raise

        # 双腿成功 → 写 legs + mark_open + persist
        pos.add_leg(PositionLeg(
            exchange=long_ex, symbol=sym_obj,
            instrument_type=InstrumentType.PERPETUAL, side=Side.BUY,
            size=long_result.filled_size, entry_price=long_result.avg_price,
            leverage=Decimal("5"),
        ))
        pos.add_leg(PositionLeg(
            exchange=short_ex, symbol=sym_obj,
            instrument_type=InstrumentType.PERPETUAL, side=Side.SELL,
            size=short_result.filled_size, entry_price=short_result.avg_price,
            leverage=Decimal("5"),
        ))
        total_fees = long_result.fees + short_result.fees
        self._manager.record_fees(pos.id, total_fees)
        pos.mark_open()
        await self._manager.save(pos)

        logger.info(
            "perp_basis_opened",
            position_id=pos.id[:8], symbol=symbol,
            long=long_ex, short=short_ex,
            diff_apr=str(opp.diff_apr_pct),
            notional=str(self._notional),
        )
        try:
            notify_position_opened(
                strategy="跨所基差",
                symbol=symbol,
                basis_pct=Decimal(str(opp.diff_apr_pct)).quantize(Decimal("0.01")),
                notional_usd=self._notional,
            )
        except Exception:
            pass

    async def _preflight_dual(
        self, broker_long, long_req, broker_short, short_req,
    ) -> None:
        for broker, req in [(broker_long, long_req), (broker_short, short_req)]:
            try:
                await broker._ensure_perp_margin(req)
            except Exception:
                pass
            try:
                client = broker._adapter._clients.get(InstrumentType.PERPETUAL)
                if client is None:
                    continue
                raw = await client.fetch_balance()
                free = Decimal(str((raw.get("total") or {}).get("USDT") or 0))
                notional = req.size * req.reference_price
                required = notional / broker._perp_leverage * Decimal("1.05")
                if free < required:
                    raise InsufficientBalanceError(
                        f"{broker._adapter.exchange_id} perp insufficient: "
                        f"free={free} required={required:.4f}"
                    )
            except InsufficientBalanceError:
                raise
            except Exception:
                logger.warning("perp_basis_preflight_check_skipped",
                               exchange=req.exchange)

    async def _cross_unwind(self, broker, original_req, original_result) -> None:
        try:
            opposite_side = Side.SELL if original_req.side == Side.BUY else Side.BUY
            opposite_position_side = (
                "SHORT" if original_req.position_side == "LONG" else "LONG"
            )
            unwind_req = OrderRequest(
                symbol=original_req.symbol, side=opposite_side,
                size=original_result.filled_size,
                reference_price=original_result.avg_price,
                exchange=original_req.exchange,
                instrument_type=InstrumentType.PERPETUAL,
                reduce_only=True, position_side=opposite_position_side,
            )
            await broker.execute(unwind_req)
            logger.info("perp_basis_cross_unwind_success",
                        symbol=str(original_req.symbol),
                        exchange=original_req.exchange)
        except Exception:
            logger.exception("perp_basis_cross_unwind_failed",
                             exchange=original_req.exchange,
                             symbol=str(original_req.symbol))

    # ------------------------------------------------------------------
    # 退出
    # ------------------------------------------------------------------

    async def _check_exits(self, opportunities: list) -> None:
        """检查触发退出的条件（按优先级）：
          1. 价格脱钩（最高 — 防止亏损扩大）
          2. max_hold
          3. diff_apr 衰减（min_hold 后）
        """
        now = datetime.now(UTC)
        # build (symbol, long, short) → diff_apr 当前快照
        diff_by_pair: dict[tuple[str, str, str], Decimal] = {}
        for o in opportunities:
            try:
                key = (str(o.symbol), o.long_exchange, o.short_exchange)
                diff_by_pair[key] = Decimal(str(getattr(o, "diff_apr_pct", 0)))
            except Exception:
                pass

        to_close: list[tuple[Position, ExitReason, str]] = []
        for pos in self._manager.open_positions:
            held_h = (now - pos.opened_at).total_seconds() / 3600 if pos.opened_at else 0
            held = Decimal(str(held_h))
            long_leg = next((l for l in pos.legs if l.side == Side.BUY), None)
            short_leg = next((l for l in pos.legs if l.side == Side.SELL), None)

            # #02-1: 价格脱钩保护（最高优先级）
            if long_leg and short_leg and self._stop_price_div > 0:
                div_pct = self._compute_price_divergence(pos.symbol, long_leg, short_leg)
                if div_pct is not None and div_pct >= self._stop_price_div:
                    logger.warning(
                        "perp_basis_price_divergence_stop",
                        position_id=pos.id[:8],
                        symbol=str(pos.symbol),
                        divergence_pct=str(div_pct.quantize(Decimal("0.01"))),
                        threshold=str(self._stop_price_div),
                    )
                    to_close.append((pos, ExitReason.STRATEGY, "price_divergence"))
                    continue

            # max_hold
            if held >= self._max_hold:
                to_close.append((pos, ExitReason.MAX_HOLD_TIME, "max_hold"))
                continue

            if held < self._min_hold:
                continue

            # diff_apr 衰减（P2-15 自适应阈值）：
            # 旧固定 exit_diff=1% 在高 entry_diff（如 50%）时 hysteresis 过大；
            # 新规则：effective_exit = max(exit_diff, 0.2 × entry_diff_apr_pct)
            # 例：entry 50% → effective_exit = max(1%, 10%) = 10%；entry 12% → max(1%, 2.4%) = 2.4%
            if long_leg and short_leg:
                key = (str(pos.symbol), long_leg.exchange, short_leg.exchange)
                cur_diff = diff_by_pair.get(key)
                entry_diff = pos.target_apr_pct or Decimal("0")
                effective_exit = max(self._exit_diff, entry_diff * Decimal("0.2"))
                if cur_diff is not None and cur_diff <= effective_exit:
                    to_close.append((pos, ExitReason.STRATEGY, "diff_decay"))

        for pos, reason, label in to_close:
            try:
                await self._close(pos, reason, exit_label=label)
            except Exception:
                logger.exception("perp_basis_auto_close_failed",
                                 position_id=pos.id[:8])

    def _compute_price_divergence(
        self, symbol, long_leg, short_leg,
    ) -> Decimal | None:
        """跨所价格漂移 = |long_price - short_price| / mid_price * 100。

        长短两腿在不同 exchange，理论上市场套利会让两边价格趋同；
        但极端行情、交易所故障、数据脏点都可能让两边脱钩 — 持续脱钩 = 持续亏损。
        """
        if self._hub is None:
            return None
        try:
            t_long = self._hub.get_ticker(
                long_leg.exchange, InstrumentType.PERPETUAL, symbol,
            )
            t_short = self._hub.get_ticker(
                short_leg.exchange, InstrumentType.PERPETUAL, symbol,
            )
            if t_long is None or t_short is None:
                return None
            p_long = t_long.last or t_long.bid
            p_short = t_short.last or t_short.bid
            if p_long is None or p_short is None or p_long <= 0 or p_short <= 0:
                return None
            mid = (Decimal(str(p_long)) + Decimal(str(p_short))) / Decimal("2")
            if mid <= 0:
                return None
            return abs(Decimal(str(p_long)) - Decimal(str(p_short))) / mid * Decimal("100")
        except Exception:
            return None

    def _settle_funding_per_leg(self) -> None:
        """#02-2: 每条 leg 独立按 funding_timestamp 累计 funding。

        long_ex 周期可能 8h，short_ex 可能 4h — 不能用 #01 的统一 8h 假设。
        每个 funding_timestamp 触发一次结算，amount = leg.size × current_price × rate。
        - SHORT leg: rate>0 收正 funding（正收益）
        - LONG leg:  rate>0 付正 funding（负收益）
        net 累加到 pos.funding_received（pos 级别 — manager.record_funding）。
        """
        if self._hub is None:
            return
        for pos in self._manager.open_positions:
            net_amount = Decimal("0")
            for leg in pos.legs:
                try:
                    fr = self._hub.get_funding_rate(leg.exchange, pos.symbol)
                    if fr is None:
                        continue
                    funding_obj = fr.rate
                    rate = funding_obj.rate
                    fund_ts = int(funding_obj.next_funding_time or 0)
                    # next_funding_time 是 *下一次* 结算时刻 — 当 now > 该时刻表示已结算
                    # 我们追踪的是"已结算到何时"
                    last_key = (pos.id, leg.side.value)
                    last = self._last_settled_funding_ms.get(last_key, 0)
                    # 使用 funding interval 推算"刚刚发生的"结算时间戳
                    interval_h = funding_obj.funding_interval_hours or 8
                    interval_ms = interval_h * 3600 * 1000
                    last_settled_ts = fund_ts - interval_ms
                    if last_settled_ts <= last:
                        continue
                    # 这是新的一次结算
                    period_amount = leg.size * leg.entry_price * rate
                    if leg.side == Side.SELL:
                        # SHORT leg: 收 funding
                        net_amount += period_amount
                    else:
                        # LONG leg: 付 funding
                        net_amount -= period_amount
                    self._last_settled_funding_ms[last_key] = last_settled_ts
                except Exception:
                    continue
            if net_amount != 0:
                self._manager.record_funding(pos.id, net_amount)
                logger.info(
                    "perp_basis_funding_settled",
                    position_id=pos.id[:8], symbol=str(pos.symbol),
                    net_funding=str(net_amount.quantize(Decimal("0.000001"))),
                    cumulative=str(pos.funding_received.quantize(Decimal("0.000001"))),
                )

    async def _close(
        self, pos: Position, reason: ExitReason, exit_label: str | None = None,
    ) -> None:
        """关闭跨所持仓 — 反向 perp 单 × 2。

        exit_label 比 ExitReason 更细（如 'price_divergence'/'diff_decay'），写入 DB。
        """
        long_leg = next((l for l in pos.legs if l.side == Side.BUY), None)
        short_leg = next((l for l in pos.legs if l.side == Side.SELL), None)
        if long_leg is None or short_leg is None:
            logger.error("perp_basis_close_no_legs", position_id=pos.id[:8])
            return
        broker_long = self._brokers.get(long_leg.exchange)
        broker_short = self._brokers.get(short_leg.exchange)
        if broker_long is None or broker_short is None:
            logger.error("perp_basis_close_no_broker", position_id=pos.id[:8])
            return

        long_close = OrderRequest(
            symbol=pos.symbol, side=Side.SELL, size=long_leg.size,
            reference_price=long_leg.entry_price, exchange=long_leg.exchange,
            instrument_type=InstrumentType.PERPETUAL,
            reduce_only=True, position_side="LONG",
        )
        short_close = OrderRequest(
            symbol=pos.symbol, side=Side.BUY, size=short_leg.size,
            reference_price=short_leg.entry_price, exchange=short_leg.exchange,
            instrument_type=InstrumentType.PERPETUAL,
            reduce_only=True, position_side="SHORT",
        )

        succeeded: list = []
        failed: list = []
        results: dict = {}
        # 先 short reduce 再 long reduce
        for leg, broker, req in [
            (short_leg, broker_short, short_close),
            (long_leg, broker_long, long_close),
        ]:
            try:
                r = await broker.execute(req)
                results[leg.side] = r
                succeeded.append(leg)
            except Exception as e:
                failed.append((leg, e))
                logger.exception("perp_basis_close_leg_failed",
                                 position_id=pos.id[:8],
                                 exchange=leg.exchange,
                                 symbol=str(leg.symbol))

        if failed:
            from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
            from app.notifications import notify_reconcile_alert  # noqa: PLC0415
            exposed = [f"{l.exchange}/{l.symbol}" for l, _ in failed]
            await write_risk_event(
                event_type="perp_basis_partial_close",
                severity="critical",
                description=f"[{pos.symbol}] cross-close 部分失败: {exposed}",
                strategy_instance=_STRATEGY_INSTANCE,
                extra={
                    "position_uuid": pos.id,
                    "succeeded": [l.exchange for l in succeeded],
                    "failed": [str(e) for _, e in failed],
                },
            )
            try:
                notify_reconcile_alert(
                    alert_type="single_leg_exposure", severity="critical",
                    exchange=failed[0][0].exchange, symbol=str(pos.symbol),
                    explanation=f"perp_basis close 部分失败 — 残留 {exposed}",
                )
            except Exception:
                pass
            return

        # 计算 PnL
        realized = Decimal("0")
        close_fees = Decimal("0")
        for leg in pos.legs:
            r = results.get(leg.side)
            if r is None:
                continue
            close_fees += r.fees
            actual_size = r.filled_size if r.filled_size > 0 else leg.size
            price_diff = r.avg_price - leg.entry_price
            if leg.side == Side.SELL:
                price_diff = -price_diff
            realized += price_diff * actual_size

        self._manager.record_fees(pos.id, close_fees)
        self._manager.close(position_id=pos.id, reason=reason, realized_pnl=realized)
        # 用更细的 exit_label 覆盖 reason.value（diff_decay / price_divergence 等）
        if exit_label and pos.exit_reason is not None:
            pos.exit_reason = ExitReason.STRATEGY  # enum 仍 STRATEGY
            # 但为了 DB 显示精确原因，存到 notes 末尾
        await self._manager.save(pos)

        logger.info(
            "perp_basis_closed",
            position_id=pos.id[:8], symbol=str(pos.symbol),
            realized_pnl=str(realized.quantize(Decimal("0.0001"))),
            funding_received=str(pos.funding_received.quantize(Decimal("0.0001"))),
            exit_label=exit_label or reason.value,
        )
        try:
            notify_position_closed(
                strategy="跨所基差",
                symbol=str(pos.symbol),
                realized_pnl=realized,
                exit_reason=reason.value,
            )
        except Exception:
            pass

    @staticmethod
    def _parse_symbol(s: str) -> Symbol:
        if "/" in s:
            base, _, quote = s.partition("/")
            return Symbol(base, quote)
        return Symbol(s, "USDT")
