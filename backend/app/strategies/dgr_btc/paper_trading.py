"""
dgr_btc/paper_trading.py
========================
DgrBtcPaperSession — 动态网格 + 再定心 实时 tick 驱动 paper trading (#13)。

Phase C 范围:
  ✅ 30s tick loop, MarketDataHub 拉 spot+perp+funding
  ✅ strategy.on_tick → mock fill → on_trade → DB persist + jsonl
  ✅ RiskFilter 实战计算 realized_vol_1h (60 个 1m close σ × √(365×24)) + margin_ratio (equity / perp_margin_used)
  ✅ Bootstrap 建仓 (paper 模式 — 内存设 spot 0.05 / short 0.05, 不调真实 API)
  ✅ restore() — DB 恢复 grid center + recenter 状态
  ✅ Telegram alert (recenter / trend_halt / DEFENSIVE / EMERGENCY / funding settled)

Phase E 未做:
  ❌ Real broker_adapter (paper/dry_run/live 三模式) — 当前仅 mock fill
  ❌ Pre-post LIMIT_MAKER + order_manager — Phase F
  ❌ 双腿原子性 + unwind — Phase E

设计 mirror hedged_grid HedgedGridPaperSession 但简化 (无 pre_post / 无 atomic dispatch),
专注 strategy 核心周期 + 审计 4 项必修.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid as _uuid
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from typing import Any, Optional

import structlog

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.inflight_manager import InflightOrderManager
from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy, OrderIntent
from app.strategies.dgr_btc.types import (
    MarketState,
    MarketType,
    Side,
    Trade,
)


logger = structlog.get_logger(__name__)

_ZERO = Decimal("0")
_VOL_WINDOW = 60  # 60 个 1m close → 1h 滑动窗口
_TRADES_JSONL = "/app/state/dgr_btc_trades.jsonl"

# Deterministic stable UUID for dgr_btc_main singleton position row (uuid5)
DGR_BTC_SINGLETON_UUID = str(_uuid.uuid5(_uuid.NAMESPACE_URL, "dgr-btc-main-singleton"))


class DgrBtcPaperSession:
    """动态网格再定心策略 paper trading session。

    生命周期: __init__ → start() (含 restore + bootstrap) → run_forever() → stop()
    """

    def __init__(
        self,
        cfg: DgrBtcStrategyConfig,
        adapter: Any,                       # binance adapter (拉价 + funding)
        market_data_hub: Any | None = None,  # 实时 ticker 源 (优先)
        position_manager: Any | None = None,  # DB Position 持久化
        tick_interval_seconds: float = 30.0,
        live_mode: bool = False,             # Phase E 才用
        broker_adapter: Any | None = None,   # Phase E 才用
    ):
        self.cfg = cfg
        self.adapter = adapter
        self.hub = market_data_hub
        self.position_manager = position_manager
        self.tick_interval = tick_interval_seconds
        self.live_mode = live_mode
        self._broker_adapter = broker_adapter

        # 策略实例 - 延迟初始化 (start() 时拉到 start_price 才能 build)
        self.strategy: Optional[DgrBtcStrategy] = None

        # 运行状态
        self._running = False
        self._stop_event: asyncio.Event = asyncio.Event()
        self._last_tick_at: Optional[datetime] = None
        self._n_ticks = 0
        self._n_intents_generated = 0
        self._n_trades_executed = 0
        self._last_funding_settle_ts = 0   # ms; 防重复 8h settle
        self._bootstrap_done = False

        # 价格窗口 (RiskFilter realized_vol_1h 用)
        self._price_window: deque[Decimal] = deque(maxlen=_VOL_WINDOW)

        # 状态变化 dedup (避免 Telegram spam)
        self._last_risk_level: Optional[str] = None
        self._was_trending = False

        # Phase H: maker pre-placement 状态
        # inflight_manager 跟踪在 binance 簿上还活着的 LIMIT_MAKER 单。
        # live_mode=False 时 register/update 都 short-circuit（mock fill 即时成交）。
        self.inflight_manager = InflightOrderManager(
            max_inflight_per_side=10,  # Phase H pre-place: 上下各 10 格备好
            broker_adapter=broker_adapter,
            live_mode=live_mode,
        )
        # 上 tick 看到的 open_orders 集合（_sync_fills diff 用）
        self._last_synced_open_orders: set[str] = set()
        # 上次 maintain 的 center（recenter 检测）
        self._last_maintain_center: Optional[Decimal] = None
        # Phase H UI 修复：缓存最近一次 tick 拿到的真实 BTC spot 价，snapshot 优先用。
        # 否则 strategy.grid.last_price 启动初期为 None → snapshot mark=center 假象重合。
        self._last_spot_px: Optional[Decimal] = None
        self._last_perp_px: Optional[Decimal] = None
        # Phase H 启用开关：默认 OFF，必须显式 DGR_BTC_PRE_PLACE=1 才启动 pre-place.
        # 设计意图：先在 UI 看到 4-box snapshot + LIVE ORDERS 框架，
        # 用户拍板后再开 flag → 后端进入 pre-place mode 真挂 LIMIT_MAKER.
        self._pre_place_enabled: bool = bool(
            live_mode
            and os.environ.get("DGR_BTC_PRE_PLACE", "0") == "1"
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动: 拉 start_price → restore DB → bootstrap 建仓 → 实例化 strategy."""
        # 1. 拉当前 BTC 价 作 start_price
        try:
            spot_px = await self._fetch_spot_price()
            perp_px = await self._fetch_perp_price()
        except Exception as e:
            logger.warning("dgr_btc_start_price_fetch_failed", error=str(e)[:120])
            spot_px = self.cfg.grid_center_price or Decimal("80000")
            perp_px = spot_px

        if spot_px <= 0:
            raise RuntimeError("dgr_btc start_price <= 0, cannot init")

        # 2. restore from DB (Phase C.8) — 如果有 open position, 用 DB state 而非 fresh
        restored_state = await self._try_restore_state()
        if restored_state is not None:
            start_price = restored_state["center"]
            logger.info(
                "dgr_btc_restored_from_db",
                center=str(start_price),
                spot_qty=str(restored_state.get("spot_qty")),
                perp_qty=str(restored_state.get("perp_qty")),
                n_recenters=restored_state.get("n_recenters", 0),
            )
        else:
            start_price = spot_px

        # 3. 实例化 strategy (用 restored center 或当前价)
        self.strategy = DgrBtcStrategy(self.cfg, start_price=start_price)

        # 4. 若 restore 了仓位状态, 覆盖默认 init
        if restored_state is not None:
            self._apply_restored_state(restored_state)
        elif not self._bootstrap_done:
            # 5. Bootstrap (Phase C.4) — paper 模式: 内存 spot/short 已由 strategy 初始化
            # live 模式 (Phase E): 调 broker 真实 market order 建仓
            await self._bootstrap(spot_px)

        # 5.5. LIVE state restore (Phase G.5: persistent state file)
        # Priority 1: /app/state/dgr_btc_state.json (持久, _tick 每 60s 写)
        # Priority 2: /app/state/dgr_btc_init_state.json (bootstrap, persistent 不存在时用)
        # Priority 3: 默认 init (start_price)
        _persist_path = "/app/state/dgr_btc_state.json"
        _bootstrap_path = "/app/state/dgr_btc_init_state.json"
        _src_path = _persist_path if os.path.exists(_persist_path) else (
            _bootstrap_path if os.path.exists(_bootstrap_path) else None
        )
        if _src_path is not None and self.strategy is not None:
            try:
                with open(_src_path) as _f:
                    _state = json.load(_f)
                _applied: dict = {"_src": os.path.basename(_src_path)}
                if "spot_qty" in _state:
                    self.strategy.spot_pos.quantity = Decimal(str(_state["spot_qty"]))
                    _applied["spot_qty"] = str(self.strategy.spot_pos.quantity)
                if "perp_qty" in _state:
                    self.strategy.perp_pos.quantity = Decimal(str(_state["perp_qty"]))
                    _applied["perp_qty"] = str(self.strategy.perp_pos.quantity)
                if "spot_avg_entry" in _state:
                    self.strategy.spot_pos.avg_entry = Decimal(str(_state["spot_avg_entry"]))
                    _applied["spot_avg_entry"] = str(self.strategy.spot_pos.avg_entry)
                if "perp_avg_entry" in _state:
                    self.strategy.perp_pos.avg_entry = Decimal(str(_state["perp_avg_entry"]))
                    _applied["perp_avg_entry"] = str(self.strategy.perp_pos.avg_entry)
                if "cash" in _state:
                    self.strategy.cash = Decimal(str(_state["cash"]))
                    _applied["cash"] = str(self.strategy.cash)
                if "center" in _state:
                    self.strategy.center = Decimal(str(_state["center"]))
                    self.strategy.grid.rebuild_around(
                        self.strategy.center, self.strategy.config.width_pct
                    )
                    # M2 修复: rebuild_around 把 last_price 设为 center; 但 restart 后第一 tick
                    # 用真实 spot_px 可能与 center 偏离多 grid → 触发 false crossover storm.
                    # 用 start() 拉到的真实 spot_px 覆盖 last_price, 避免假穿格.
                    self.strategy.grid.last_price = spot_px
                    self.strategy.grid.last_grid_index = (
                        self.strategy.grid.find_grid_index(spot_px)
                    )
                    _applied["center"] = str(self.strategy.center)
                    _applied["last_price"] = str(spot_px)
                if "n_recenters" in _state:
                    self.strategy.n_recenters = int(_state["n_recenters"])
                if "realized_pnl" in _state:
                    self.strategy.perp_pos.realized_pnl = Decimal(str(_state["realized_pnl"]))
                if "total_fees" in _state:
                    self.strategy.total_fees = Decimal(str(_state["total_fees"]))
                # CRITICAL #2: restore safety counters (daily_notional/count/date)
                # 仅在 counter_date == today 时恢复, 否则 _maybe_reset_daily 走默认归零路径
                if (
                    "safety" in _state
                    and self._broker_adapter is not None
                    and getattr(self._broker_adapter, "safety", None) is not None
                ):
                    try:
                        self._broker_adapter.safety.restore_from_dict(_state["safety"])
                        _applied["safety_restored"] = True
                    except Exception:
                        logger.exception("dgr_btc_safety_restore_failed")
                # Phase H.6: restore inflight orders (避免 restart 后 60s orphan_recovery 窗口)
                if "inflight" in _state and isinstance(_state["inflight"], list):
                    n_inflight_restored = 0
                    for io_dict in _state["inflight"]:
                        try:
                            mkt_str = str(io_dict.get("market", "spot")).lower()
                            io_market = (
                                MarketType.SPOT if mkt_str == "spot" else MarketType.PERP
                            )
                            self.inflight_manager.register_local(
                                order_id=str(io_dict["order_id"]),
                                market=io_market,
                                grid_level=Decimal(str(io_dict["grid_level"])),
                                side=str(io_dict["side"]),
                            )
                            n_inflight_restored += 1
                        except Exception:
                            logger.exception(
                                "dgr_btc_inflight_restore_failed",
                                io=str(io_dict)[:120],
                            )
                    if n_inflight_restored > 0:
                        _applied["inflight_restored"] = n_inflight_restored
                # 不删文件 (persistent), 但 init_state bootstrap 文件用过一次后可手动删
                logger.info("dgr_btc_state_restored", **_applied)
            except Exception:
                logger.exception("dgr_btc_state_restore_failed_use_default_init")

        # 第一笔 price 入 vol 窗
        self._price_window.append(spot_px)

        logger.info(
            "dgr_btc_paper_session_started",
            instance=self.cfg.instance_name,
            spot_price=str(spot_px),
            perp_price=str(perp_px),
            start_price=str(start_price),
            leverage=self.cfg.leverage,
            grid_step=str(self.cfg.grid_step_usdt),
            qty=str(self.cfg.grid_qty_per_grid),
            width_pct=str(self.cfg.width_pct),
            recenter_trigger=str(self.cfg.recenter_trigger_pct),
            trend_threshold=self.cfg.risk_trend_grids_threshold,
            tick_interval_s=self.tick_interval,
            live_mode=self.live_mode,
            restored=restored_state is not None,
        )

    async def _bootstrap(self, spot_px: Decimal) -> None:
        """Bootstrap 建仓 (Phase C.4).

        paper 模式: strategy.__init__ 已经把 spot_pos.quantity = spot_initial_btc 设好,
                   perp_pos.quantity = -short_initial_btc 也设好. 这里只标记完成 + 通知.
                   注意 cash 已扣 spot_initial * center, 等价于"已 market buy".

        live 模式 (Phase E): 真实 market BUY spot + market SELL perp 开 short.
        """
        if self.live_mode:
            # TODO Phase E: 调 broker.execute_intent market order 建仓
            logger.warning(
                "dgr_btc_bootstrap_live_mode_not_implemented",
                msg="Phase E will wire broker.execute_intent for real bootstrap",
            )
        else:
            # paper: strategy 已带初始仓位, 无需调用真实 API
            logger.info(
                "dgr_btc_bootstrap_paper_inmemory",
                spot=str(self.cfg.spot_initial_btc),
                short=str(self.cfg.short_initial_btc),
                cash_remaining=str(self.strategy.cash),
            )

        self._bootstrap_done = True
        await self._notify(
            f"dgr_btc 启动: spot {self.cfg.spot_initial_btc} BTC + short {self.cfg.short_initial_btc} BTC @ ${spot_px:,.0f}"
        )

    async def run_forever(self) -> None:
        """主 tick 循环. stop_event 触发后退出."""
        if not self.cfg.enabled:
            logger.info("dgr_btc_session_disabled_idle", instance=self.cfg.instance_name)
            return
        self._running = True
        # Phase G.5: persistent state writer 节流 (60s 一次)
        self._last_state_persist_ts: float = 0.0
        while self._running and not self._stop_event.is_set():
            try:
                await self._tick()
                # Phase G.5: 持久化 strategy state 到 state.json
                import time as _t
                if _t.monotonic() - self._last_state_persist_ts >= 60.0:
                    self._persist_strategy_state_file()
                    self._last_state_persist_ts = _t.monotonic()
            except Exception:
                logger.exception("dgr_btc_tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.tick_interval,
                )
                break
            except asyncio.TimeoutError:
                continue

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        logger.info(
            "dgr_btc_paper_session_stopped",
            n_ticks=self._n_ticks,
            n_intents=self._n_intents_generated,
            n_trades=self._n_trades_executed,
            n_recenters=self.strategy.n_recenters if self.strategy else 0,
        )

    # ------------------------------------------------------------------
    # 主 tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        if self.strategy is None:
            return

        self._n_ticks += 1
        now = datetime.now(timezone.utc)
        self._last_tick_at = now

        # 0. Phase H: 优先同步已 fill / canceled 的 pre-place inflight 单
        #    必须放最前 — 先把成交吸收到 strategy state 再做后续 reactive decision.
        #    paper / dry_run 自动 no-op.
        try:
            await self._sync_fills()
        except Exception:
            logger.exception("dgr_btc_sync_fills_failed")

        # 0.1. CRITICAL #3: 每 6 ticks (5s × 6 = 30s) 刷新 USDT 借款 cache.
        # SafetyGuard.check_pre_order 用此 cache 判断 max_open_borrow_usd 是否超 cap.
        if (
            self._pre_place_enabled
            and self._broker_adapter is not None
            and getattr(self._broker_adapter, "safety", None) is not None
            and self._n_ticks % 6 == 1  # 第 1/7/13... tick 刷新
        ):
            try:
                borrowed = await self._broker_adapter.fetch_margin_borrowed_usdt()
                self._broker_adapter.safety.update_borrowed_usdt(borrowed)
            except Exception:
                logger.exception("dgr_btc_safety_borrow_refresh_failed")

        # 1. 拉价
        try:
            spot_px = await self._fetch_spot_price()
            perp_px = await self._fetch_perp_price()
        except Exception as e:
            logger.warning("dgr_btc_price_fetch_failed", error=str(e)[:120])
            return
        if spot_px <= 0 or perp_px <= 0:
            return
        # Phase H UI: 缓存最新真实价 (供 snapshot 显示, 而非 fallback center)
        self._last_spot_px = spot_px
        self._last_perp_px = perp_px

        # 2. 更新 vol 窗口 (Phase C.3 RiskFilter 实战)
        self._price_window.append(spot_px)
        realized_vol_1h = self._compute_realized_vol()

        # 3. 计算 margin_ratio (Phase C.3 实战)
        margin_ratio = self._compute_margin_ratio(perp_px)

        # 4. 检查 funding 8h settle
        funding_settled = self._is_funding_settle_time(now)
        funding_rate = _ZERO
        if funding_settled:
            try:
                funding_rate = await self._fetch_funding_rate()
            except Exception as e:
                logger.warning("dgr_btc_funding_fetch_failed", error=str(e)[:120])
                funding_rate = _ZERO

        # 5. 构造 MarketState
        market = MarketState(
            timestamp=now,
            spot_price=spot_px,
            perp_price=perp_px,
            funding_rate=funding_rate,
            funding_settled=funding_settled,
            bid_depth_usdt=Decimal("1000000"),
            ask_depth_usdt=Decimal("1000000"),
            realized_vol_1h=realized_vol_1h,
        )

        # 6. strategy.on_tick → intents (内部含 maybe_recenter)
        try:
            n_recenters_before = self.strategy.n_recenters
            intents = self.strategy.on_tick(market, margin_ratio=margin_ratio)
            # recenter 事件 alert
            if self.strategy.n_recenters > n_recenters_before:
                ev = self.strategy.recenter_events[-1]
                await self._notify(
                    f"dgr_btc recenter #{self.strategy.n_recenters}: "
                    f"${ev.old_center:,.0f} → ${ev.new_center:,.0f} "
                    f"(偏离 {float(ev.deviation_pct * 100):.2f}%)"
                )
        except Exception:
            logger.exception("dgr_btc_strategy_on_tick_failed")
            return

        # 7. 风控状态变化 alert (dedup)
        if self.strategy.risk.last_report is not None:
            level = self.strategy.risk.last_report.level.value
            if level != self._last_risk_level and level != "NORMAL":
                msgs = ", ".join(self.strategy.risk.last_report.messages)
                await self._notify(f"dgr_btc 风控 {level}: {msgs}")
            self._last_risk_level = level

        # 8. trend halt 状态变化 alert (dedup)
        is_trending = self.strategy.grid.is_trending(
            self.cfg.risk_trend_grids_threshold
        )
        if is_trending and not self._was_trending:
            await self._notify(
                f"dgr_btc 趋势保护激活 (连续 {self.strategy.grid.consecutive_direction_grids} 单方向)"
            )
        elif not is_trending and self._was_trending:
            await self._notify("dgr_btc 趋势保护解除")
        self._was_trending = is_trending

        # 9. funding 结算 alert
        if funding_settled:
            await self._notify(
                f"dgr_btc funding settled: rate={float(funding_rate)*100:.4f}% / "
                f"cash={self.strategy.cash}"
            )

        # 10. 处理 intents
        #     C1 修复 (LIVE 切换前审计): pre_place_enabled=True 时只走 maintain 唯一权威派单链路,
        #     避免 reactive atomic_pair LIVE 5s wait 与 maintain no_wait 同 grid 重复挂单 →
        #     重复成交 / 单腿暴露的 race condition.
        #     paper / dry_run / pre_place=off 模式仍走 reactive _dispatch_intents (mock fill).
        if intents and not self._pre_place_enabled:
            self._n_intents_generated += len(intents)
            await self._dispatch_intents(intents, market)
        elif intents and self._pre_place_enabled:
            # 仅记录意图数, 不实际派单 (maintain 会通过 grid trigger 重新评估)
            self._n_intents_generated += len(intents)
            logger.debug(
                "dgr_btc_reactive_dispatch_skipped_pre_place_mode",
                n_intents=len(intents),
            )

        # 10.5 Phase H: maintain pre-placed pairs (pre_place_enabled=True 才生效)
        try:
            await self._maintain_resting_pairs(market)
        except Exception:
            logger.exception("dgr_btc_maintain_resting_failed")

        # 11. 持仓有变化时 → DB persist
        if intents:
            try:
                await self._persist_position_to_db()
            except Exception:
                logger.exception("dgr_btc_position_persist_failed")

    # ------------------------------------------------------------------
    # RiskFilter 实战计算 (Phase C.3)
    # ------------------------------------------------------------------

    def _compute_realized_vol(self) -> Decimal:
        """计算 1h 滑动窗口对数收益率的年化波动率.

        backtest hardcoded 0 → 永不触发 HIGH_VOLATILITY.
        实战必须真实计算否则 DEFENSIVE 永不激活.
        """
        if len(self._price_window) < 2:
            return _ZERO
        # 对数收益率
        prices = [float(p) for p in self._price_window]
        from math import log
        rets = [log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
        if len(rets) < 2:
            return _ZERO
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = sqrt(var)
        # 年化 (假设 1m K → 1 年 365 * 24 * 60 个 sample)
        annualized = std * sqrt(365 * 24 * 60)
        return Decimal(str(annualized))

    def _compute_margin_ratio(self, mark_price: Decimal) -> Decimal:
        """margin_ratio = equity / perp_margin_used.

        equity = cash + spot * mark + perp 盈亏
        perp_margin_used = |perp_quantity| * mark / leverage
        backtest hardcoded 1.0 → 永不触发 LOW_MARGIN.
        """
        if self.strategy is None:
            return Decimal("10.0")
        perp_qty = abs(self.strategy.perp_pos.quantity)
        if perp_qty == 0 or self.cfg.leverage <= 0:
            return Decimal("10.0")
        margin_used = perp_qty * mark_price / Decimal(self.cfg.leverage)
        if margin_used <= 0:
            return Decimal("10.0")
        # equity (使用最新 mark)
        spot_value = self.strategy.spot_pos.quantity * mark_price
        # perp pnl: unrealized = (basis - mark) * |qty| for short
        perp_unreal = (
            (self.strategy.perp_pos.avg_entry - mark_price)
            * perp_qty
            if self.strategy.perp_pos.quantity < 0
            else _ZERO
        )
        equity = self.strategy.cash + spot_value + perp_unreal + self.strategy.perp_pos.realized_pnl
        ratio = equity / margin_used
        return ratio

    # ------------------------------------------------------------------
    # Intent dispatch + mock fill (Phase C — 无 atomic + 无 broker)
    # ------------------------------------------------------------------

    async def _dispatch_intents(
        self,
        intents: list[OrderIntent],
        market: MarketState,
    ) -> None:
        """Phase E.1 集成: AtomicPairExecutor + pretrade_check + unwind.

        每对 (spot, perp) intent 走原子性 dispatch:
          PAPER: 默认双腿成 (single_leg_failure_rate=0 from env)
          LIVE (Phase F): broker_adapter + maker_reprice + unwind
        """
        from app.strategies.dgr_btc.atomic_pair import (
            AtomicPairExecutor, PairExecutionMode, PairOutcome, pair_intents,
        )
        from app.strategies.dgr_btc.pretrade_check import PretradeChecker

        # 懒初始化 executor (复用 session 期间)
        if not hasattr(self, "_pair_executor") or self._pair_executor is None:
            mode = PairExecutionMode.LIVE if self.live_mode else PairExecutionMode.PAPER
            failure_rate = float(os.environ.get("DGR_BTC_SIMULATE_LEG_FAILURE_RATE", "0"))
            checker = PretradeChecker(
                strategy=self.strategy,
                live_mode=self.live_mode,
                market_data_hub=self.hub,
                # 2% 容纳 step $500 自然偏差 (~0.65%); 默认 0.5% 会过早拦截 grid trigger
                max_price_deviation_pct=Decimal("0.02"),
            )
            self._pair_executor = AtomicPairExecutor(
                mode=mode,
                spot_fee_rate=self.cfg.backtest_spot_fee,
                perp_maker_fee=self.cfg.backtest_perp_maker_fee,
                perp_taker_fee=self.cfg.backtest_perp_taker_fee,
                single_leg_failure_rate=failure_rate,
                broker_adapter=self._broker_adapter,
                pretrade_checker=checker,
            )

        pairs = pair_intents(intents)
        if not pairs:
            return

        for spot_intent, perp_intent in pairs:
            result = await self._pair_executor.execute_pair(spot_intent, perp_intent, market)
            # Phase G.7: 异常 outcome → Telegram 推送 (BOTH_FILLED 不推, 避免 spam)
            if result.outcome != PairOutcome.BOTH_FILLED:
                try:
                    _grid = spot_intent.grid_level or perp_intent.grid_level
                    _msg = (
                        f"⚠ dgr_btc {result.outcome.value}\n"
                        f"  grid_level: ${_grid}\n"
                        f"  spot: {spot_intent.side.value} {spot_intent.quantity} @ ${spot_intent.price}\n"
                        f"  perp: {perp_intent.side.value} {perp_intent.quantity} @ ${perp_intent.price}\n"
                        f"  reason: {result.reject_reason or '-'}\n"
                        f"  unwind: {'yes' if result.unwind_trade is not None else 'no'}"
                    )
                    # fire-and-forget
                    import asyncio as _aio
                    _aio.create_task(self._notify(_msg))
                except Exception:
                    logger.exception("dgr_btc_anomaly_notify_failed")
            if result.outcome == PairOutcome.PRETRADE_REJECTED:
                logger.warning("dgr_btc_pretrade_rejected", reason=result.reject_reason)
                continue
            for trade in result.trades:
                try:
                    self.strategy.on_trade(trade)
                    self._n_trades_executed += 1
                    # 找回对应 intent 用于 jsonl
                    intent = spot_intent if trade.market == spot_intent.market else perp_intent
                    self._persist_trade_jsonl(intent, trade)
                except Exception:
                    logger.exception("dgr_btc_on_trade_failed", trade_id=trade.trade_id)
            if result.unwind_trade is not None:
                logger.warning(
                    "dgr_btc_unwind_applied",
                    outcome=result.outcome.value,
                    leg_market=result.unwind_trade.market.value,
                )
                try:
                    self.strategy.on_trade(result.unwind_trade)
                    self._n_trades_executed += 1
                    # Phase G.4: unwind trade 也写 jsonl 用于审计
                    # 构造伪 intent (unwind 没有 source intent, side 跟 trade 一致)
                    _unwind_intent = OrderIntent(
                        market=result.unwind_trade.market,
                        side=result.unwind_trade.side,
                        price=result.unwind_trade.price,
                        quantity=result.unwind_trade.quantity,
                        grid_level=result.unwind_trade.grid_level,
                        reason=f"unwind_{result.outcome.value.lower()}",
                    )
                    self._persist_trade_jsonl(_unwind_intent, result.unwind_trade)
                except Exception:
                    logger.exception("dgr_btc_unwind_on_trade_failed")

    # ------------------------------------------------------------------
    # Phase H: maker pre-placement —— _sync_fills + _maintain_resting_pairs
    # ------------------------------------------------------------------

    async def _sync_fills(self, market: MarketState | None = None) -> None:
        """从 binance 拉 open_orders, diff 出已 fill / canceled, 触发 strategy.on_trade.

        只在 live_mode 且 broker_adapter 存在时运行. Paper / dry_run 跳过.
        """
        if not self._pre_place_enabled or self._broker_adapter is None:
            return
        try:
            open_orders = await self._broker_adapter.fetch_open_orders()
        except Exception as e:
            logger.warning("dgr_btc_sync_fetch_open_failed", error=str(e)[:120])
            return

        # 算 stale 阈值: ±2 step 之外的 inflight 应被 cancel (recenter / 远端漂移)
        step = self.cfg.grid_step_usdt
        max_drift = step * Decimal("2.5")
        center = self.strategy.center if self.strategy else None

        missing, stale, orphan = self.inflight_manager.update_from_open_orders(
            open_orders, max_grid_drift=max_drift, current_center=center,
        )

        # 0. orphan 回收 (restart recovery): broker 端有 dgr_ 前缀单但本地无记录
        #    → register_local 回收, 让后续 maintain 不重复派单
        for o in orphan:
            try:
                oid = str(o.get("id") or o.get("orderId") or o.get("clientOrderId") or "")
                market_str = o.get("_dgr_market") or o.get("symbol") or ""
                # 推断 market: _dgr_market 是 broker.fetch_open_orders 标注的标签
                if market_str.startswith("spot"):
                    io_market = MarketType.SPOT
                elif "swap" in market_str.lower() or "perp" in market_str.lower():
                    io_market = MarketType.PERP
                else:
                    io_market = MarketType.SPOT  # fallback
                grid_level = Decimal(str(o.get("price") or "0"))
                side = "BUY" if (o.get("side") or "").lower() == "buy" else "SELL"
                if oid and grid_level > 0:
                    self.inflight_manager.register_local(
                        order_id=oid, market=io_market,
                        grid_level=grid_level, side=side,
                    )
                    logger.info(
                        "dgr_btc_orphan_recovered",
                        order_id=oid, market=io_market.value,
                        grid_level=str(grid_level), side=side,
                    )
            except Exception:
                logger.exception("dgr_btc_orphan_recover_failed", order=str(o)[:120])

        # 1. missing → fetch_order 拿最终状态 → 若 filled 构造 Trade
        for order_id in missing:
            # 找本地 inflight 记录拿 market
            io = self.inflight_manager.force_unregister(order_id)
            if io is None:
                continue
            od = await self._broker_adapter.fetch_order(order_id, io.market)
            if od is None:
                logger.warning("dgr_btc_sync_fetch_order_none", order_id=order_id)
                continue
            status = (od.get("status") or "").lower()
            filled = Decimal(str(od.get("filled") or "0"))
            avg = od.get("average") or od.get("price") or io.grid_level
            try:
                avg_dec = Decimal(str(avg))
            except Exception:
                avg_dec = io.grid_level

            if status in ("closed", "filled") and filled > 0:
                trade = Trade(
                    trade_id=f"live_{_uuid.uuid4().hex[:12]}",
                    order_id=str(order_id),
                    symbol=(
                        self.cfg.symbol_spot if io.market == MarketType.SPOT
                        else self.cfg.symbol_perp
                    ),
                    market=io.market,
                    side=Side.BUY if io.side == "BUY" else Side.SELL,
                    price=avg_dec,
                    quantity=filled,
                    fee=self._estimate_maker_fee(io.market, avg_dec, filled),
                    is_maker=True,
                    timestamp=datetime.now(timezone.utc),
                    grid_level=io.grid_level,
                )
                try:
                    self.strategy.on_trade(trade)
                    self._n_trades_executed += 1
                    intent = OrderIntent(
                        market=io.market,
                        side=trade.side,
                        price=avg_dec,
                        quantity=filled,
                        grid_level=io.grid_level,
                        reason="pre_place_fill",
                    )
                    self._persist_trade_jsonl(intent, trade)
                    logger.info(
                        "dgr_btc_pre_place_filled",
                        order_id=order_id,
                        market=io.market.value,
                        side=io.side,
                        price=str(avg_dec),
                        qty=str(filled),
                        grid_level=str(io.grid_level),
                    )
                    # 单腿 fill 早期告警：同 grid_level 配对腿是否还活在 inflight？
                    # 若活 → 单腿 fill 发生，60s reconciliation 会兜底，但提早 Telegram alert.
                    other_market = (
                        MarketType.PERP if io.market == MarketType.SPOT else MarketType.SPOT
                    )
                    paired = self.inflight_manager.find_by_level(
                        other_market, io.grid_level,
                    )
                    if paired is not None:
                        logger.warning(
                            "dgr_btc_single_leg_fill_detected",
                            filled_market=io.market.value,
                            paired_market=other_market.value,
                            grid_level=str(io.grid_level),
                            paired_order_id=paired.order_id,
                            note="cancel_paired_then_maintain_will_replace",
                        )
                        # Phase H.live fix: 单腿 fill 时立即 cancel 配对腿，避免:
                        # 1) stale 单残留在 binance (maintain 用 spot 端 find_by_level,
                        #    spot 已 unregister 时 maintain 重挂一对 → 旧 paired 漏 cancel)
                        # 2) 价格继续走可能让 stale 单自然成交, 造成"双成交"超额加空/平空
                        # cancel 成功后 force_unregister, maintain 下 tick 重挂全新 pair.
                        paired_cancel_ok = False
                        paired_race_filled = False
                        try:
                            paired_cancel_ok = await self._broker_adapter.cancel_order_by_market(
                                paired.order_id, paired.market
                            )
                            # CRITICAL #4: cancel "成功" 不代表 paired 没成交 —
                            # binance -2011 "unknown order" 在 cancel_order_by_market 内部
                            # 被当 ok=True (单已不存在). 但单不存在原因可能是:
                            #   A) 已 canceled (我们期望)
                            #   B) 已 filled (race window — 价格 paired 同价位同时撞穿)
                            # 不 verify 直接 force_unregister 会丢 case B 的 fill →
                            # strategy 不知道 perp 已成交, 但 binance 已扣 / 加持仓
                            # → 双成交风险 / state drift.
                            # Fix: cancel ok 后必须 fetch_order 二次确认.
                            if paired_cancel_ok:
                                paired_od = None
                                try:
                                    paired_od = await self._broker_adapter.fetch_order(
                                        paired.order_id, paired.market,
                                    )
                                except Exception:
                                    logger.exception(
                                        "dgr_btc_single_leg_paired_verify_fetch_failed",
                                        paired_order_id=paired.order_id,
                                    )
                                paired_status = (
                                    (paired_od.get("status") or "").lower()
                                    if paired_od else ""
                                )
                                paired_filled = (
                                    Decimal(str(paired_od.get("filled") or "0"))
                                    if paired_od else _ZERO
                                )
                                if (
                                    paired_status in ("closed", "filled")
                                    and paired_filled > 0
                                ):
                                    # Race-filled: paired 在 cancel 命令到达前已成交
                                    paired_race_filled = True
                                    p_avg = paired_od.get("average") or paired_od.get(
                                        "price"
                                    ) or paired.grid_level
                                    try:
                                        p_avg_dec = Decimal(str(p_avg))
                                    except Exception:
                                        p_avg_dec = paired.grid_level
                                    p_trade = Trade(
                                        trade_id=f"live_{_uuid.uuid4().hex[:12]}",
                                        order_id=str(paired.order_id),
                                        symbol=(
                                            self.cfg.symbol_spot
                                            if paired.market == MarketType.SPOT
                                            else self.cfg.symbol_perp
                                        ),
                                        market=paired.market,
                                        side=(
                                            Side.BUY if paired.side == "BUY" else Side.SELL
                                        ),
                                        price=p_avg_dec,
                                        quantity=paired_filled,
                                        fee=self._estimate_maker_fee(
                                            paired.market, p_avg_dec, paired_filled,
                                        ),
                                        is_maker=True,
                                        timestamp=datetime.now(timezone.utc),
                                        grid_level=paired.grid_level,
                                    )
                                    try:
                                        self.strategy.on_trade(p_trade)
                                        self._n_trades_executed += 1
                                        p_intent = OrderIntent(
                                            market=paired.market,
                                            side=p_trade.side,
                                            price=p_avg_dec,
                                            quantity=paired_filled,
                                            grid_level=paired.grid_level,
                                            reason="single_leg_paired_race_filled",
                                        )
                                        self._persist_trade_jsonl(p_intent, p_trade)
                                    except Exception:
                                        logger.exception(
                                            "dgr_btc_paired_race_on_trade_failed",
                                            paired_order_id=paired.order_id,
                                        )
                                    self.inflight_manager.force_unregister(paired.order_id)
                                    logger.warning(
                                        "dgr_btc_single_leg_paired_race_filled_recovered",
                                        paired_order_id=paired.order_id,
                                        paired_market=paired.market.value,
                                        filled=str(paired_filled),
                                        grid_level=str(io.grid_level),
                                    )
                                else:
                                    self.inflight_manager.force_unregister(paired.order_id)
                                    logger.info(
                                        "dgr_btc_single_leg_paired_canceled",
                                        paired_order_id=paired.order_id,
                                        paired_market=paired.market.value,
                                        grid_level=str(io.grid_level),
                                        verified_status=paired_status or "unverified",
                                    )
                            else:
                                logger.warning(
                                    "dgr_btc_single_leg_paired_cancel_failed",
                                    paired_order_id=paired.order_id,
                                    paired_market=paired.market.value,
                                )
                        except Exception:
                            logger.exception(
                                "dgr_btc_single_leg_paired_cancel_exception",
                                paired_order_id=paired.order_id,
                            )
                        try:
                            if paired_race_filled:
                                tail = "paired 也同步成交(race)；已补 on_trade"
                            elif paired_cancel_ok:
                                tail = "已 cancel paired"
                            else:
                                tail = "paired cancel 失败"
                            await self._notify(
                                f"⚠ dgr_btc 单腿 fill: {io.market.value} {io.side} @ ${io.grid_level} "
                                f"成交; {tail} {other_market.value} 单 (id {paired.order_id[:12]}…), "
                                f"maintain 下 tick 重挂新 pair"
                            )
                        except Exception:
                            logger.exception("dgr_btc_single_leg_notify_failed")
                except Exception:
                    logger.exception("dgr_btc_sync_on_trade_failed", order_id=order_id)
            elif status in ("canceled", "rejected", "expired"):
                logger.info(
                    "dgr_btc_pre_place_canceled",
                    order_id=order_id, status=status,
                    market=io.market.value, grid_level=str(io.grid_level),
                )
            else:
                # 还 open 但本地丢了 — 罕见, 重新 register
                logger.warning(
                    "dgr_btc_sync_status_unexpected",
                    order_id=order_id, status=status, filled=str(filled),
                )
                self.inflight_manager.register_local(
                    order_id=order_id, market=io.market,
                    grid_level=io.grid_level, side=io.side,
                )

        # 2. stale → cancel
        for io in stale:
            ok = await self._broker_adapter.cancel_order_by_market(io.order_id, io.market)
            if ok:
                self.inflight_manager.force_unregister(io.order_id)
                logger.info(
                    "dgr_btc_stale_inflight_canceled",
                    order_id=io.order_id, market=io.market.value,
                    grid_level=str(io.grid_level),
                )

    def _estimate_maker_fee(
        self, market: MarketType, price: Decimal, qty: Decimal
    ) -> Decimal:
        notional = price * qty
        if market == MarketType.SPOT:
            return notional * self.cfg.backtest_spot_fee
        return notional * self.cfg.backtest_perp_maker_fee

    async def _maintain_resting_pairs(self, market: MarketState) -> None:
        """保证 binance 簿上永远挂着 next upper grid SELL + next lower grid BUY.

        recenter / trend halt / DEFENSIVE 时 cancel ALL inflight, 不挂新.
        """
        if not self._pre_place_enabled or self._broker_adapter is None:
            return
        if self.strategy is None:
            return

        # 1. trend halt 激活 → 不挂新 + cancel 全部 inflight
        is_trending = self.strategy.grid.is_trending(
            self.cfg.risk_trend_grids_threshold
        )
        if is_trending:
            await self._cancel_all_inflight(reason="trend_halt")
            return

        # 2. risk DEFENSIVE 或 EMERGENCY → 同样停挂
        if self.strategy.risk.last_report is not None:
            level = self.strategy.risk.last_report.level.value
            if level in ("DEFENSIVE", "EMERGENCY"):
                await self._cancel_all_inflight(reason=f"risk_{level.lower()}")
                return

        # 3. recenter 触发 → cancel + 重算
        center = self.strategy.center
        if (
            self._last_maintain_center is not None
            and self._last_maintain_center != center
        ):
            await self._cancel_all_inflight(reason="recenter")
        self._last_maintain_center = center

        # 4. 算 target grid level — Phase H.live C方案：perp 锚定 spot_level (同步触发)
        # 历史背景：之前 perp_upper = perp_mark + step，spot 触发时 perp 距离 perp_level
        #   多 (spot_mark - center) ≈ $200~$500 → 单腿率近 100% (LIVE 实测 2026-05-25).
        # 新设计：perp_upper = spot_upper + maker_safety，让 spot/perp 几乎同时穿越.
        #   maker_safety $20 保 perp 端是 maker (post-only) — BTC spot/perp basis 通常
        #   ±$30~±$100，$20 offset 既保 maker 又紧跟 spot. spot fill 时 perp 平均差
        #   $20，秒级追上即同步成交.
        # InflightOrder.grid_level 仍用 spot_level 标记，paired 检测一致.
        step = self.cfg.grid_step_usdt
        qty = self.cfg.grid_qty_per_grid
        maker_safety = Decimal("20")    # perp 相对 spot grid 的 maker 偏移
        spot_upper = center + step                  # SELL spot 上格
        spot_lower = center - step                  # BUY  spot 下格
        perp_upper = spot_upper + maker_safety      # SELL perp: 略高于 spot, maker
        perp_lower = spot_lower - maker_safety      # BUY  perp: 略低于 spot, maker

        # 5. cap 检查 — paired_inverse: 上格双 SELL（spot 减仓 + perp 加空）/ 下格双 BUY（spot 加仓 + perp 平空）
        spot_qty = self.strategy.spot_pos.quantity
        perp_qty_abs = abs(self.strategy.perp_pos.quantity)
        upper_ok = (
            spot_qty - qty >= _ZERO                          # spot 可卖
            and perp_qty_abs + qty <= self.cfg.max_short_btc  # perp short 还有加空空间
        )
        lower_ok = (
            spot_qty + qty <= self.cfg.max_spot_btc           # spot 还有加仓空间
            and perp_qty_abs - qty >= _ZERO                   # perp short 可平
        )

        # 6. 派单 — paired_inverse 设计：
        #    上穿 spot SELL + perp SELL（加空，delta 双向降）→ 两侧都高价 SELL = maker
        #    下穿 spot BUY  + perp BUY （平空，delta 双向升）→ 两侧都低价 BUY  = maker
        tol = step / Decimal("4")
        if upper_ok and self.inflight_manager.find_by_level(
            MarketType.SPOT, spot_upper, tol=tol
        ) is None:
            await self._place_pair(
                spot_level=spot_upper, perp_level=perp_upper, qty=qty,
                spot_side=Side.SELL, perp_side=Side.SELL,
                market_state=market,
            )
        if lower_ok and self.inflight_manager.find_by_level(
            MarketType.SPOT, spot_lower, tol=tol
        ) is None:
            await self._place_pair(
                spot_level=spot_lower, perp_level=perp_lower, qty=qty,
                spot_side=Side.BUY, perp_side=Side.BUY,
                market_state=market,
            )

    async def _cancel_all_inflight(self, reason: str) -> None:
        spot_orders = self.inflight_manager.get_inflight(MarketType.SPOT)
        perp_orders = self.inflight_manager.get_inflight(MarketType.PERP)
        all_orders = [(o, MarketType.SPOT) for o in spot_orders] + [
            (o, MarketType.PERP) for o in perp_orders
        ]
        if not all_orders:
            return
        logger.info(
            "dgr_btc_cancel_all_inflight",
            reason=reason, count=len(all_orders),
        )
        for io, market in all_orders:
            try:
                ok = await self._broker_adapter.cancel_order_by_market(io.order_id, market)
                if ok:
                    self.inflight_manager.force_unregister(io.order_id)
            except Exception:
                logger.exception("dgr_btc_cancel_inflight_failed", order_id=io.order_id)

    async def _place_pair(
        self,
        spot_level: Decimal,
        perp_level: Decimal,
        qty: Decimal,
        spot_side: Side,
        perp_side: Side,
        market_state: MarketState,
    ) -> None:
        """派一对 (spot + perp) LIMIT_MAKER 单, fire-and-forget.

        paired_inverse: 上穿双 SELL / 下穿双 BUY → 两侧都是同方向 maker side.
        spot_level / perp_level 可以略有偏差（适配 spot/perp basis），都是 maker.

        spot reject → 不挂 perp.
        spot 派成 + perp reject → 立即 cancel spot 那张刚挂的单 (还没 fill).
        """
        from app.strategies.dgr_btc.broker_adapter import RejectError

        # 派 spot @ spot_level
        try:
            spot_trade = await self._broker_adapter.place_limit_maker(
                market=MarketType.SPOT, side=spot_side, price=spot_level, quantity=qty,
                no_wait=True,
            )
        except RejectError as e:
            logger.info(
                "dgr_btc_maintain_spot_rejected",
                spot_level=str(spot_level), side=spot_side.value, reason=str(e)[:120],
            )
            return
        except Exception:
            logger.exception("dgr_btc_maintain_spot_failed", spot_level=str(spot_level))
            return

        self.inflight_manager.register_local(
            order_id=spot_trade.order_id, market=MarketType.SPOT,
            grid_level=spot_level, side=spot_side.value,
        )

        # 派 perp @ perp_level
        try:
            perp_trade = await self._broker_adapter.place_limit_maker(
                market=MarketType.PERP, side=perp_side, price=perp_level, quantity=qty,
                no_wait=True,
            )
        except RejectError as e:
            logger.info(
                "dgr_btc_maintain_perp_rejected_unwind_spot",
                spot_level=str(spot_level), perp_level=str(perp_level),
                side=perp_side.value, reason=str(e)[:120],
                spot_order_id=spot_trade.order_id,
            )
            try:
                await self._broker_adapter.cancel_order_by_market(
                    spot_trade.order_id, MarketType.SPOT,
                )
                self.inflight_manager.force_unregister(spot_trade.order_id)
            except Exception:
                logger.exception("dgr_btc_maintain_spot_cancel_failed")
            return
        except Exception:
            logger.exception("dgr_btc_maintain_perp_failed", spot_level=str(spot_level))
            return

        # perp inflight grid_level 用 spot_level（保 paired 检测一致）
        self.inflight_manager.register_local(
            order_id=perp_trade.order_id, market=MarketType.PERP,
            grid_level=spot_level, side=perp_side.value,
        )
        logger.info(
            "dgr_btc_maintain_pair_placed",
            spot_level=str(spot_level), perp_level=str(perp_level),
            spot_id=spot_trade.order_id, spot_side=spot_side.value,
            perp_id=perp_trade.order_id, perp_side=perp_side.value,
            qty=str(qty),
        )

    def _mock_fill(self, intent: OrderIntent, market: MarketState) -> Trade:
        """paper-only mock fill.
        fill_price = intent.price (穿越即成交假设),
        fee = notional * (spot_fee | perp_maker_fee).
        """
        notional = intent.price * intent.quantity
        if intent.market == MarketType.SPOT:
            fee = notional * self.cfg.backtest_spot_fee
        else:
            fee = notional * self.cfg.backtest_perp_maker_fee
        return Trade(
            trade_id=f"dgr_{_uuid.uuid4().hex[:12]}",
            order_id=f"paper_{_uuid.uuid4().hex[:8]}",
            symbol=(
                self.cfg.symbol_spot
                if intent.market == MarketType.SPOT
                else self.cfg.symbol_perp
            ),
            market=intent.market,
            side=intent.side,
            price=intent.price,
            quantity=intent.quantity,
            fee=fee,
            is_maker=True,
            timestamp=market.timestamp,
            grid_level=intent.grid_level,
        )

    # ------------------------------------------------------------------
    # 持久化 (Phase C.5 DB + C.6 jsonl)
    # ------------------------------------------------------------------


    def _persist_strategy_state_file(self) -> None:
        """Phase G.5: 写 strategy 当前 state 到 /app/state/dgr_btc_state.json.

        每 60s 调一次. restart 时由 start() 读回. 替代 consume-once init_state.json.
        """
        if self.strategy is None:
            return
        try:
            from datetime import datetime, timezone
            state = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "spot_qty": str(self.strategy.spot_pos.quantity),
                "perp_qty": str(self.strategy.perp_pos.quantity),
                "spot_avg_entry": str(self.strategy.spot_pos.avg_entry),
                "perp_avg_entry": str(self.strategy.perp_pos.avg_entry),
                "cash": str(self.strategy.cash),
                "center": str(self.strategy.center),
                "n_recenters": self.strategy.n_recenters,
                "grid_lower": str(self.strategy.grid.lower_bound),
                "grid_upper": str(self.strategy.grid.upper_bound),
                "realized_pnl": str(self.strategy.perp_pos.realized_pnl),
                "total_fees": str(self.strategy.total_fees),
                "funding_paid": str(self.strategy.funding_paid),
                # Phase H.6: 持久化 inflight orders → restart 后立即恢复（不依赖 60s orphan recovery）
                "inflight": [
                    {
                        "order_id": io.order_id,
                        "market": io.market.value,
                        "grid_level": str(io.grid_level),
                        "side": io.side,
                        "placed_at": io.placed_at.isoformat(),
                    }
                    for market in [MarketType.SPOT, MarketType.PERP]
                    for io in self.inflight_manager.get_inflight(market)
                ],
            }
            # CRITICAL #2: 持久化 safety counters (daily_notional / daily_count / counter_date)
            # 防止重启清零 → daily cap 跨重启绕过. pending_* 故意不持久化(进程死即清).
            if (
                self._broker_adapter is not None
                and getattr(self._broker_adapter, "safety", None) is not None
            ):
                try:
                    state["safety"] = self._broker_adapter.safety.to_dict()
                except Exception:
                    logger.exception("dgr_btc_safety_to_dict_failed")
            os.makedirs("/app/state", exist_ok=True)
            # write-atomic: tmp + rename
            tmp = "/app/state/dgr_btc_state.json.tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, "/app/state/dgr_btc_state.json")
        except Exception:
            logger.exception("dgr_btc_state_file_persist_failed")

    def _persist_trade_jsonl(self, intent: OrderIntent, trade: Trade) -> None:
        """每笔 trade 写 /app/state/dgr_btc_trades.jsonl (简化持久化)."""
        try:
            os.makedirs(os.path.dirname(_TRADES_JSONL), exist_ok=True)
            entry = {
                "ts": trade.timestamp.isoformat(),
                "mode": "PAPER" if not self.live_mode else "LIVE",
                "market": intent.market.value,
                "side": intent.side.value,
                "price": str(trade.price),
                "quantity": str(trade.quantity),
                "fee": str(trade.fee),
                "grid_level": (
                    str(intent.grid_level)
                    if intent.grid_level is not None
                    else None
                ),
                "reason": intent.reason,
                "order_id": trade.order_id,
                "trade_id": trade.trade_id,
                "spot_qty_after": str(self.strategy.spot_pos.quantity),
                "perp_qty_after": str(self.strategy.perp_pos.quantity),
                "cash_after": str(self.strategy.cash),
                "short_basis_after": str(self.strategy.perp_pos.avg_entry),
                "n_recenters": self.strategy.n_recenters,
            }
            with open(_TRADES_JSONL, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("dgr_btc_trade_persist_failed", error=str(e)[:120])

    async def _persist_position_to_db(self) -> None:
        """把 strategy 当前 spot+perp 仓位写 positions 表 (strategy_instance='dgr_btc_main').

        这跟交易所真实持仓**对账**的复杂逻辑放 Phase E (复用 #02 balance_reconciler).
        Phase C 仅记 strategy 内部视图.
        """
        if self.position_manager is None:
            return
        # 用 app.risk.models Position 包装 (dracula 共享层)
        from app.risk.models import (
            ExitReason,
            Position as DomainPosition,
            PositionLeg,
            PositionStatus,
        )
        from app.exchanges.models import InstrumentType, Side as DomainSide, Symbol

        sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)

        # 构造 2 条 leg (spot + perp)
        spot_leg = PositionLeg(
            exchange=self.cfg.exchange,
            symbol=sym,
            instrument_type=InstrumentType.SPOT,
            side=DomainSide.BUY,  # 现货 long
            size=self.strategy.spot_pos.quantity,
            entry_price=self.strategy.spot_pos.avg_entry,
            leverage=Decimal("1"),
        )
        perp_leg = PositionLeg(
            exchange=self.cfg.exchange,
            symbol=sym,
            instrument_type=InstrumentType.PERPETUAL,
            side=DomainSide.SELL,  # 永续 short
            size=abs(self.strategy.perp_pos.quantity),
            entry_price=self.strategy.perp_pos.avg_entry,
            leverage=Decimal(self.cfg.leverage),
        )

        notional = (
            self.strategy.spot_pos.quantity * self.strategy.spot_pos.avg_entry
            + abs(self.strategy.perp_pos.quantity) * self.strategy.perp_pos.avg_entry
        )

        # 用固定 instance UUID 形式: dgr_btc_main 所有数据落同一行
        pos = DomainPosition(
            id=DGR_BTC_SINGLETON_UUID,  # 单例策略 (uuid5)
            strategy_instance="dgr_btc_main",
            symbol=sym,
            notional_usd=notional,
            legs=[spot_leg, perp_leg],
            status=PositionStatus.OPEN,
            realized_pnl=self.strategy.perp_pos.realized_pnl,
            funding_received=self.strategy.funding_paid,
            fees_paid=self.strategy.total_fees,
            opened_at=datetime.now(timezone.utc),
            notes=json.dumps({
                "center": str(self.strategy.center),
                "n_recenters": self.strategy.n_recenters,
                "last_recenter_ts": (
                    self.strategy.last_recenter_ts.isoformat()
                    if self.strategy.last_recenter_ts
                    else None
                ),
                "grid_lower": str(self.strategy.grid.lower_bound),
                "grid_upper": str(self.strategy.grid.upper_bound),
                "cash_usdt": str(self.strategy.cash),
            }, ensure_ascii=False),
        )
        try:
            await self.position_manager.save(pos)
        except Exception:
            logger.exception("dgr_btc_db_save_failed")

    # ------------------------------------------------------------------
    # Restore (Phase C.8)
    # ------------------------------------------------------------------

    async def _try_restore_state(self) -> Optional[dict]:
        """从 DB positions 表加载 dgr_btc_main 单例仓位状态.

        Returns None if no open position found.
        """
        if self.position_manager is None:
            return None
        try:
            # 尝试加载所有 open positions, 找 instance='dgr_btc_main' 的
            n_loaded = await self.position_manager.load_open_positions()
            if n_loaded == 0:
                return None
            # 用 position_manager 内部接口找
            pos = None
            for p in self.position_manager._positions.values() if hasattr(
                self.position_manager, "_positions"
            ) else []:
                if p.strategy_instance == "dgr_btc_main":
                    pos = p
                    break
            if pos is None:
                return None
            # 解 notes JSON 拿 center/recenter/grid 状态
            meta = {}
            try:
                meta = json.loads(pos.notes or "{}")
            except Exception:
                pass
            spot_leg = next(
                (l for l in pos.legs if l.instrument_type.value == "spot"),
                None,
            )
            perp_leg = next(
                (l for l in pos.legs if l.instrument_type.value == "perpetual"),
                None,
            )
            return {
                "center": Decimal(meta.get("center", "80000")),
                "n_recenters": int(meta.get("n_recenters", 0)),
                "last_recenter_ts": meta.get("last_recenter_ts"),
                "grid_lower": Decimal(meta.get("grid_lower", "68000")),
                "grid_upper": Decimal(meta.get("grid_upper", "92000")),
                "cash_usdt": Decimal(meta.get("cash_usdt", "0")),
                "spot_qty": spot_leg.size if spot_leg else _ZERO,
                "spot_avg_entry": spot_leg.entry_price if spot_leg else _ZERO,
                "perp_qty": perp_leg.size if perp_leg else _ZERO,
                "perp_avg_entry": perp_leg.entry_price if perp_leg else _ZERO,
                "realized_pnl": pos.realized_pnl,
                "fees_paid": pos.fees_paid,
                "funding_received": pos.funding_received,
            }
        except Exception as e:
            # C6 修复 (LIVE 审计): restore 失败必须 fail-loud, 不再静默 return None.
            # 静默 fresh init 会在 LIVE 模式下导致 grid 与真实仓位错位 → 乱单.
            # paper 模式可接受 fresh init, 但仍记 error level log + raise 让上层选择.
            logger.error(
                "dgr_btc_restore_failed_FAIL_LOUD",
                error=str(e)[:200],
                live_mode=self.live_mode,
            )
            if self.live_mode:
                raise RuntimeError(
                    f"dgr_btc state restore failed in LIVE mode — refuse fresh init: {e}"
                ) from e
            # paper 模式仍兼容老行为, 但已升级到 error level
            return None

    def _apply_restored_state(self, st: dict) -> None:
        """把 _try_restore_state 返回的 dict 应用到 strategy."""
        if self.strategy is None:
            return
        self.strategy.center = st["center"]
        self.strategy.n_recenters = st["n_recenters"]
        self.strategy.spot_pos.quantity = st["spot_qty"]
        self.strategy.spot_pos.avg_entry = st["spot_avg_entry"]
        # short 在 internal 是负值
        self.strategy.perp_pos.quantity = -st["perp_qty"]
        self.strategy.perp_pos.avg_entry = st["perp_avg_entry"]
        self.strategy.perp_pos.realized_pnl = st["realized_pnl"]
        self.strategy.total_fees = st["fees_paid"]
        self.strategy.funding_paid = st["funding_received"]
        self.strategy.cash = st["cash_usdt"]
        if st.get("last_recenter_ts"):
            try:
                self.strategy.last_recenter_ts = datetime.fromisoformat(
                    st["last_recenter_ts"]
                )
            except Exception:
                pass
        # rebuild grid 与 restored bounds 一致
        self.strategy.grid.lower_bound = st["grid_lower"]
        self.strategy.grid.upper_bound = st["grid_upper"]
        self.strategy.grid.levels = self.strategy.grid._build_levels()
        self.strategy.grid.last_price = st["center"]
        self.strategy.grid.last_grid_index = self.strategy.grid.find_grid_index(
            st["center"]
        )
        self._bootstrap_done = True

    # ------------------------------------------------------------------
    # 价格 / funding fetch (优先 MarketDataHub, fallback adapter)
    # ------------------------------------------------------------------

    async def _fetch_spot_price(self) -> Decimal:
        if self.hub is not None:
            try:
                t = self.hub.get_ticker(self.cfg.exchange, self.cfg.symbol_spot)
                if t and t.last:
                    return Decimal(str(t.last))
            except Exception:
                pass
        from app.exchanges.models import InstrumentType, Symbol
        sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
        t = await self.adapter.fetch_ticker(sym, InstrumentType.SPOT)
        return Decimal(str(t.last)) if t and t.last else _ZERO

    async def _fetch_perp_price(self) -> Decimal:
        if self.hub is not None:
            try:
                t = self.hub.get_ticker(self.cfg.exchange, self.cfg.symbol_perp)
                if t and t.last:
                    return Decimal(str(t.last))
            except Exception:
                pass
        from app.exchanges.models import InstrumentType, Symbol
        sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
        t = await self.adapter.fetch_ticker(sym, InstrumentType.PERPETUAL)
        return Decimal(str(t.last)) if t and t.last else _ZERO

    async def _fetch_funding_rate(self) -> Decimal:
        try:
            from app.exchanges.models import Symbol
            sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
            fr = await self.adapter.fetch_funding_rate(sym)
            return Decimal(str(fr.rate)) if fr and fr.rate else _ZERO
        except Exception:
            return _ZERO

    def _is_funding_settle_time(self, now: datetime) -> bool:
        """检测当前时间是否落在 8h funding settle 边界 (UTC 00/08/16 整点 ±1min).
        防重复触发: 同一 8h 槽位只 settle 一次.
        """
        if now.hour % 8 != 0 or now.minute > 1:
            return False
        slot_ts = int(
            now.replace(minute=0, second=0, microsecond=0).timestamp() * 1000
        )
        if slot_ts <= self._last_funding_settle_ts:
            return False
        self._last_funding_settle_ts = slot_ts
        return True

    # ------------------------------------------------------------------
    # Telegram alert (Phase C.7) — fire-and-forget
    # ------------------------------------------------------------------

    async def _notify(self, message: str) -> None:
        if not self.cfg.live_safety_telegram_alerts_enabled:
            return
        try:
            from app.notifications import notify_system
            # notify_system 是 sync 函数, 用 to_thread 避免阻塞 event loop
            await asyncio.to_thread(notify_system, message)
        except Exception:
            # fire-and-forget: 失败不影响策略
            logger.warning("dgr_btc_notify_failed", message=message[:80])

    # ------------------------------------------------------------------
    # 配置热更新 (mirror hedged_grid update_cfg)
    # ------------------------------------------------------------------

    def update_cfg(self, overrides: dict) -> None:
        """运行时 PATCH overrides → 重建 config + 同步 strategy."""
        if self.strategy is None:
            self.cfg = self.cfg.apply_overrides(overrides)
            return
        new_cfg = self.cfg.apply_overrides(overrides)
        # grid 任意参数变化 → 重建 GridManager
        rebuild_grid = (
            new_cfg.grid_step_usdt != self.cfg.grid_step_usdt
            or new_cfg.grid_qty_per_grid != self.cfg.grid_qty_per_grid
            or new_cfg.width_pct != self.cfg.width_pct
        )
        # delta / position_limits 变化 → 重建 DeltaHedger
        rebuild_hedger = (
            new_cfg.delta_upper_limit != self.cfg.delta_upper_limit
            or new_cfg.delta_lower_limit != self.cfg.delta_lower_limit
            or new_cfg.max_spot_btc != self.cfg.max_spot_btc
            or new_cfg.max_short_btc != self.cfg.max_short_btc
            or new_cfg.delta_target != self.cfg.delta_target
            or new_cfg.delta_rebalance_threshold != self.cfg.delta_rebalance_threshold
        )
        # risk 任意参数变化 → 重建 RiskFilter
        rebuild_risk = (
            new_cfg.risk_trend_grids_threshold != self.cfg.risk_trend_grids_threshold
            or new_cfg.risk_hourly_vol_threshold != self.cfg.risk_hourly_vol_threshold
            or new_cfg.risk_margin_ratio_min != self.cfg.risk_margin_ratio_min
            or new_cfg.risk_max_daily_loss_pct != self.cfg.risk_max_daily_loss_pct
            or new_cfg.risk_max_drawdown_pct != self.cfg.risk_max_drawdown_pct
            or new_cfg.total_capital_usdt != self.cfg.total_capital_usdt
        )
        # leverage 变化 → 同步 perp legs
        leverage_changed = new_cfg.leverage != self.cfg.leverage

        self.cfg = new_cfg
        # ⭐ 同步 strategy.config (否则 strategy 内部用旧 cfg)
        self.strategy.config = new_cfg
        if rebuild_grid:
            from app.strategies.dgr_btc.grid_manager import GridManager
            old_last_price = self.strategy.grid.last_price
            self.strategy.grid = GridManager.from_center(
                center=self.strategy.center,
                width_pct=new_cfg.width_pct,
                step_usdt=new_cfg.grid_step_usdt,
                qty_per_grid=new_cfg.grid_qty_per_grid,
            )
            if old_last_price:
                self.strategy.grid.last_price = old_last_price
                self.strategy.grid.last_grid_index = (
                    self.strategy.grid.find_grid_index(old_last_price)
                )
        if rebuild_hedger:
            from app.strategies.dgr_btc.delta_hedger import DeltaHedger
            self.strategy.hedger = DeltaHedger(
                upper_limit=new_cfg.delta_upper_limit,
                lower_limit=new_cfg.delta_lower_limit,
                max_spot=new_cfg.max_spot_btc,
                max_short=new_cfg.max_short_btc,
                target=new_cfg.delta_target,
                rebalance_threshold=new_cfg.delta_rebalance_threshold,
            )
        if rebuild_risk:
            from app.strategies.dgr_btc.risk_filter import RiskFilter
            self.strategy.risk = RiskFilter(
                trend_grids_threshold=new_cfg.risk_trend_grids_threshold,
                hourly_vol_threshold=new_cfg.risk_hourly_vol_threshold,
                margin_ratio_min=new_cfg.risk_margin_ratio_min,
                funding_filter_enabled=new_cfg.risk_funding_filter_enabled,
                funding_threshold=new_cfg.risk_funding_threshold,
                min_orderbook_depth=new_cfg.risk_min_orderbook_depth_usdt,
                max_daily_loss_pct=new_cfg.risk_max_daily_loss_pct,
                max_drawdown_pct=new_cfg.risk_max_drawdown_pct,
                initial_equity=new_cfg.total_capital_usdt,
            )
        if leverage_changed:
            logger.info(
                "dgr_btc_leverage_changed",
                old=self.cfg.leverage,
                new=new_cfg.leverage,
            )
        logger.info(
            "dgr_btc_cfg_updated",
            overrides=list(overrides.keys()),
            rebuild_grid=rebuild_grid,
            rebuild_hedger=rebuild_hedger,
            rebuild_risk=rebuild_risk,
        )

    # ------------------------------------------------------------------
    # 状态快照 (API 用)
    # ------------------------------------------------------------------

    def is_running(self) -> bool:
        return self._running

    def _snapshot_orders(self) -> list[dict]:
        """Phase H: 当前 inflight 快照 (供 UI LIVE ORDERS panel)。

        paper / pre_place 未启用 → 返回空数组（UI 显示 empty state）。
        live + pre_place 启用 → 从 inflight_manager 拿。
        """
        if not getattr(self, "_pre_place_enabled", False):
            return []
        out: list[dict] = []
        try:
            for market in [MarketType.SPOT, MarketType.PERP]:
                for io in self.inflight_manager.get_inflight(market):
                    out.append({
                        "order_id": io.order_id,
                        "market": market.value,
                        "side": io.side,
                        "price": str(io.grid_level),
                        "quantity": str(self.cfg.grid_qty_per_grid),
                        "grid_level": str(io.grid_level),
                        "status": "PLACED",
                        "placed_at": io.placed_at.isoformat(),
                    })
        except Exception:
            logger.exception("dgr_btc_snapshot_orders_failed")
        return out

    def snapshot(self) -> dict:
        """API 暴露给 UI 的运行时状态."""
        if self.strategy is None:
            return {
                "instance": self.cfg.instance_name,
                "running": self._running,
                "enabled": self.cfg.enabled,
                "ready": False,
                "reason": "strategy_not_initialized",
            }
        try:
            # Phase H UI 修复：优先用 _tick 缓存的真实价（_last_spot_px / _last_perp_px）
            # 否则 strategy.grid.last_price 初期 None → mark 假象 = center 重合。
            mark = (
                self._last_spot_px
                or self.strategy.grid.last_price
                or self.strategy.center
            )
            perp_mark = self._last_perp_px or mark
            market = MarketState(
                timestamp=self._last_tick_at or datetime.now(timezone.utc),
                spot_price=mark,
                perp_price=perp_mark,
            )
            snap = self.strategy.get_snapshot(market)
            # 详细仓位 — 给 UI 持仓 panel 用
            spot_pos = self.strategy.spot_pos
            perp_pos = self.strategy.perp_pos
            spot_notional = spot_pos.quantity * mark
            perp_qty_abs = abs(perp_pos.quantity)
            perp_notional = perp_qty_abs * mark
            # perp margin used = notional / leverage
            perp_margin = (
                perp_notional / Decimal(self.cfg.leverage)
                if self.cfg.leverage > 0 else _ZERO
            )
            # equity breakdown
            cash_part = self.strategy.cash
            spot_value = spot_notional
            perp_unreal = perp_pos.unrealized_pnl
            perp_real = perp_pos.realized_pnl
            return {
                "instance": self.cfg.instance_name,
                "running": self._running,
                "live_mode": self.live_mode,
                "enabled": self.cfg.enabled,
                "ready": True,
                "last_tick_at": (
                    self._last_tick_at.isoformat() if self._last_tick_at else None
                ),
                "n_ticks": self._n_ticks,
                "n_intents": self._n_intents_generated,
                "n_trades": self._n_trades_executed,
                "n_recenters": self.strategy.n_recenters,
                "spot_qty": str(snap.spot_position.quantity),
                "perp_qty": str(snap.perp_position.quantity),
                "delta": str(snap.delta),
                "cash_usdt": str(snap.cash_usdt),
                "total_equity": str(snap.total_equity),
                "funding_paid": str(snap.funding_paid),
                "total_fees": str(snap.total_fees),
                "mark_price": str(mark),
                "center": str(self.strategy.center),
                "trend_count": self.strategy.grid.consecutive_direction_grids,
                "trend_threshold": self.cfg.risk_trend_grids_threshold,
                "is_trending": self.strategy.grid.is_trending(
                    self.cfg.risk_trend_grids_threshold
                ),
                "grid_stats": self.strategy.grid.stats(),
                "risk_level": (
                    self.strategy.risk.last_report.level.value
                    if self.strategy.risk.last_report
                    else "UNKNOWN"
                ),
                "risk_messages": (
                    self.strategy.risk.last_report.messages
                    if self.strategy.risk.last_report
                    else []
                ),
                "init_capital_usdt": str(self.cfg.total_capital_usdt),
                "leverage": self.cfg.leverage,
                "width_pct": str(self.cfg.width_pct),
                "recenter_trigger_pct": str(self.cfg.recenter_trigger_pct),
                # 详细仓位 panel 数据
                "positions": {
                    "spot": {
                        "qty": str(spot_pos.quantity),
                        "avg_entry": str(spot_pos.avg_entry),
                        "mark_price": str(mark),
                        "notional_usd": str(spot_notional),
                        "unrealized_pnl": str(spot_pos.unrealized_pnl),
                        "realized_pnl": str(spot_pos.realized_pnl),
                    },
                    "perp": {
                        "qty": str(perp_pos.quantity),
                        "qty_abs": str(perp_qty_abs),
                        "side": "SHORT" if perp_pos.quantity < 0 else "LONG" if perp_pos.quantity > 0 else "FLAT",
                        "avg_entry": str(perp_pos.avg_entry),
                        "short_basis": str(perp_pos.avg_entry),
                        "mark_price": str(mark),
                        "notional_usd": str(perp_notional),
                        "margin_used_usd": str(perp_margin),
                        "unrealized_pnl": str(perp_unreal),
                        "realized_pnl": str(perp_real),
                        "leverage": self.cfg.leverage,
                    },
                    "equity_breakdown": {
                        "cash": str(cash_part),
                        "spot_value": str(spot_value),
                        "perp_unrealized": str(perp_unreal),
                        "perp_realized": str(perp_real),
                        "total": str(snap.total_equity),
                    },
                    "limits": {
                        "max_spot_btc": str(self.cfg.max_spot_btc),
                        "max_short_btc": str(self.cfg.max_short_btc),
                        "delta_upper": str(self.cfg.delta_upper_limit),
                        "delta_lower": str(self.cfg.delta_lower_limit),
                        "spot_used_pct": str(
                            (spot_pos.quantity / self.cfg.max_spot_btc * Decimal("100"))
                            if self.cfg.max_spot_btc > 0 else _ZERO
                        ),
                        "short_used_pct": str(
                            (perp_qty_abs / self.cfg.max_short_btc * Decimal("100"))
                            if self.cfg.max_short_btc > 0 else _ZERO
                        ),
                    },
                },
                # Phase H: LIVE ORDERS panel — 当前簿上 inflight 挂单快照
                "orders": self._snapshot_orders(),
                "pre_place_enabled": self._pre_place_enabled,
            }
        except Exception:
            logger.exception("dgr_btc_snapshot_failed")
            return {
                "instance": self.cfg.instance_name,
                "running": self._running,
                "error": "snapshot_unavailable",
            }


# ============================================================
# Factory (mirror hedged_grid build_paper_session pattern)
# ============================================================


def build_dgr_btc_paper_session(
    cfg: DgrBtcStrategyConfig,
    adapter: Any,
    market_data_hub: Any | None = None,
    position_manager: Any | None = None,
    tick_interval_seconds: float = 30.0,
    live_mode: bool = False,
) -> DgrBtcPaperSession:
    """生成 PaperSession 实例 (供 main.py lifespan 调用)."""
    return DgrBtcPaperSession(
        cfg=cfg,
        adapter=adapter,
        market_data_hub=market_data_hub,
        position_manager=position_manager,
        tick_interval_seconds=tick_interval_seconds,
        live_mode=live_mode,
    )
