"""SpotPerpPaperSession — spot-perp 基差套利会话（paper + live）。

开仓:
  scanner 检测到 |basis_pct| >= ENTRY_PCT 且当前持仓数 < MAX_CONCURRENT,
  写入 PositionRecord(strategy_instance="spot_perp_main"):
    target_apr_pct = signed basis_pct(开仓时基差,正=premium 负=discount)
    notional_usd = NOTIONAL
    notes = symbol  (live 模式下追加 \\n{json} 存 entry-leg metadata)
    fees_paid = 一次性预扣往返费

每次 tick(60s):
  对每个 open 仓位:
    current_basis = scanner 当前 basis_pct (若不在 opps 列表 = |basis|<threshold,近似 0)
    captured_pct = abs(entry_basis) - abs(current_basis)
    unrealized_pnl = captured_pct * notional / 100 - fees
  平仓条件:
    - basis 收敛(|current| <= EXIT_PCT)
    - 持仓时长 >= MAX_HOLD_HOURS

LIVE 模式 (D.1):
  - 仅激活 premium 方向 (perp > spot): LONG spot + SHORT perp
  - discount 方向需要 spot margin short, 暂跳过 live, 仅记 paper PnL
  - 复用 LiveBroker.execute_pair, 跟 funding_rate 同款双腿原子
  - 实际成交 spot_size/spot_px/perp_px 序列化进 notes 末尾以支持平仓
"""
from __future__ import annotations

import asyncio
import json
import uuid as uuid_lib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.logging import get_logger
from app.exchanges.models import InstrumentType, Side, Symbol
from app.execution.paper_broker import OrderRequest
from app.models.position import PositionRecord
from app.notifications import notify_position_closed, notify_position_opened
from app.strategies.spot_perp_basis.runner import SpotPerpRunner

logger = get_logger(__name__)


# 默认参数（兜底）— 实际优先读 config/strategies/spot_perp_main.yaml
ENTRY_PCT = Decimal("0.30")       # |basis_pct| >= 0.30% 入场（fee 0.16% + 缓冲）
EXIT_PCT = Decimal("0.03")        # |basis_pct| <= 0.03% 收敛平仓
MAX_HOLD_HOURS = Decimal("12")    # 12 小时强制平仓
MAX_CONCURRENT = 3
NOTIONAL_PER_POSITION = Decimal("50")
ROUND_TRIP_FEE_USD = Decimal("0.50")  # 现货+永续两腿往返费，paper 简化
_FUNDING_REFRESH_EVERY_N_TICKS = 5    # 实时 funding 累计每 N 个 tick 刷新（5*60s=5 分钟）


# ---------------------------------------------------------------------------
# 策略配置（yaml 化）
# ---------------------------------------------------------------------------


