"""纸交易协调器

将扫描器、执行器、风控三者串联成一个可持续运行的纸交易会话：

  每次 tick（默认 60s）：
    1. 扫描当前资金费率机会
    2. 对每个通过过滤的机会尝试开仓（RiskGuard 内部把关）
    3. 检查所有开仓的风控状态 → 触发则平仓
    4. 若距上次资金费结算已满 8 小时 → 模拟结算

设计原则：
  - 不依赖真实交易所下单（使用 PaperBroker）
  - 不依赖数据库（PositionManager.save 被调用方 mock 或降级处理）
  - 每个方法均为 async，便于与 FastAPI lifespan 集成
  - ``run_once()`` 单次执行供测试和脚本使用

用法::

    session = PaperTradingSession(
        scanner=scanner,
        executor=executor,
        manager=manager,
        size_per_trade_usd=Decimal("500"),
    )
    asyncio.create_task(session.run_forever())
    ...
    await session.stop()
    print(session.status())
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Sequence

from app.core.logging import get_logger
from app.execution.order_executor import OrderExecutor
from app.exchanges.models import Side
from app.notifications import notify_position_closed, notify_position_opened
from app.risk.limits import RiskLimitError
from app.risk.models import ExitReason
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.scanner import FundingRateOpportunity, FundingRateScanner

logger = get_logger(__name__)

_FUNDING_INTERVAL_HOURS = 8


# ---------------------------------------------------------------------------
# 状态快照
# ---------------------------------------------------------------------------


@dataclass
class StatusSnapshot:
    """单次状态快照，用于监控与日志。"""

    timestamp: datetime
    open_positions: int
    total_notional_usd: Decimal
    unrealized_pnl_usd: Decimal       # 基于最新扫描价格的 mark-to-market
    total_funding_usd: Decimal        # 累计资金费收入（所有仓位）
    total_fees_usd: Decimal           # 累计手续费（所有仓位）
    total_trades: int                 # 历史总开仓次数
    net_pnl_usd: Decimal              # 已实现盈亏 + 资金费 − 手续费


# ---------------------------------------------------------------------------
# 协调器
# ---------------------------------------------------------------------------


class PaperTradingSession:
    """纸交易协调器。

    Parameters
    ----------
    scanner:
        资金费率扫描器实例。
    executor:
        订单执行器（内含 PaperBroker）。
    manager:
        仓位管理器（内存 SSOT）。
    size_per_trade_usd:
        每笔仓位名义价值（USD）。
    scan_interval_seconds:
        两次 tick 之间的间隔，默认 60 秒。
    """

    def __init__(
        self,
        scanner: FundingRateScanner,
        executor: OrderExecutor,
        manager: PositionManager,
        size_per_trade_usd: Decimal,
        scan_interval_seconds: float = 60.0,
        pre_funding_window_minutes: float = 15.0,
        min_apr_for_hold_pct: Decimal = Decimal("0"),
        profit_target_pct: Decimal = Decimal("0"),
        perp_margin_loss_threshold_pct: Decimal = Decimal("0"),
    ) -> None:
        self._scanner = scanner
        self._executor = executor
        self._manager = manager
        self._size = size_per_trade_usd
        self._interval = scan_interval_seconds
        self._pre_funding_window_min = pre_funding_window_minutes
        self._min_apr_for_hold = min_apr_for_hold_pct  # 0 = 不检查
        self._profit_target_pct = profit_target_pct    # 0 = 不检查
        self._perp_margin_loss_threshold = perp_margin_loss_threshold_pct  # 0 = 不检查
        self._running = False
        self._last_funding_settled: datetime = datetime.now(UTC)
        self._tick_count: int = 0

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """无限循环，直到外部取消或调用 stop()。"""
        self._running = True
        logger.info(
            "paper_trading_started",
            interval_s=self._interval,
            size_per_trade=str(self._size),
        )
        while self._running:
            loop_start = asyncio.get_event_loop().time()
            await self._tick()
            elapsed = asyncio.get_event_loop().time() - loop_start
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    async def stop(self) -> None:
        """请求停止（下次循环结束后退出）。"""
        self._running = False
        logger.info("paper_trading_stop_requested")

    async def restore(self) -> int:
        """从 DB 恢复 OPEN/PENDING 仓位到内存，避免容器重启后状态丢失。

        必须在 ``run_forever()`` 之前调用，否则去重逻辑会失效，
        导致重启后立即重复开仓。
        """
        count = await self._manager.load_open_positions()
        if count > 0:
            logger.info("paper_trading_state_restored", positions=count)
        return count

    async def run_once(self) -> list[FundingRateOpportunity]:
        """单次执行 tick，返回本次扫描到的机会列表（供测试/脚本使用）。"""
        return await self._tick()

    def status(
        self,
        opportunities: Sequence[FundingRateOpportunity] | None = None,
    ) -> StatusSnapshot:
        """返回当前持仓状态快照。

        Parameters
        ----------
        opportunities:
            若提供，用最新扫描价格计算未实现盈亏；否则未实现盈亏为 0。
        """
        open_pos = self._manager.open_positions
        all_pos = self._manager.all_positions

        total_notional = sum((p.notional_usd for p in open_pos), Decimal("0"))
        total_funding = sum((p.funding_received for p in all_pos), Decimal("0"))
        total_fees = sum((p.fees_paid for p in all_pos), Decimal("0"))
        realized = sum((p.realized_pnl for p in all_pos), Decimal("0"))
        net_pnl = realized + total_funding - total_fees

        unrealized = self._compute_unrealized(open_pos, opportunities or [])

        return StatusSnapshot(
            timestamp=datetime.now(UTC),
            open_positions=len(open_pos),
            total_notional_usd=total_notional,
            unrealized_pnl_usd=unrealized,
            total_funding_usd=total_funding,
            total_fees_usd=total_fees,
            total_trades=len(all_pos),
            net_pnl_usd=net_pnl,
        )

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    async def _tick(self) -> list[FundingRateOpportunity]:
        """单次完整循环：扫描 → 费率翻负检查 → 开仓 → 风控 → 资金费 → 日志。"""
        self._tick_count += 1
        now = datetime.now(UTC)

        opportunities = await self._scan()

        # 先检查已开仓位的费率是否翻负，再尝试开新仓
        # （顺序很重要：先关掉负费率仓，避免新开仓被同 symbol 旧仓阻塞）
        await self._check_funding_flip()

        if opportunities:
            await self._open_positions(opportunities)

        await self._monitor_and_close(now)

        if opportunities:
            await self._maybe_settle_funding(opportunities, now)

        snap = self.status(opportunities)
        logger.info(
            "paper_trading_tick",
            tick=self._tick_count,
            open_positions=snap.open_positions,
            total_trades=snap.total_trades,
            net_pnl=str(snap.net_pnl_usd.quantize(Decimal("0.01"))),
            total_funding=str(snap.total_funding_usd.quantize(Decimal("0.01"))),
            total_fees=str(snap.total_fees_usd.quantize(Decimal("0.01"))),
        )
        return opportunities

    async def _scan(self) -> list[FundingRateOpportunity]:
        """运行扫描器，异常时返回空列表（不中断主循环）。"""
        try:
            return await self._scanner.scan()
        except Exception:
            logger.exception("paper_trading_scan_failed")
            return []

    async def _open_positions(
        self, opportunities: Sequence[FundingRateOpportunity]
    ) -> None:
        """对每个机会尝试开仓。

        过滤顺序：
        0. APR < scanner.min_apr_pct 跳过（现算，避免 PATCH 后 cached opp 过期）
        1. 资金费结算前 N 分钟窗口内才允许开仓（默认 15min）
        2. 已有同标的持仓则跳过
        3. RiskLimitError 和 ValueError 静默跳过
        """
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        window_ms = int(self._pre_funding_window_min * 60 * 1000)
        # 现算 min_apr — 防御式读取（测试 mock 可能没 _config，回退 0 = 全过）
        scfg = getattr(self._scanner, "_config", None)
        min_apr = getattr(scfg, "min_apr_pct", Decimal("0")) if scfg else Decimal("0")
        for opp in opportunities:
            if opp.apr_pct < min_apr:
                continue
            time_to_funding_ms = opp.funding_rate.next_funding_time - now_ms
            if time_to_funding_ms <= 0 or time_to_funding_ms > window_ms:
                logger.debug(
                    "paper_open_skipped_outside_window",
                    symbol=str(opp.symbol),
                    minutes_to_funding=round(time_to_funding_ms / 60_000, 2),
                    window_minutes=self._pre_funding_window_min,
                )
                continue
            if self._manager.get_by_symbol(opp.symbol):
                continue
            try:
                pos = await self._executor.open_delta_neutral(
                    opp, size_usd=self._size
                )
                logger.info(
                    "paper_position_opened",
                    symbol=str(opp.symbol),
                    exchange=opp.exchange,
                    apr_pct=str(opp.apr_pct.quantize(Decimal("0.01"))),
                    position_id=pos.id[:8],
                )
                notify_position_opened(
                    strategy="资金费率套利",
                    symbol=str(opp.symbol),
                    basis_pct=opp.apr_pct.quantize(Decimal("0.01")),
                    notional_usd=self._size,
                )
            except RiskLimitError as e:
                logger.debug(
                    "paper_open_blocked_by_risk",
                    symbol=str(opp.symbol),
                    reason=str(e),
                )
            except Exception:
                logger.exception(
                    "paper_open_unexpected_error", symbol=str(opp.symbol)
                )

    async def _check_funding_flip(self) -> None:
        """实时查询每个开仓 symbol 的当前费率/价格，并按多个动态退出条件平仓：

          1. 净收益 ≥ 阈值       → STRATEGY（止盈）
          2. 费率 ≤ 0           → FUNDING_REVERSAL（继续持仓会倒贴）
          3. APR < 阈值         → STRATEGY（机会衰减）
          4. 永续单腿亏损 ≥ 阈值 → PERP_LIQ_RISK（保护两腿不被强平拆开）

        止损 / 最长持仓由 RiskGuard 在 `_monitor_and_close` 中处理。
        """
        for pos in list(self._manager.open_positions):
            # 1. 净收益止盈检查（不依赖外部数据，先做）
            if self._profit_target_pct > 0 and pos.notional_usd > 0:
                net_pnl = pos.funding_received - pos.fees_paid + pos.realized_pnl
                profit_pct = net_pnl / pos.notional_usd * Decimal("100")
                if profit_pct >= self._profit_target_pct:
                    await self._close_with_reason(
                        pos, ExitReason.STRATEGY,
                        log_event="paper_profit_target_exit",
                        log_extra={"profit_pct": float(profit_pct)},
                    )
                    continue

            # 2. 拉取最新资金费率
            exchange = pos.legs[0].exchange if pos.legs else ""
            if not exchange:
                continue
            fr = await self._scanner.current_rate(exchange, pos.symbol)
            if fr is None:
                continue  # 拉取失败时不动作（保守）

            # 3. 费率翻负
            if fr.rate <= Decimal("0"):
                await self._close_with_reason(
                    pos, ExitReason.FUNDING_REVERSAL,
                    log_event="paper_funding_flip_exit",
                    log_extra={"rate": str(fr.rate)},
                )
                continue

            # 4. APR 低于持仓阈值（机会衰减）
            if self._min_apr_for_hold > 0:
                current_apr = fr.apr * Decimal("100")
                if current_apr < self._min_apr_for_hold:
                    await self._close_with_reason(
                        pos, ExitReason.STRATEGY,
                        log_event="paper_apr_drop_exit",
                        log_extra={
                            "current_apr_pct": float(current_apr),
                            "min_apr_for_hold": float(self._min_apr_for_hold),
                        },
                    )
                    continue

            # 5. 永续单腿保证金亏损保护（防止 perp 被强平后留下 spot 裸多）
            if self._perp_margin_loss_threshold > 0:
                triggered = await self._maybe_close_perp_margin_risk(pos, exchange)
                if triggered:
                    continue

    async def _maybe_close_perp_margin_risk(self, pos, exchange: str) -> bool:
        """如永续 SHORT 腿浮亏达到 perp_margin_loss_threshold_pct% × 初始保证金，
        关闭整个 Delta 中性仓位（双腿同时平）。返回是否触发关仓。

        SHORT 浮亏：(current_price - entry_price) × size > 0 时为亏损。
        当前 5x 杠杆 + 80% 阈值 → 价格上涨约 16% 触发，距清算线 +19.5% 还有 3.5% 缓冲。
        """
        from app.exchanges.models import Side, InstrumentType  # noqa: PLC0415
        perp_leg = next(
            (l for l in pos.legs
             if l.side == Side.SELL and l.instrument_type == InstrumentType.PERPETUAL),
            None,
        )
        if perp_leg is None or perp_leg.size <= 0 or perp_leg.entry_price <= 0:
            return False

        # 初始保证金 = leg.notional / leverage（margin_used 已是这个值）
        initial_margin = perp_leg.margin_used
        if initial_margin <= 0:
            return False

        current_price = await self._scanner.current_perp_price(exchange, pos.symbol)
        if current_price is None:
            return False  # 拉取失败时保守不动

        # SHORT 浮亏（正数 = 亏损）
        unrealized_loss = (current_price - perp_leg.entry_price) * perp_leg.size
        if unrealized_loss <= 0:
            return False  # 没亏损，不触发

        loss_pct = unrealized_loss / initial_margin * Decimal("100")
        if loss_pct < self._perp_margin_loss_threshold:
            return False

        await self._close_with_reason(
            pos, ExitReason.PERP_LIQ_RISK,
            log_event="paper_perp_margin_risk_exit",
            log_extra={
                "perp_loss_pct": float(loss_pct),
                "threshold": float(self._perp_margin_loss_threshold),
                "entry_price": str(perp_leg.entry_price),
                "current_price": str(current_price),
                "leverage": str(perp_leg.leverage),
            },
        )
        return True

    async def _close_with_reason(
        self, pos, reason: ExitReason, log_event: str, log_extra: dict
    ) -> None:
        """统一的关仓 + 通知 + 日志包装。"""
        try:
            await self._executor.close_position(pos.id, reason=reason)
            logger.info(
                log_event,
                position_id=pos.id[:8],
                symbol=str(pos.symbol),
                reason=reason.value,
                realized_pnl=str(pos.realized_pnl.quantize(Decimal("0.01"))),
                **log_extra,
            )
            notify_position_closed(
                strategy="资金费率套利",
                symbol=str(pos.symbol),
                realized_pnl=pos.realized_pnl,
                exit_reason=reason.value,
            )
        except KeyError:
            pass  # 已平仓
        except Exception:
            logger.exception(
                "paper_dynamic_exit_failed",
                position_id=pos.id[:8],
                reason=reason.value,
            )

    async def _monitor_and_close(self, as_of: datetime) -> None:
        """风控检查；对触发规则的仓位执行平仓。"""
        flagged = self._executor.check_all_positions(as_of=as_of)
        for pos, violations in flagged:
            reason = self._violations_to_reason(violations)
            try:
                await self._executor.close_position(pos.id, reason=reason)
                logger.info(
                    "paper_position_closed",
                    position_id=pos.id[:8],
                    reason=reason.value,
                    rules=[v.rule for v in violations],
                    realized_pnl=str(pos.realized_pnl.quantize(Decimal("0.01"))),
                )
                notify_position_closed(
                    strategy="资金费率套利",
                    symbol=str(pos.symbol),
                    realized_pnl=pos.realized_pnl,
                    exit_reason=reason.value,
                )
            except KeyError:
                pass  # 已平仓，安全忽略

    async def _maybe_settle_funding(
        self,
        opportunities: Sequence[FundingRateOpportunity],
        now: datetime,
    ) -> None:
        """若距上次结算已满 8 小时，对开仓位模拟资金费入账。"""
        hours_since = (now - self._last_funding_settled).total_seconds() / 3600
        if hours_since < _FUNDING_INTERVAL_HOURS:
            return

        # (exchange, symbol_str) → (funding_rate, perp_bid_price)
        rate_map: dict[tuple[str, str], Decimal] = {
            (opp.exchange, str(opp.symbol)): opp.funding_rate.rate
            for opp in opportunities
        }
        price_map: dict[tuple[str, str], Decimal] = {
            (opp.exchange, str(opp.symbol)): (
                opp.perp_orderbook.bids[0][0]
                if opp.perp_orderbook.bids
                else Decimal("0")
            )
            for opp in opportunities
        }

        total_settled = Decimal("0")
        for pos in self._manager.open_positions:
            exchange = pos.legs[0].exchange if pos.legs else ""
            key = (exchange, str(pos.symbol))
            rate = rate_map.get(key, Decimal("0"))
            perp_price = price_map.get(key, Decimal("0"))
            if rate <= 0 or perp_price <= 0:
                continue
            perp_leg = next((l for l in pos.legs if l.side == Side.SELL), None)
            if perp_leg is None:
                continue
            funding = perp_leg.size * perp_price * rate
            self._manager.record_funding(pos.id, funding)
            total_settled += funding

        self._last_funding_settled = now
        if total_settled > 0:
            logger.info(
                "paper_funding_settled",
                total_usd=str(total_settled.quantize(Decimal("0.0001"))),
                settled_positions=len(self._manager.open_positions),
            )

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_unrealized(
        open_positions: list,
        opportunities: Sequence[FundingRateOpportunity],
    ) -> Decimal:
        """按最新扫描价格计算所有开仓的未实现盈亏合计。"""
        if not opportunities:
            return Decimal("0")

        spot_map: dict[tuple[str, str], Decimal] = {
            (opp.exchange, str(opp.symbol)): (
                opp.spot_orderbook.asks[0][0]
                if opp.spot_orderbook.asks
                else Decimal("0")
            )
            for opp in opportunities
        }
        perp_map: dict[tuple[str, str], Decimal] = {
            (opp.exchange, str(opp.symbol)): (
                opp.perp_orderbook.bids[0][0]
                if opp.perp_orderbook.bids
                else Decimal("0")
            )
            for opp in opportunities
        }

        total = Decimal("0")
        for pos in open_positions:
            exchange = pos.legs[0].exchange if pos.legs else ""
            key = (exchange, str(pos.symbol))
            spot_px = spot_map.get(key, Decimal("0"))
            perp_px = perp_map.get(key, Decimal("0"))
            for leg in pos.legs:
                if leg.side == Side.BUY and spot_px:
                    total += (spot_px - leg.entry_price) * leg.size
                elif leg.side == Side.SELL and perp_px:
                    total += (leg.entry_price - perp_px) * leg.size
        return total

    @staticmethod
    def _violations_to_reason(violations: list) -> ExitReason:
        rules = {v.rule for v in violations}
        if "stop_loss_pct" in rules:
            return ExitReason.STOP_LOSS
        if "max_hold_hours" in rules:
            return ExitReason.MAX_HOLD_TIME
        return ExitReason.RISK_LIMIT