@dataclass
class SpotPerpStrategyConfig:
    """spot-perp 策略运行参数（来自 yaml 或运行时 override）。"""

    enabled: bool = True
    notional_per_position: Decimal = NOTIONAL_PER_POSITION
    max_concurrent: int = MAX_CONCURRENT
    max_hold_hours: Decimal = MAX_HOLD_HOURS
    min_hold_minutes: Decimal = Decimal("5")  # 防 scanner 抖动 → 1-3min 内噪音平仓
    entry_pct: Decimal = ENTRY_PCT
    exit_pct: Decimal = EXIT_PCT
    # a — 基差扩大止损：当前基差较入场扩大 ≥ 该值（pct），立止防扛飞刀。0=禁用。
    stop_basis_widening_pct: Decimal = Decimal("0.50")
    # c — 方向独立入场阈值。0 = 回退用 entry_pct（向下兼容）。
    # discount 方向有 borrow + 付 funding 双层成本，应设比 premium 更高门槛。
    entry_pct_premium: Decimal = Decimal("0")
    entry_pct_discount: Decimal = Decimal("0")
    round_trip_fee_usd: Decimal = ROUND_TRIP_FEE_USD
    direction_filter: str = "premium"  # "premium" | "discount" | "both"
    candidate_symbols: list[str] = field(default_factory=list)
    exchanges: list[str] = field(default_factory=list)
    scan_threshold_pct: Decimal = Decimal("0.10")
    # b — 入场时机过滤（防接飞刀）：要求 |basis| 已从近 N 分钟峰值回落 ≥ M%
    # 0 = 禁用（旧行为，见到阈值即入场）
    peak_window_minutes: Decimal = Decimal("10")
    min_peak_dropoff_pct: Decimal = Decimal("0.05")

    @classmethod
    def from_yaml(cls, data: dict | None) -> "SpotPerpStrategyConfig":
        if not data:
            return cls()
        entry = data.get("entry") or {}
        exit_ = data.get("exit") or {}
        position = data.get("position") or {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            entry_pct=Decimal(str(entry.get("min_basis_pct", ENTRY_PCT))),
            entry_pct_premium=Decimal(str(entry.get("min_basis_pct_premium", "0") or "0")),
            entry_pct_discount=Decimal(str(entry.get("min_basis_pct_discount", "0") or "0")),
            scan_threshold_pct=Decimal(str(entry.get("scan_threshold_pct", "0.10"))),
            exit_pct=Decimal(str(exit_.get("basis_convergence_pct", EXIT_PCT))),
            max_hold_hours=Decimal(str(exit_.get("max_hold_hours", MAX_HOLD_HOURS))),
            min_hold_minutes=Decimal(str(exit_.get("min_hold_minutes", "5"))),
            stop_basis_widening_pct=Decimal(
                str(exit_.get("stop_basis_widening_pct", "0.50") or "0"),
            ),
            peak_window_minutes=Decimal(
                str(entry.get("peak_window_minutes", "10") or "0"),
            ),
            min_peak_dropoff_pct=Decimal(
                str(entry.get("min_peak_dropoff_pct", "0.05") or "0"),
            ),
            max_concurrent=int(position.get("max_positions", MAX_CONCURRENT)),
            notional_per_position=Decimal(str(position.get("size_usd", NOTIONAL_PER_POSITION))),
            direction_filter=str(position.get("direction_filter", "premium")).lower(),
            candidate_symbols=list(position.get("candidate_symbols") or []),
            exchanges=list(position.get("exchanges") or []),
        )

    def apply_overrides(self, overrides: dict) -> "SpotPerpStrategyConfig":
        """生成应用了 UI override 的新副本（不可变模式）。"""
        if not overrides:
            return self
        # 仅接受白名单字段
        new = SpotPerpStrategyConfig(
            enabled=bool(overrides.get("enabled", self.enabled)),
            entry_pct=Decimal(str(overrides.get("entry_pct", self.entry_pct))),
            entry_pct_premium=Decimal(str(overrides.get("entry_pct_premium", self.entry_pct_premium))),
            entry_pct_discount=Decimal(str(overrides.get("entry_pct_discount", self.entry_pct_discount))),
            exit_pct=Decimal(str(overrides.get("exit_pct", self.exit_pct))),
            max_hold_hours=Decimal(str(overrides.get("max_hold_hours", self.max_hold_hours))),
            min_hold_minutes=Decimal(str(overrides.get("min_hold_minutes", self.min_hold_minutes))),
            stop_basis_widening_pct=Decimal(
                str(overrides.get("stop_basis_widening_pct", self.stop_basis_widening_pct)),
            ),
            peak_window_minutes=Decimal(
                str(overrides.get("peak_window_minutes", self.peak_window_minutes)),
            ),
            min_peak_dropoff_pct=Decimal(
                str(overrides.get("min_peak_dropoff_pct", self.min_peak_dropoff_pct)),
            ),
            max_concurrent=int(overrides.get("max_concurrent", self.max_concurrent)),
            notional_per_position=Decimal(str(overrides.get("notional_per_position", self.notional_per_position))),
            direction_filter=str(overrides.get("direction_filter", self.direction_filter)).lower(),
            scan_threshold_pct=Decimal(str(overrides.get("scan_threshold_pct", self.scan_threshold_pct))),
            candidate_symbols=list(overrides.get("candidate_symbols", self.candidate_symbols)),
            exchanges=list(overrides.get("exchanges", self.exchanges)),
            round_trip_fee_usd=self.round_trip_fee_usd,
        )
        return new

    def entry_threshold_for(self, direction: str) -> Decimal:
        """c — 返回某方向的入场阈值。per-direction > 0 时优先，否则回退 entry_pct。"""
        d = (direction or "").lower()
        if d == "discount" and self.entry_pct_discount > 0:
            return self.entry_pct_discount
        if d == "premium" and self.entry_pct_premium > 0:
            return self.entry_pct_premium
        return self.entry_pct


# ---------------------------------------------------------------------------
# notes 字段编解码 — 复用 PositionRecord.notes 存 live leg metadata
# ---------------------------------------------------------------------------


def _encode_notes(symbol: str, meta: dict | None = None) -> str:
    """符号编码进首行；live metadata 序列化进第二行 JSON。"""
    if not meta:
        return symbol
    return f"{symbol}\n{json.dumps(meta, separators=(',', ':'))}"


def _decode_notes(notes: str) -> tuple[str, dict | None]:
    """从 notes 解出 (symbol, meta_dict | None)。兼容旧记录（仅 symbol）。"""
    if not notes:
        return "", None
    if "\n" not in notes:
        return notes, None
    head, _, tail = notes.partition("\n")
    try:
        meta = json.loads(tail)
        if not isinstance(meta, dict):
            return head, None
        return head, meta
    except (ValueError, TypeError):
        return head, None


def _symbol_from_pair(pair: str) -> Symbol:
    """'BTC/USDT' -> Symbol('BTC', 'USDT')。"""
    base, _, quote = pair.partition("/")
    return Symbol(base, quote or "USDT")


class SpotPerpPaperSession:
    """spot-perp 基差套利 paper trading 调度器。"""

    STRATEGY_INSTANCE = "spot_perp_main"
    STRATEGY_TYPE = "spot_perp"

    def __init__(
        self,
        runner: SpotPerpRunner,
        tick_interval_seconds: float = 60.0,
        live_mode: bool = False,
        brokers: dict | None = None,
        notional_per_position: Decimal | None = None,
        strategy_config: SpotPerpStrategyConfig | None = None,
    ) -> None:
        """spot-perp 套利会话。

        Parameters
        ----------
        live_mode:
            True = 真实下单（仅 premium 方向）；False = 仅记 PositionRecord 模拟。
        brokers:
            ``{exchange_name: LiveBroker}`` 路由表，live_mode=True 时必填。
        notional_per_position:
            单仓位名义（USD），优先级最高（覆盖 strategy_config）；保留兼容。
        strategy_config:
            从 yaml 加载的运行参数；缺省使用模块常量兜底。
        """
        if live_mode and not brokers:
            raise ValueError("live_mode=True requires non-empty brokers dict")
        self._runner = runner
        self._tick_interval = tick_interval_seconds
        self._live_mode = bool(live_mode)
        self._brokers = brokers or {}
        self._cfg = strategy_config or SpotPerpStrategyConfig()
        # D.2.b 实时 funding：每 N 个 tick 刷新一次，避免每 60s 都打 API
        self._tick_counter = 0
        self._funding_cache: dict[str, Decimal] = {}  # uuid → cached funding USDT
        # b — 入场时机过滤：每 symbol 维护近 N 分钟的 |basis| 滑窗
        # key = symbol_pair (e.g. "BTC/USDT"), value = list of (timestamp_ms, abs_basis_pct)
        self._basis_peak_cache: dict[str, list[tuple[int, Decimal]]] = {}
        # 显式 notional 参数兼容旧调用，覆盖 cfg
        if notional_per_position is not None:
            self._cfg = self._cfg.apply_overrides(
                {"notional_per_position": notional_per_position}
            )
        self._stop_event = asyncio.Event()
        self._running = False
        self._last_tick_at: datetime | None = None

    @property
    def live_mode(self) -> bool:
        return self._live_mode

    @property
    def cfg(self) -> SpotPerpStrategyConfig:
        """当前生效的策略配置（运行时 override 后）。"""
        return self._cfg

    def update_cfg(self, overrides: dict) -> None:
        """热更新策略配置（下一 tick 生效）— UI 调阈值时调用。"""
        self._cfg = self._cfg.apply_overrides(overrides)
        logger.info("spot_perp_cfg_updated", **{k: str(v) for k, v in overrides.items()})

    # 兼容旧 API（test 用）
    @property
    def _notional(self) -> Decimal:
        return self._cfg.notional_per_position

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    async def run_forever(self) -> None:
        self._running = True
        logger.info("spot_perp_paper_session_started", interval=self._tick_interval)
        try:
            while not self._stop_event.is_set():
                try:
                    async with get_session() as session:
                        await self._tick(session)
                    self._last_tick_at = datetime.now(timezone.utc)
                except Exception:
                    logger.exception("spot_perp_paper_tick_failed")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._tick_interval
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            self._running = False
            logger.info("spot_perp_paper_session_stopped")

    def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Core tick logic
    # ------------------------------------------------------------------

    async def _tick(self, session: AsyncSession) -> None:
        opps = self._runner.latest_opportunities or []
        opps_by_sym = {o.symbol: o for o in opps}

        stmt = (
            select(PositionRecord)
            .where(PositionRecord.strategy_instance == self.STRATEGY_INSTANCE)
            .where(PositionRecord.status == "open")
        )
        open_rows = (await session.execute(stmt)).scalars().all()
        existing_syms = {_decode_notes(r.notes)[0] for r in open_rows}

        now = datetime.now(timezone.utc)

        # D.2.b 实时 funding：每 N 个 tick 刷新一次缓存
        self._tick_counter += 1
        refresh_funding = (
            self._live_mode
            and self._tick_counter % _FUNDING_REFRESH_EVERY_N_TICKS == 1
        )

        closed_count = 0
        for r in open_rows:
            sym, meta = _decode_notes(r.notes)
            entry_basis = Decimal(str(r.target_apr_pct or 0))
            cur = opps_by_sym.get(sym)
            current_basis = (
                Decimal(str(cur.basis_pct)) if cur is not None else Decimal("0")
            )

            captured_pct = abs(entry_basis) - abs(current_basis)
            formula_pnl = (
                captured_pct * (r.notional_usd or Decimal("0")) / Decimal("100")
                - (r.fees_paid or Decimal("0"))
            )

            # D.2.b 实时 funding 加成（仅 live spot_perp 有 meta 时）
            live_funding = await self._get_live_funding(r, meta) if (
                self._live_mode and meta and r.opened_at and refresh_funding
            ) else self._funding_cache.get(r.uuid, Decimal("0"))

            pnl = formula_pnl + live_funding
            r.unrealized_pnl = pnl
            if live_funding != 0:
                r.funding_received = live_funding

            held_seconds = (now - r.opened_at).total_seconds() if r.opened_at else 0.0
            held_hours = Decimal(str(held_seconds / 3600))

            should_close = False
            exit_reason: str | None = None
            held_minutes = held_hours * Decimal("60")
            # min_hold_minutes 门槛：防 1-3 min 内 scanner 抖动触发噪音平仓
            min_hold_passed = held_minutes >= self._cfg.min_hold_minutes
            # a — 基差扩大止损（方向感知）：current 比 entry 朝相反方向走得太远即止
            widening = self._basis_widened_pct(entry_basis, current_basis)
            stop_threshold = self._cfg.stop_basis_widening_pct
            if abs(current_basis) <= self._cfg.exit_pct and min_hold_passed:
                should_close = True
                exit_reason = "basis_convergence"
            elif (
                stop_threshold > 0
                and widening >= stop_threshold
                and min_hold_passed
            ):
                should_close = True
                exit_reason = "basis_stop"
            elif held_hours >= self._cfg.max_hold_hours:
                should_close = True
                exit_reason = "max_hold"

            if should_close:
                # live：先反向下单两条腿；任何一条失败则保留 open 留待下一 tick 重试
                pnl_source = "formula"
                final_pnl = pnl
                close_fees_total = Decimal("0")
                funding_decimal = Decimal("0")
                borrow_decimal = Decimal("0")
                if self._live_mode and meta:
                    opened_at_ms = (
                        int(r.opened_at.timestamp() * 1000)
                        if r.opened_at is not None else None
                    )
                    close_result = await self._close_live(sym, meta, opened_at_ms)
                    if close_result is None:
                        logger.warning(
                            "spot_perp_live_close_failed_will_retry",
                            symbol=sym, position_uuid=r.uuid,
                        )
                        continue
                    # D.2.a/b 真实成交价 + funding + borrow 算 PnL，失败回退公式
                    real = self._real_pnl_from_fills(meta, close_result)
                    if real is not None:
                        close_fees_total = Decimal(str(close_result.get("close_fees", "0")))
                        funding_decimal = Decimal(
                            str(close_result.get("funding_received", "0") or "0"),
                        )
                        borrow_decimal = Decimal(
                            str(close_result.get("borrow_interest", "0") or "0"),
                        )
                        final_pnl = real - (r.fees_paid or Decimal("0"))
                        pnl_source = "real_fills"
                    # 把 close 成交合并到 notes 用于审计/重放
                    merged_meta = {**meta, **close_result}
                    r.notes = _encode_notes(sym, merged_meta)

                r.status = "closed"
                r.closed_at = now
                r.realized_pnl = final_pnl
                r.unrealized_pnl = Decimal("0")
                r.exit_reason = exit_reason
                if funding_decimal != 0:
                    r.funding_received = funding_decimal
                # close fees + borrow interest 合并进 fees_paid 累计
                if close_fees_total > 0:
                    r.fees_paid = (r.fees_paid or Decimal("0")) + close_fees_total
                if borrow_decimal > 0:
                    r.fees_paid = (r.fees_paid or Decimal("0")) + borrow_decimal
                # 平仓后清理 funding 缓存
                self._funding_cache.pop(r.uuid, None)
                closed_count += 1
                logger.info(
                    "spot_perp_close",
                    symbol=sym,
                    mode="live" if (self._live_mode and meta) else "paper",
                    pnl_source=pnl_source,
                    entry_basis=str(entry_basis),
                    current_basis=str(current_basis),
                    realized=str(final_pnl),
                    funding=str(funding_decimal),
                    close_fees=str(close_fees_total),
                    borrow_interest=str(borrow_decimal),
                    reason=exit_reason,
                )
                notify_position_closed(
                    strategy="期现套利",
                    symbol=sym,
                    realized_pnl=final_pnl,
                    exit_reason=exit_reason or "unknown",
                )

        # b — 把本 tick 所有候选的 |basis| 写入滑窗（在过滤前更新，所有标的都要追踪）
        now_ms_for_peak = int(now.timestamp() * 1000)
        self._update_peak_cache(opps, now_ms_for_peak)

        slots = self._cfg.max_concurrent - (len(open_rows) - closed_count)
        opened_count = 0
        if slots > 0:
            for opp in opps:
                if slots <= 0:
                    break
                if opp.symbol in existing_syms:
                    continue
                # c — per-direction 入场阈值（discount 因 borrow + funding 双层成本默认更严）
                threshold = self._cfg.entry_threshold_for(opp.direction)
                abs_basis = abs(Decimal(str(opp.basis_pct)))
                if abs_basis < threshold:
                    continue
                # b — 入场时机过滤：要求 |basis| 已从近 N 分钟峰值回落 ≥ M%（防接飞刀）
                pass_dropoff, dropoff = self._check_peak_dropoff(
                    str(opp.symbol), abs_basis, now_ms_for_peak,
                )
                if not pass_dropoff:
                    logger.info(
                        "spot_perp_skip_peak_not_dropped",
                        symbol=opp.symbol,
                        abs_basis=str(abs_basis),
                        dropoff=str(dropoff),
                        required=str(self._cfg.min_peak_dropoff_pct),
                    )
                    continue

                meta: dict | None = None
                if self._live_mode:
                    # 方向过滤（D.2.c 后 discount 也走 _open_live margin 路径）
                    df = self._cfg.direction_filter
                    if df != "both" and opp.direction != df:
                        logger.info(
                            "spot_perp_live_skip_direction",
                            symbol=opp.symbol,
                            direction=opp.direction,
                            allowed=df,
                        )
                        continue
                    meta = await self._open_live(opp)
                    if meta is None:
                        # 实盘下单失败，跳过（不写 row）；下次 tick 再试
                        continue

                new_pos = PositionRecord(
                    uuid=str(uuid_lib.uuid4()),
                    strategy_instance=self.STRATEGY_INSTANCE,
                    strategy_type=self.STRATEGY_TYPE,
                    status="open",
                    notional_usd=self._notional,
                    margin_used=Decimal("0"),
                    target_apr_pct=Decimal(str(opp.basis_pct)),
                    realized_pnl=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    funding_received=Decimal("0"),
                    fees_paid=(
                        Decimal(str(meta.get("fees", "0"))) if meta
                        else self._cfg.round_trip_fee_usd
                    ),
                    opened_at=now,
                    closed_at=None,
                    exit_reason=None,
                    notes=_encode_notes(opp.symbol, meta),
                )
                session.add(new_pos)
                slots -= 1
                opened_count += 1
                logger.info(
                    "spot_perp_open",
                    symbol=opp.symbol,
                    mode="live" if self._live_mode else "paper",
                    basis_pct=str(opp.basis_pct),
                    direction=opp.direction,
                    notional=str(self._notional),
                )
                notify_position_opened(
                    strategy="期现套利",
                    symbol=opp.symbol,
                    basis_pct=opp.basis_pct,
                    notional_usd=self._notional,
                )

        if closed_count > 0 or opened_count > 0:
            logger.info(
                "spot_perp_paper_tick_summary",
                opened=opened_count,
                closed=closed_count,
            )
        await session.commit()

    # ------------------------------------------------------------------
    # Live execution (D.1 premium / D.2.c discount)
    # ------------------------------------------------------------------

    async def _open_live(self, opp) -> dict | None:
        """实盘开仓：

        - **premium**(perp > spot): spot BUY + perp SELL（D.1，CCXT 默认现货）
        - **discount**(perp < spot): spot SELL margin (MARGIN_BUY 自动借币) + perp BUY（D.2.c）

        返回 metadata 或 None(失败)。
        """
        broker = self._brokers.get(opp.exchange)
        if broker is None:
            logger.warning("spot_perp_live_no_broker", exchange=opp.exchange)
            return None

        symbol = _symbol_from_pair(opp.symbol)
        spot_px = Decimal(str(opp.spot_price))
        perp_px = Decimal(str(opp.perp_price))
        if spot_px <= 0:
            return None

        # 用 spot 价格估算 base 数量；spot 和 perp 同 size 实现 delta-neutral
        qty = (self._notional / spot_px).quantize(Decimal("0.00000001"))
        if qty <= 0:
            logger.warning("spot_perp_live_qty_too_small",
                           symbol=opp.symbol, notional=str(self._notional))
            return None

        # OKX 要求 clOrdId 仅字母数字（无 - / _），故 hex prefix 也用纯字符
        client_id = f"sp{uuid_lib.uuid4().hex[:14]}"
        direction = opp.direction  # "premium" | "discount"

        if direction == "premium":
            spot_req = OrderRequest(
                symbol=symbol, side=Side.BUY, size=qty,
                reference_price=spot_px, exchange=opp.exchange,
                instrument_type=InstrumentType.SPOT,
                client_order_id=f"{client_id}s",
            )
            perp_req = OrderRequest(
                symbol=symbol, side=Side.SELL, size=qty,
                reference_price=perp_px, exchange=opp.exchange,
                reduce_only=False, instrument_type=InstrumentType.PERPETUAL,
                client_order_id=f"{client_id}p",
                position_side="SHORT",  # premium 永续做空
            )
        elif direction == "discount":
            # discount: SHORT spot via cross-margin（自动借币卖出）+ LONG perp
            # - Binance: 需 MARGIN_BUY 标记借币；OKX UTA cross-margin 自动借/无需标记
            is_binance = opp.exchange == "binance"
            spot_req = OrderRequest(
                symbol=symbol, side=Side.SELL, size=qty,
                reference_price=spot_px, exchange=opp.exchange,
                instrument_type=InstrumentType.SPOT,
                client_order_id=f"{client_id}s",
                margin_mode="cross",
                side_effect="MARGIN_BUY" if is_binance else None,
            )
            perp_req = OrderRequest(
                symbol=symbol, side=Side.BUY, size=qty,
                reference_price=perp_px, exchange=opp.exchange,
                reduce_only=False, instrument_type=InstrumentType.PERPETUAL,
                client_order_id=f"{client_id}p",
                position_side="LONG",  # discount 永续做多（Binance Hedge 模式必填，OKX 同名）
            )
        else:
            logger.warning("spot_perp_live_unknown_direction",
                           symbol=opp.symbol, direction=direction)
            return None

        try:
            spot_r, perp_r = await broker.execute_pair(spot_req, perp_req)
        except Exception:
            logger.exception("spot_perp_live_open_failed",
                             symbol=opp.symbol, direction=direction)
            return None

        meta = {
            "exchange": opp.exchange,
            "direction": direction,        # D.2.c — close 时按方向反向下单
            "spot_size": str(spot_r.filled_size),
            "perp_size": str(perp_r.filled_size),
            "entry_spot_px": str(spot_r.avg_price),
            "entry_perp_px": str(perp_r.avg_price),
            "client_id": client_id,
            "fees": str(spot_r.fees + perp_r.fees),
        }
        logger.info(
            "spot_perp_live_open_filled",
            symbol=opp.symbol,
            direction=direction,
            spot_px=meta["entry_spot_px"],
            perp_px=meta["entry_perp_px"],
            qty=str(qty),
            fees=meta["fees"],
        )
        return meta

    async def _close_live(
        self, symbol_pair: str, meta: dict, opened_at_ms: int | None = None,
    ) -> dict | None:
        """实盘平仓（按 meta.direction 反向下单）：

        - **premium**: spot SELL（普通）+ perp BUY (reduce_only)
        - **discount**: spot BUY (AUTO_REPAY 自动还币) + perp SELL (reduce_only)

        opened_at_ms 提供时（D.2.b），关单后会拉 funding history 与（discount）
        借币利息累计到返回字典：
          ``{close_spot_px, close_perp_px, close_spot_size, close_perp_size,
             close_fees, funding_received, borrow_interest}``
        失败时返回 ``None``，调用方下一 tick 重试。
        """
        exchange = meta.get("exchange", "binance")
        direction = meta.get("direction", "premium")  # 兼容 D.1 旧 meta 默认 premium
        broker = self._brokers.get(exchange)
        if broker is None:
            logger.warning("spot_perp_live_close_no_broker", exchange=exchange)
            return None

        try:
            spot_size = Decimal(str(meta.get("spot_size", "0")))
            perp_size = Decimal(str(meta.get("perp_size", "0")))
            entry_spot_px = Decimal(str(meta.get("entry_spot_px", "0")))
            entry_perp_px = Decimal(str(meta.get("entry_perp_px", "0")))
        except Exception:
            logger.exception("spot_perp_live_close_meta_invalid", meta=meta)
            return None

        if spot_size <= 0 or perp_size <= 0:
            logger.warning("spot_perp_live_close_zero_size", meta=meta)
            return None

        symbol = _symbol_from_pair(symbol_pair)

        if direction == "premium":
            spot_close = OrderRequest(
                symbol=symbol, side=Side.SELL, size=spot_size,
                reference_price=entry_spot_px, exchange=exchange,
                reduce_only=True, instrument_type=InstrumentType.SPOT,
            )
            perp_close = OrderRequest(
                symbol=symbol, side=Side.BUY, size=perp_size,
                reference_price=entry_perp_px, exchange=exchange,
                reduce_only=True, instrument_type=InstrumentType.PERPETUAL,
                position_side="SHORT",  # 平掉之前开的 SHORT
            )
        elif direction == "discount":
            # discount close: BUY spot 还币 + SELL perp 平多
            # - Binance: AUTO_REPAY 自动还币；OKX UTA cross-margin 买回时自动减债
            is_binance = exchange == "binance"
            spot_close = OrderRequest(
                symbol=symbol, side=Side.BUY, size=spot_size,
                reference_price=entry_spot_px, exchange=exchange,
                reduce_only=False,  # 借币不能 reduce_only
                instrument_type=InstrumentType.SPOT,
                margin_mode="cross",
                side_effect="AUTO_REPAY" if is_binance else None,
            )
            perp_close = OrderRequest(
                symbol=symbol, side=Side.SELL, size=perp_size,
                reference_price=entry_perp_px, exchange=exchange,
                reduce_only=True, instrument_type=InstrumentType.PERPETUAL,
                position_side="LONG",  # 平掉之前开的 LONG
            )
        else:
            logger.warning("spot_perp_live_close_unknown_direction",
                           symbol=symbol_pair, direction=direction)
            return None
        try:
            spot_r = await broker.execute(spot_close)
        except Exception:
            logger.exception("spot_perp_live_close_spot_failed",
                             symbol=symbol_pair)
            return None
        try:
            perp_r = await broker.execute(perp_close)
        except Exception:
            logger.exception("spot_perp_live_close_perp_failed",
                             symbol=symbol_pair)
            # 现货已平 → 后续 tick 重试时 spot_size 仍记录原始值
            # discount/边角 case 后期再加更细的状态机
            return None

        out = {
            "close_spot_px": str(spot_r.avg_price),
            "close_perp_px": str(perp_r.avg_price),
            "close_spot_size": str(spot_r.filled_size),
            "close_perp_size": str(perp_r.filled_size),
            "close_fees": str(spot_r.fees + perp_r.fees),
        }

        # D.2.b — 关单后拉 funding 累计 + (discount) 借币利息
        if opened_at_ms is not None:
            adapter = getattr(broker, "_adapter", None)
            if adapter is not None:
                funding = await self._fetch_funding_received(
                    adapter, symbol_pair, opened_at_ms,
                )
                out["funding_received"] = str(funding)
                if direction == "discount":
                    entry_spot_px_dec = Decimal(str(meta.get("entry_spot_px", "0") or "0"))
                    borrow = await self._fetch_borrow_interest_usdt(
                        adapter, symbol_pair, opened_at_ms,
                        approx_price_usdt=entry_spot_px_dec,
                    )
                    out["borrow_interest"] = str(borrow)

        logger.info(
            "spot_perp_live_close_filled",
            symbol=symbol_pair,
            close_spot_px=out["close_spot_px"],
            close_perp_px=out["close_perp_px"],
            close_fees=out["close_fees"],
            funding_received=out.get("funding_received", "—"),
            borrow_interest=out.get("borrow_interest", "—"),
        )
        return out

    # ------------------------------------------------------------------
    # D.2.b — 持仓收益 / 成本拉取（funding settlement + 借币利息）
    # ------------------------------------------------------------------

    async def _get_live_funding(self, row, meta: dict) -> Decimal:
        """实时拉持仓期累计 funding，结果缓存进 self._funding_cache（每 N tick 刷新）。

        失败时返回上次缓存值（可能为 0）；不阻塞主 tick。
        """
        meta_exchange = meta.get("exchange") if meta else None
        broker = self._brokers.get(meta_exchange or "binance")
        if broker is None:
            return self._funding_cache.get(row.uuid, Decimal("0"))
        adapter = getattr(broker, "_adapter", None)
        if adapter is None:
            return self._funding_cache.get(row.uuid, Decimal("0"))

        sym, _ = _decode_notes(row.notes or "")
        opened_at_ms = (
            int(row.opened_at.timestamp() * 1000) if row.opened_at else None
        )
        if opened_at_ms is None or not sym:
            return self._funding_cache.get(row.uuid, Decimal("0"))

        try:
            value = await self._fetch_funding_received(adapter, sym, opened_at_ms)
            self._funding_cache[row.uuid] = value
            return value
        except Exception:
            logger.debug("get_live_funding_failed", uuid=row.uuid, exc_info=True)
            return self._funding_cache.get(row.uuid, Decimal("0"))

    # ------------------------------------------------------------------
    # b — 入场时机过滤：基差峰值滑窗 + 回落判断
    # ------------------------------------------------------------------

    def _update_peak_cache(self, opps: list, now_ms: int) -> None:
        """把本 tick 的所有候选 |basis| 写入对应 symbol 滑窗，淘汰过期条目。"""
        window_min = self._cfg.peak_window_minutes
        if window_min <= 0:
            return
        cutoff_ms = now_ms - int(window_min * 60 * 1000)
        for opp in opps:
            sym = str(opp.symbol)
            abs_basis = abs(Decimal(str(opp.basis_pct)))
            entries = self._basis_peak_cache.setdefault(sym, [])
            entries.append((now_ms, abs_basis))
            # 淘汰过期 + 容量上限（防内存膨胀）
            self._basis_peak_cache[sym] = [
                (t, b) for t, b in entries[-200:] if t >= cutoff_ms
            ]

    def _check_peak_dropoff(self, symbol_pair: str, current_abs_basis: Decimal, now_ms: int) -> tuple[bool, Decimal]:
        """检查 |basis| 是否已从近 N 分钟峰值回落 ≥ min_peak_dropoff_pct。

        返回 (是否通过, 当前回落幅度)。

        - peak_window_minutes <= 0 或 min_peak_dropoff_pct <= 0 → 禁用，永远 True
        - 缓存空（冷启动 / 新标的）→ True，避免错过首次机会
        - 否则：peak - current_abs_basis ≥ dropoff 才 True
        """
        window_min = self._cfg.peak_window_minutes
        dropoff_min = self._cfg.min_peak_dropoff_pct
        if window_min <= 0 or dropoff_min <= 0:
            return True, Decimal("0")
        entries = self._basis_peak_cache.get(symbol_pair, [])
        if not entries:
            return True, Decimal("0")
        cutoff_ms = now_ms - int(window_min * 60 * 1000)
        valid = [(t, b) for t, b in entries if t >= cutoff_ms]
        if not valid:
            return True, Decimal("0")
        peak = max(b for _, b in valid)
        dropoff = peak - current_abs_basis
        return dropoff >= dropoff_min, dropoff

    @staticmethod
    async def _fetch_funding_received(
        adapter, symbol_pair: str, since_ms: int,
    ) -> Decimal:
        """拉自 since_ms 起此标的 perp 资金费结算累计（USDT，已含正负号）。

        SHORT perp(premium): rate>0 收 → 正；rate<0 付 → 负
        LONG perp(discount): 反向（CCXT 已按账户实际持仓正负化）

        失败时返回 0（不阻塞 PnL 计算）。
        """
        try:
            clients = getattr(adapter, "_clients", {}) or {}
            client = clients.get(InstrumentType.PERPETUAL)
            if client is None or not hasattr(client, "fetch_funding_history"):
                return Decimal("0")
            base, _, quote = symbol_pair.partition("/")
            ccxt_sym = f"{base}/{quote or 'USDT'}:{quote or 'USDT'}"
            records = await client.fetch_funding_history(
                ccxt_sym, since=int(since_ms),
            )
            total = Decimal("0")
            for rec in records or []:
                amount = rec.get("amount")
                if amount is not None:
                    try:
                        total += Decimal(str(amount))
                    except (ValueError, ArithmeticError):
                        continue
            return total
        except Exception:
            logger.debug(
                "spot_perp_fetch_funding_history_failed",
                symbol=symbol_pair, exc_info=True,
            )
            return Decimal("0")

    @staticmethod
    async def _fetch_borrow_interest_usdt(
        adapter, symbol_pair: str, since_ms: int,
        approx_price_usdt: Decimal,
    ) -> Decimal:
        """拉自 since_ms 起借币累计利息（按 base_asset 单位 → USDT 换算）。

        仅 discount 方向涉及（SHORT spot 借了 base 资产）。
        失败/不支持 → 返回 0。
        """
        try:
            clients = getattr(adapter, "_clients", {}) or {}
            client = clients.get(InstrumentType.SPOT)
            if client is None or not hasattr(client, "fetch_borrow_interest"):
                return Decimal("0")
            base, _, _ = symbol_pair.partition("/")
            records = await client.fetch_borrow_interest(
                code=base, since=int(since_ms),
            )
            total_base = Decimal("0")
            for rec in records or []:
                interest = rec.get("interest") or rec.get("amount")
                if interest is not None:
                    try:
                        total_base += Decimal(str(interest))
                    except (ValueError, ArithmeticError):
                        continue
            # base → USDT 近似（用 entry_spot_px 即可，误差极小）
            if approx_price_usdt > 0:
                return total_base * approx_price_usdt
            return Decimal("0")
        except Exception:
            logger.debug(
                "spot_perp_fetch_borrow_interest_failed",
                symbol=symbol_pair, exc_info=True,
            )
            return Decimal("0")

    @staticmethod
    def _basis_widened_pct(entry_basis: Decimal, current_basis: Decimal) -> Decimal:
        """a — 方向感知的"基差扩大幅度"。返回值 > 0 表示朝不利方向走了多少 pct。

        - **premium** (entry > 0): widening = current - entry
          basis 从 +0.30 走到 +0.80 → +0.50（扩大 0.50pct）
        - **discount** (entry < 0): widening = entry - current
          basis 从 -0.30 走到 -0.80 → +0.50（扩大 0.50pct，即更负）
        - **flat** (entry == 0): 永远不算扩大

        反向收敛或穿越 0（如 premium → discount）会返回负值，由调用方忽略，
        改走 basis_convergence 路径。
        """
        if entry_basis > 0:
            return current_basis - entry_basis
        if entry_basis < 0:
            return entry_basis - current_basis
        return Decimal("0")

    @staticmethod
    def _real_pnl_from_fills(meta: dict, close_result: dict) -> Decimal | None:
        """方向感知的真实 PnL（D.2.b 后含 funding + 借币利息）：

        - **premium** (spot LONG + perp SHORT)::
              spot_leg = (close_spot - entry_spot) × size
              perp_leg = (entry_perp - close_perp) × size
        - **discount** (spot SHORT + perp LONG)::
              spot_leg = (entry_spot - close_spot) × size
              perp_leg = (close_perp - entry_perp) × size

        总 PnL = spot_leg + perp_leg + funding_received - close_fees - borrow_interest

        ``funding_received`` 与 ``borrow_interest`` 来自 close_result（D.2.b 后可选；
        缺失视为 0，向后兼容 D.2.a / D.1 旧 close_result）。
        open_fees 已计入 r.fees_paid，由调用方再扣。
        """
        try:
            direction = str(meta.get("direction", "premium")).lower()
            entry_spot = Decimal(str(meta["entry_spot_px"]))
            entry_perp = Decimal(str(meta["entry_perp_px"]))
            spot_size = Decimal(str(meta["spot_size"]))
            perp_size = Decimal(str(meta["perp_size"]))
            close_spot = Decimal(str(close_result["close_spot_px"]))
            close_perp = Decimal(str(close_result["close_perp_px"]))
            close_fees = Decimal(str(close_result.get("close_fees", "0")))
            funding = Decimal(str(close_result.get("funding_received", "0") or "0"))
            borrow = Decimal(str(close_result.get("borrow_interest", "0") or "0"))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            return None
        if spot_size <= 0 or perp_size <= 0:
            return None
        if direction == "premium":
            spot_leg = (close_spot - entry_spot) * spot_size
            perp_leg = (entry_perp - close_perp) * perp_size
        elif direction == "discount":
            spot_leg = (entry_spot - close_spot) * spot_size
            perp_leg = (close_perp - entry_perp) * perp_size
        else:
            return None
        return spot_leg + perp_leg + funding - close_fees - borrow
