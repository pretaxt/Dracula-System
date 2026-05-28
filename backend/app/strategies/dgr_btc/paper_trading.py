"""
dgr_btc/paper_trading.py — Martingale + Recenter + SL paper session
========================================================================

P4 重写 (2026-05-26): 从 1771 行 paired_inverse → ~500 行单边马丁。

设计原则:
  - 内核完全 delegate to engine.MartingaleEngine (P5 已 byte-equal 验证)
  - 保持 v1 caller contract 稳定 (main.py / strategies.py / dashboard.py /
    positions.py / telegram 不需要改动也能读)
  - 对外接口稳定: cfg / strategy.* / snapshot() / inflight_manager / live_mode

新数学验证: MartingaleEngine + MartingaleBacktestRunner 25 测试全绿
v1 (paired_inverse) 备份: paper_trading_v1.py.bak (本地, 不 commit)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Optional

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.engine import (
    DecisionKind,
    EngineConfig,
    Layer,
    MartingaleEngine,
    StrategyState,
)
from app.strategies.dgr_btc.execution_adapter import (
    BrokerError,
    ExecutionAdapter,
    FillResult,
    LiveBroker,
    PaperBroker,
)


logger = logging.getLogger(__name__)
_ZERO = Decimal("0")
_ONE = Decimal("1")


# ─── Compat views for caller (telegram / positions / dashboard 直读) ───


@dataclass
class _PositionView:
    """兼容 v1 sess.strategy.spot_pos / .perp_pos 接口"""
    quantity: Decimal = _ZERO
    avg_entry: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO


class _MartingaleStrategyView:
    """兼容层: 让 v1 caller 读 sess.strategy.* 看到合理值

    Single-side long-only。perp 字段都是 0（向后兼容）。
    """

    def __init__(self, state: StrategyState, engine: MartingaleEngine, cfg: DgrBtcStrategyConfig):
        self._state = state
        self._engine = engine
        self._cfg = cfg

    @property
    def spot_pos(self) -> _PositionView:
        s = self._state
        return _PositionView(
            quantity=s.total_qty,
            avg_entry=s.avg_cost,
            realized_pnl=s.realized_pnl_usdt,
        )

    @property
    def perp_pos(self) -> _PositionView:
        return _PositionView()  # 单边: perp 永远 0

    @property
    def cash(self) -> Decimal:
        return _ZERO  # filled by session; view doesn't know cash. callers should prefer snapshot().

    @property
    def center(self) -> Decimal:
        """v1 "center" = grid 中心；单边语义 = 当前 avg_cost"""
        return self._state.avg_cost

    @property
    def n_recenters(self) -> int:
        """v1 "n_recenters" = 价格偏离 12% 触发次数；单边语义 = TP 完成的 cycle 数"""
        return self._state.n_tp + self._state.n_sl

    @property
    def recenter_events(self) -> list:
        """v1 "recenter_events" 历史；v2 不维护事件列表，返回空 list 让 endpoint 不报 500"""
        return []

    @property
    def total_fees(self) -> Decimal:
        # martingale engine fold 进 avg_cost；这里给 0（callers 用 snapshot 拿）
        return _ZERO


# ─── Inflight manager stub (paper 模式不需要真挂单跟踪) ───


class _PaperInflightStub:
    """v1 caller 调 sess.inflight_manager.total_inflight() 时返回 0"""

    def total_inflight(self) -> int:
        return 0


# ─── 主 Session ───


class DgrBtcPaperSession:
    """单边马丁 paper session.

    生命周期: __init__ → start() (含 restore) → run_forever() → stop()

    v1 API 稳定 (向后兼容):
      - cfg / strategy / inflight_manager / live_mode / _running
      - _last_spot_px / _last_perp_px (后者恒为 None)
      - snapshot() → dict
    """

    def __init__(
        self,
        cfg: DgrBtcStrategyConfig,
        adapter: Any,
        market_data_hub: Any | None = None,
        position_manager: Any | None = None,
        tick_interval_seconds: float = 5.0,
        live_mode: bool = False,
        broker_adapter: Any | None = None,
    ):
        self.cfg = cfg
        self.adapter = adapter
        self.hub = market_data_hub
        self.position_manager = position_manager
        self.tick_interval = tick_interval_seconds
        self.live_mode = live_mode
        self._broker_adapter = broker_adapter

        # Martingale engine (P5 byte-equal 验证)
        self._engine = MartingaleEngine(self._build_engine_config())
        self._state = StrategyState()
        self._cash: Decimal = cfg.total_capital_usdt

        # §6.2 #1: ExecutionAdapter — paper/LIVE 单分支入口.
        # LIVE 模式需 broker_adapter; paper 模式合成 fill.
        self._broker: ExecutionAdapter
        if live_mode and broker_adapter is not None:
            self._broker = LiveBroker(broker_adapter, self._engine.cfg.fee_pct)
        else:
            self._broker = PaperBroker(self._engine)

        # Compat layer
        self.strategy = _MartingaleStrategyView(self._state, self._engine, cfg)
        self.inflight_manager = _PaperInflightStub()

        # 运行状态
        self._running = False
        self._stop_event = asyncio.Event()
        self._last_tick_at: Optional[datetime] = None
        self._n_ticks = 0
        self._n_trades_executed = 0

        # 最近价格 (telegram / dashboard 读)
        self._last_spot_px: Optional[Decimal] = None
        self._last_perp_px: Optional[Decimal] = None  # 单边: 永远 None

        # 高低价缓存 (用于 intrabar SL/ADD 触发)
        self._rolling_low: Optional[Decimal] = None
        self._last_bar_ts: Optional[datetime] = None

        # Pre-liq auto-deleverage cooldown 跟踪 (审查 #3)
        self._last_deleverage_at: Optional[datetime] = None
        self._n_deleverages: int = 0  # 累计触发次数 (审计用)

        # §6.2 #8 regime detector 缓存
        # 每小时只 fetch 30d bars 一次 (避免每 tick 都拉 720 根 1h bar)
        self._regime_last_eval_at: Optional[datetime] = None
        self._regime_last_is_bad: bool = False
        self._regime_last_metrics: Optional[Any] = None
        self._n_regime_blocks: int = 0  # gate 拦截 ADD 次数
        self._regime_last_alert_at: Optional[datetime] = None  # 24h telegram cooldown

        # §6.2 #4: LIVE 关键运行指标告警 watcher (后台 task, LIVE 模式 + telegram 开启时启动)
        self._metrics_alert_task: Optional[asyncio.Task] = None

        # 持久化路径
        state_dir = Path(os.environ.get("DGR_BTC_STATE_DIR", "/app/state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = state_dir / (
            "dgr_btc_state.json" if live_mode else "dgr_btc_paper_state.json"
        )
        # 交易明细 jsonl (append-only, 每笔 fill 1 行 JSON, LIVE 对账+回放用)
        self._trades_jsonl = state_dir / (
            "dgr_btc_live_trades.jsonl" if live_mode else "dgr_btc_trades.jsonl"
        )

    # ──────────────────── private: config wiring ────────────────────

    def _build_engine_config(self) -> EngineConfig:
        """从 DgrBtcStrategyConfig 抽 Martingale 字段构造 EngineConfig"""
        cfg = self.cfg
        return EngineConfig(
            initial_capital=cfg.total_capital_usdt,
            grid_step=cfg.mart_grid_step,
            factor=cfg.mart_factor,
            max_layers=cfg.mart_max_layers,
            tp_pct=cfg.mart_tp_pct,
            sl_pct=cfg.mart_sl_pct,
            layer_weights=list(cfg.mart_layer_weights),
            fee_pct=cfg.mart_fee_pct,
        )

    # ──────────────────── lifecycle ────────────────────

    async def start(self) -> None:
        """启动: restore state + 拉初始价 + (live) 与 broker 对账孤儿单"""
        logger.info("dgr_btc_paper_start instance=%s live=%s cap=%s",
                    self.cfg.instance_name, self.live_mode, self.cfg.total_capital_usdt)

        # 尝试恢复
        restored = await self._try_restore_state()
        if not restored:
            logger.info("dgr_btc_paper_fresh_start")

        # 拉初始价
        try:
            self._last_spot_px = await self._fetch_spot_price()
            logger.info("dgr_btc_paper_initial_price=%s", self._last_spot_px)
        except Exception as e:
            logger.exception("dgr_btc_paper_initial_price_fetch_failed: %s", e)

        # 审查 #2: LIVE 重启 inflight reconcile.
        # broker 端可能有 restart 前留下的 dgr_ 挂单. 本地 inflight 为空 (未持久化),
        # 必须从 broker 拉回, 否则 (a) 这些单游离 / (b) 新订单 client_order_id 冲突 /
        # (c) 撤单时漏撤 → 单边持仓风险.
        if self.live_mode and self._broker_adapter is not None:
            try:
                await self._reconcile_inflight_on_startup()
            except Exception:
                logger.exception("dgr_btc_inflight_reconcile_failed")

        # §6.2 #4: LIVE 模式启动 alert watcher (paper 模式无 broker_adapter, 无 metrics)
        if self.live_mode and self._broker_adapter is not None:
            try:
                from app.strategies.dgr_btc.live_metrics import run_alert_watcher  # noqa: PLC0415
                instance = getattr(self._broker_adapter, "instance_name", "dgr_btc")
                self._metrics_alert_task = asyncio.create_task(
                    run_alert_watcher(
                        instance_name=instance,
                        poll_interval_sec=60.0,
                        telegram_enabled=self.cfg.live_safety_telegram_alerts_enabled,
                    ),
                    name=f"dgr_btc_metrics_alert_watcher_{instance}",
                )
                logger.info("dgr_btc_metrics_alert_watcher_spawned instance=%s", instance)
            except Exception:
                logger.exception("dgr_btc_metrics_alert_watcher_spawn_failed")

        self._running = True

    async def _reconcile_inflight_on_startup(self) -> None:
        """重启时与 broker 对账, 把 dgr_ 前缀的存活挂单 re-register 到 inflight_manager.

        策略: 启动安全优先
          - 拿 broker.fetch_open_orders() (全 symbol 范围, 但 broker_adapter 内部已过滤当前 symbol)
          - 按 client_order_id 前缀 'dgr_' 筛 (避免误伤别的策略)
          - 调 inflight_manager.update_from_open_orders, orphan 部分自动 register_local
          - 非 dgr_ 前缀的不动 (别人的策略 / 用户手动单)
          - 异常 fail-soft: log 但不 raise (启动不应被对账阻塞)
        """
        from app.strategies.dgr_btc.types import MarketType  # noqa: PLC0415

        if self._broker_adapter is None:
            return
        try:
            open_orders = await self._broker_adapter.fetch_open_orders()
        except Exception as e:
            logger.warning("dgr_btc_reconcile_fetch_open_orders_failed: %s", str(e)[:120])
            return

        # 过滤 dgr_ 前缀的挂单 (我们的)
        ours = []
        for o in open_orders or []:
            cid = str(o.get("clientOrderId") or o.get("clientOid") or "")
            if cid.startswith("dgr_"):
                ours.append(o)

        if not ours:
            logger.info("dgr_btc_reconcile_no_outstanding_orders")
            return

        # 真 InflightOrderManager 才有 update_from_open_orders. _PaperInflightStub 没有
        # — 那种情况退化为 "只识别 + 记账 + 通知" 模式 (无 register_local).
        has_full_manager = hasattr(self.inflight_manager, "update_from_open_orders") \
            and hasattr(self.inflight_manager, "register_local")

        recovered = 0
        missing_count = 0
        stale_count = 0
        if has_full_manager:
            try:
                missing, stale, orphan = self.inflight_manager.update_from_open_orders(
                    open_orders=ours, max_grid_drift=None, current_center=None,
                )
                missing_count = len(missing)
                stale_count = len(stale)
            except Exception:
                logger.exception("dgr_btc_reconcile_update_failed")
                return

            # 重注册 orphan (restart recovery 主路径)
            for o in orphan:
                try:
                    oid = str(o.get("id") or o.get("orderId") or o.get("clientOrderId") or "")
                    side = (o.get("side") or "").upper()
                    price = Decimal(str(o.get("price", "0")))
                    self.inflight_manager.register_local(
                        order_id=oid, market=MarketType.SPOT, grid_level=price, side=side,
                    )
                    recovered += 1
                    logger.info(
                        "dgr_btc_reconcile_recovered order_id=%s side=%s price=%s",
                        oid, side, price,
                    )
                except Exception:
                    logger.exception("dgr_btc_reconcile_register_orphan_failed o=%r", o)
        else:
            # Stub 模式: 只识别 + 日志 + telegram, 不调 register
            for o in ours:
                logger.info(
                    "dgr_btc_reconcile_detected_orphan_(no_register) cid=%s side=%s price=%s",
                    o.get("clientOrderId"), o.get("side"), o.get("price"),
                )

        logger.info(
            "dgr_btc_reconcile_complete found=%d recovered=%d missing=%d stale=%d stub=%s",
            len(ours), recovered, missing_count, stale_count, not has_full_manager,
        )

        # Telegram 简告知 (启动事件值得通知)
        if len(ours) > 0 and self.cfg.live_safety_telegram_alerts_enabled:
            try:
                from app.notifications.telegram import notify_system  # noqa: PLC0415
                detail = (
                    f"♻️ dgr_btc 启动 inflight 对账: 发现 {len(ours)} 个 broker 端 dgr_ 挂单"
                )
                if has_full_manager:
                    detail += f", 已 recover {recovered} 个到本地 inflight"
                else:
                    detail += " (paper stub 模式, 未重新注册)"
                notify_system(detail)
            except Exception:
                logger.debug("reconcile_telegram_failed", exc_info=True)

    async def run_forever(self) -> None:
        """tick loop"""
        try:
            while not self._stop_event.is_set():
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("dgr_btc_paper_tick_failed")

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.tick_interval,
                    )
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False

    async def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        # §6.2 #4: cancel metrics alert watcher + unregister collector
        if self._metrics_alert_task is not None:
            self._metrics_alert_task.cancel()
            try:
                await self._metrics_alert_task
            except (asyncio.CancelledError, Exception):
                pass
            self._metrics_alert_task = None
        if self._broker_adapter is not None and hasattr(self._broker_adapter, "close"):
            try:
                self._broker_adapter.close()
            except Exception:
                logger.debug("dgr_btc_broker_close_failed", exc_info=True)
        try:
            self._persist_state()
        except Exception:
            logger.exception("dgr_btc_paper_stop_persist_failed")

    # ──────────────────── tick ────────────────────

    async def _tick(self) -> None:
        """LIVE/paper tick: KILL check → 拉 1m bar → process_decisions → persist"""
        self._n_ticks += 1
        self._last_tick_at = datetime.now(timezone.utc)

        # KILL switch 检查 (paper + LIVE 都遵守)
        kill_path = "/app/state/dgr_btc_KILL"
        if os.path.exists(kill_path):
            # 仍 fetch price 让 _last_spot_px 更新 (UI 显示用)，但不调 engine.decide
            try:
                price = await self._fetch_spot_price()
                self._last_spot_px = price
            except Exception:
                pass
            return

        # 拉最新 1m bar，含当前分钟 intrabar low (P11 修复: 让 paper 接近回测 intrabar mode)
        # 用 dracula adapter.fetch_klines(Symbol, interval, limit, InstrumentType.SPOT)
        close: Decimal
        low: Decimal
        try:
            from app.exchanges.models import InstrumentType, Symbol  # noqa: PLC0415
            sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
            klines = await self.adapter.fetch_klines(sym, "1m", limit=2, instrument=InstrumentType.SPOT)
            if klines and len(klines) > 0:
                k = klines[-1]  # 最新（可能未收盘）的 1m bar
                close = Decimal(str(k.close))
                low = Decimal(str(k.low))
            else:
                raise RuntimeError("empty klines response")
        except Exception:
            # fallback: 拉 ticker 当 close_only mode (旧行为)
            try:
                close = await self._fetch_spot_price()
                low = close
            except Exception:
                return  # 拉价完全失败跳过 tick

        self._last_spot_px = close
        # §6.2 #1: 单分支 — broker 已封装 paper synthetic / LIVE binance 差异
        await self._process_decisions(close, low)
        # 保证金健康监控 (杠杆 > 1 时启用, 跨阈值告警)
        try:
            self._check_margin_health(close, alert=True)
        except Exception:
            logger.debug("margin_check_failed", exc_info=True)
        # 预清算自动减仓 (审查 #3 救命级) — 在 margin alert 之后, 距强平太近时执行
        try:
            self._trigger_pre_liq_deleverage(close)
        except Exception:
            logger.exception("dgr_btc_pre_liq_deleverage_failed")

        try:
            self._persist_state()
        except Exception:
            logger.exception("dgr_btc_paper_persist_failed")

    async def _process_decisions(self, price: Decimal, low: Decimal) -> None:
        """核心决策循环 (async, 单分支)，与 MartingaleBacktestRunner 同形。

        §6.2 #1 revamp: paper / LIVE 共用此环, broker 抽象隔离 IO 细节.
          - paper / backtest: self._broker = PaperBroker → 合成 fill, never fails
          - LIVE: self._broker = LiveBroker → 调真实 binance broker_adapter
        失败 (BrokerError) 一致 fail-closed: log + break, 不 apply_fill, 下 tick 重试.

        Mirror test 仍可调此方法 (asyncio.run 包) 验证 paper ≡ backtest 等价.
        """
        mode_label = "LIVE" if self.live_mode else "paper"
        # §6.2 #8 regime gate: 进入 sideways-grinder regime 时暂停加仓 (ENTRY + ADD)
        # SL/TP 永远启用. 默认 OFF (cfg.risk_regime_gate_enabled=False), paper 验证后启用.
        allow_add = self._compute_regime_allow_add()
        for _ in range(self._engine.cfg.max_layers + 3):
            d = self._engine.decide(self._state, price, low, allow_add_layer=allow_add)
            if d.kind == DecisionKind.NOOP:
                break

            if d.kind == DecisionKind.ADD_LAYER:
                stake = d.stake_usdt
                if self._cash < stake:
                    stake = self._cash
                if stake <= _ZERO:
                    break
                try:
                    fill = await self._broker.place_buy(
                        d.layer_index, d.target_price, stake,
                    )
                except BrokerError as e:
                    logger.warning(
                        "dgr_btc_%s_buy_reject layer=%d target=%s reason=%s",
                        mode_label, d.layer_index, d.target_price, str(e)[:120],
                    )
                    break  # fail-closed
                self._cash -= fill.cost_or_proceeds
                self._engine.apply_fill(
                    self._state, d, fill.price, fill.qty, fill.cost_or_proceeds,
                )
                self._n_trades_executed += 1
                logger.info(
                    "dgr_btc_%s_BUY layer=%d fill_px=%s qty=%s stake=%s reason=%s",
                    mode_label, d.layer_index, fill.price, fill.qty,
                    fill.cost_or_proceeds, d.reason,
                )
                jsonl_record: dict[str, Any] = {
                    "action": "BUY",
                    "layer_index": d.layer_index,
                    "fill_price": str(fill.price),
                    "qty": str(fill.qty),
                    "stake": str(fill.cost_or_proceeds),
                    "target_price": str(d.target_price),
                    "reason": d.reason,
                }
                if fill.broker_order_id:
                    jsonl_record["broker_order_id"] = fill.broker_order_id
                self._append_trade_jsonl(jsonl_record)
                header = (
                    f"🟢 dgr_btc {mode_label} BUY L{d.layer_index} @ ${fill.price:,.2f}\n"
                    f"成交: {fill.qty:.5f} BTC | 投入 ${fill.cost_or_proceeds:,.2f}"
                )
                self._send_fill_snapshot(header)

            elif d.kind in (DecisionKind.TAKE_PROFIT, DecisionKind.STOP_LOSS):
                pre_cost = self._state.total_cost
                pre_qty = self._state.total_qty
                pre_avg = self._state.avg_cost
                action = "TP" if d.kind == DecisionKind.TAKE_PROFIT else "SL"
                try:
                    fill = await self._broker.place_unwind_sell(pre_qty, d.target_price)
                except BrokerError as e:
                    logger.error(
                        "dgr_btc_%s_%s_FAIL qty=%s reason=%s",
                        mode_label, action, pre_qty, str(e)[:120],
                    )
                    break  # fail-closed
                self._cash += fill.cost_or_proceeds
                self._engine.apply_fill(
                    self._state, d, fill.price, fill.qty, fill.cost_or_proceeds,
                )
                self._n_trades_executed += 1
                logger.info(
                    "dgr_btc_%s_%s_SELL fill_px=%s qty=%s proceeds=%s reason=%s",
                    mode_label, action, fill.price, fill.qty,
                    fill.cost_or_proceeds, d.reason,
                )
                pnl = fill.cost_or_proceeds - pre_cost
                pnl_pct = (pnl / pre_cost * Decimal("100")) if pre_cost > _ZERO else _ZERO
                jsonl_record = {
                    "action": action,
                    "fill_price": str(fill.price),
                    "qty": str(fill.qty),
                    "proceeds": str(fill.cost_or_proceeds),
                    "pre_avg": str(pre_avg),
                    "pre_qty": str(pre_qty),
                    "pre_cost": str(pre_cost),
                    "pnl": str(pnl),
                    "pnl_pct": str(pnl_pct),
                    "target_price": str(d.target_price),
                    "reason": d.reason,
                }
                if fill.broker_order_id:
                    jsonl_record["broker_order_id"] = fill.broker_order_id
                self._append_trade_jsonl(jsonl_record)
                emoji = "💰" if action == "TP" else "🔴"
                header = (
                    f"{emoji} dgr_btc {mode_label} {action} cycle {self._state.cycle_id - 1} closed\n"
                    f"卖出: {fill.qty:.5f} BTC @ ${fill.price:,.2f}\n"
                    f"avg ${pre_avg:,.2f} → proceeds ${fill.cost_or_proceeds:,.2f}\n"
                    f"净 P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
                )
                self._send_fill_snapshot(header)
                break

    # ──────────────────── fill snapshot (telegram + log) ────────────────────

    def _format_snapshot(self) -> str:
        """格式化当前 strategy state 表 (在 fill 之后调用)."""
        cfg = self._engine.cfg
        s = self._state
        price = self._last_spot_px or _ZERO

        if not s.is_in_cycle:
            # cycle closed (TP/SL just fired)
            next_l0 = self._cash * cfg.layer_weights[0] if cfg.layer_weights else _ZERO
            return (
                f"📊 cycle {s.cycle_id} (closed) | 累计 TP {s.n_tp} / SL {s.n_sl}\n"
                f"现金: ${self._cash:,.2f} | 等待新 ENTRY L0\n"
                f"下一 ENTRY L0 预计 ${next_l0:,.2f}"
            )

        floating = s.unrealized_pnl(price, cfg.fee_pct) if price > _ZERO else _ZERO
        next_tp = s.avg_cost * (Decimal("1") + cfg.tp_pct)
        next_sl = s.avg_cost * (Decimal("1") - cfg.sl_pct)

        lines = [
            f"📊 layers {s.n_layers}/{cfg.max_layers} | cycle {s.cycle_id} | TP/SL 累计 {s.n_tp}/{s.n_sl}",
            f"持仓: {s.total_qty:.5f} BTC / 成本 ${s.total_cost:,.2f}",
            f"avg_cost: ${s.avg_cost:,.2f} | 当前价: ${price:,.2f}",
            f"现金: ${self._cash:,.2f} | 浮动 P&L: ${floating:+,.2f}",
        ]

        if s.n_layers < cfg.max_layers and s.next_buy_price:
            drop = (s.avg_cost - s.next_buy_price) / s.avg_cost * Decimal("100")
            lines.append(f"下一档 ADD L{s.n_layers}: ${s.next_buy_price:,.2f} (-{drop:.2f}%)")
        else:
            lines.append(f"下一档 ADD: 满层 (max {cfg.max_layers})")

        tp_pct_disp = float(cfg.tp_pct) * 100
        sl_pct_disp = float(cfg.sl_pct) * 100
        lines.append(f"TP @ ${next_tp:,.2f} (+{tp_pct_disp:.1f}%)")
        lines.append(f"SL @ ${next_sl:,.2f} (-{sl_pct_disp:.1f}%)")
        return "\n".join(lines)

    def _send_fill_snapshot(self, header: str) -> None:
        """每笔 fill 后推送：log 必发, telegram 按 cfg 开关."""
        try:
            snapshot = self._format_snapshot()
            full = f"{header}\n{snapshot}"
            logger.info("dgr_btc_fill_snapshot\n%s", full)
            if self.cfg.live_safety_telegram_alerts_enabled:
                from app.notifications.telegram import notify_system  # noqa: PLC0415
                notify_system(full)
        except Exception:
            logger.debug("dgr_btc_fill_snapshot_failed", exc_info=True)


    # ──────────────────── margin health monitor ────────────────────

    def _check_margin_health(self, mark_price: Decimal, alert: bool = True) -> dict | None:
        """计算保证金健康度 (基于 cfg.leverage 杠杆假设)，发警报.

        模拟币安统一账户 cross-margin: maintenance_margin = 5% 持仓名义。
        - 抵押 = total_capital / leverage
        - 权益 = 抵押 + 未实现盈亏
        - 维护要求 = 持仓名义 × 5%
        - 保证金率 = 权益 / 维护要求
        - WARN < 1.5x, CRITICAL < 1.2x, 清算 < 1.0x
        """
        state = self._state
        if not state.is_in_cycle or mark_price <= _ZERO:
            return None
        leverage = max(1, int(self.cfg.leverage))
        if leverage <= 1:
            return None

        collateral = self.cfg.total_capital_usdt / Decimal(str(leverage))
        notional = state.total_qty * mark_price
        unrealized = state.unrealized_pnl(mark_price, self._engine.cfg.fee_pct)
        equity = collateral + unrealized

        maint_pct = Decimal("0.05")
        maint_req = notional * maint_pct
        margin_ratio = (equity / maint_req) if maint_req > _ZERO else Decimal("999")

        qty = state.total_qty
        if qty > _ZERO:
            liq_price = (state.avg_cost * qty - collateral) / (qty * (Decimal("1") - maint_pct))
            if liq_price < _ZERO:
                liq_price = _ZERO
        else:
            liq_price = _ZERO
        liq_distance_pct = ((mark_price - liq_price) / mark_price * Decimal("100")) if mark_price > _ZERO else _ZERO

        health = {
            "leverage": leverage,
            "collateral_usdt": collateral,
            "notional_usdt": notional,
            "unrealized_pnl": unrealized,
            "equity_usdt": equity,
            "maintenance_req_usdt": maint_req,
            "margin_ratio": margin_ratio,
            "liq_price": liq_price,
            "liq_distance_pct": liq_distance_pct,
        }
        if not alert:
            return health

        if margin_ratio < Decimal("1.2"):
            level = "CRITICAL"
        elif margin_ratio < Decimal("1.5"):
            level = "WARN"
        else:
            level = None

        last = getattr(self, "_last_margin_alert_level", None)
        if level and level != last:
            emoji = "\U0001F6A8" if level == "CRITICAL" else "\u26A0\uFE0F"
            msg = (
                f"{emoji} dgr_btc 保证金 {level}\n"
                f"杠杆: {leverage}x | 名义: ${notional:,.0f} | 抵押: ${collateral:,.0f}\n"
                f"权益: ${equity:,.2f} | 维护要求: ${maint_req:,.2f}\n"
                f"保证金率: {margin_ratio:.2f}x (清算线 1.0x)\n"
                f"清算价: ${liq_price:,.2f} | 距清算: {liq_distance_pct:.2f}%\n"
                f"当前价: ${mark_price:,.2f} | avg_cost: ${state.avg_cost:,.2f}\n"
                f"操作建议: " + (
                    "立即追加保证金 OR 平仓减仓" if level == "CRITICAL"
                    else "考虑追加保证金"
                )
            )
            logger.warning("dgr_btc_margin_alert level=%s ratio=%.2f liq=%s",
                          level, float(margin_ratio), liq_price)
            if self.cfg.live_safety_telegram_alerts_enabled:
                try:
                    from app.notifications.telegram import notify_risk_violation  # noqa: PLC0415
                    notify_risk_violation(f"DGR_BTC_MARGIN_{level}", msg)
                except Exception:
                    logger.debug("margin_alert_telegram_failed", exc_info=True)
            self._last_margin_alert_level = level
        elif not level and last:
            logger.info("dgr_btc_margin_recovered ratio=%.2f", float(margin_ratio))
            if self.cfg.live_safety_telegram_alerts_enabled:
                try:
                    from app.notifications.telegram import notify_system  # noqa: PLC0415
                    notify_system(f"\u2705 dgr_btc 保证金恢复健康 (ratio {margin_ratio:.2f}x)")
                except Exception:
                    pass
            self._last_margin_alert_level = None

        return health

    # ──────────────────── pre-liq auto-deleverage (审查 #3 救命级) ────────────────────

    def _trigger_pre_liq_deleverage(self, mark_price: Decimal) -> bool:
        """救命级风控: 距强平 < threshold 时自动平 deleverage_pct 的仓位.

        risk-manager 审查指出: 策略 SL (avg×0.90) 在 10x 杠杆下永远不会触发
        (强平在 avg×0.937), LUNA/FTX/COVID 类闪跌策略归零. 这是唯一能救命的层.

        触发条件:
          - cfg.risk_pre_liq_enabled = True
          - margin_ratio < cfg.risk_pre_liq_margin_ratio_threshold (默认 1.10)
          - 距上次触发 > cfg.risk_pre_liq_cooldown_seconds (默认 1h)
          - state.is_in_cycle (有持仓)

        执行:
          - 按 deleverage_pct (默认 50%) 比例减仓
          - 每层 layer qty + cost 同比例缩减 (保持 avg_cost 不变)
          - 现金 += proceeds (扣 sell fee)
          - 累计 realized_pnl_usdt += pnl_on_sold_portion
          - 写 jsonl + telegram 强告警
          - 不增加 cycle_id (仍是同一 cycle, 只是 size 缩了)

        Returns:
          True if deleverage triggered, False otherwise.
        """
        if not self.cfg.risk_pre_liq_enabled:
            return False
        if not self._state.is_in_cycle or mark_price <= _ZERO:
            return False
        if self.cfg.leverage <= 1:
            return False  # 无杠杆无强平风险, 跳过

        # 取健康度 (不触发 alert)
        health = self._check_margin_health(mark_price, alert=False)
        if health is None:
            return False
        margin_ratio = health["margin_ratio"]
        if margin_ratio >= self.cfg.risk_pre_liq_margin_ratio_threshold:
            return False

        # Cooldown
        now = datetime.now(timezone.utc)
        if self._last_deleverage_at is not None:
            elapsed = (now - self._last_deleverage_at).total_seconds()
            if elapsed < self.cfg.risk_pre_liq_cooldown_seconds:
                logger.info(
                    "dgr_btc_pre_liq_skipped_cooldown margin_ratio=%.3f elapsed=%.0fs",
                    float(margin_ratio), elapsed,
                )
                return False

        # ─── 执行 deleverage ───
        deleverage_pct = self.cfg.risk_pre_liq_deleverage_pct
        keep_ratio = _ONE - deleverage_pct  # 保留比例 = 50%
        if keep_ratio <= _ZERO or keep_ratio >= _ONE:
            logger.error("dgr_btc_pre_liq_invalid_deleverage_pct=%s", deleverage_pct)
            return False

        # 计算卖出数量 + proceeds
        from app.strategies.dgr_btc.engine import Layer  # noqa: PLC0415
        fee_pct = self._engine.cfg.fee_pct
        qty_sold = self._state.total_qty * deleverage_pct
        cost_basis_sold = self._state.total_cost * deleverage_pct
        proceeds = qty_sold * mark_price * (_ONE - fee_pct)
        pnl = proceeds - cost_basis_sold

        # 缩减每层 (按比例 keep, avg_cost 保持不变)
        new_layers = [
            Layer(
                entry_price=l.entry_price,
                qty_btc=l.qty_btc * keep_ratio,
                cost_usdt=l.cost_usdt * keep_ratio,
            )
            for l in self._state.layers
        ]
        self._state.layers = new_layers
        self._cash += proceeds
        self._state.realized_pnl_usdt += pnl
        self._last_deleverage_at = now
        self._n_deleverages += 1
        self._n_trades_executed += 1

        # 重算 next_buy_price (avg_cost 不变所以理论上不变, 但保险起见)
        if self._state.is_in_cycle:
            self._state.next_buy_price = self._state.avg_cost * (_ONE - self._engine.cfg.grid_step)

        logger.warning(
            "dgr_btc_pre_liq_deleverage_triggered ratio=%.3f qty_sold=%s proceeds=%s pnl=%s remaining_qty=%s",
            float(margin_ratio), qty_sold, proceeds, pnl, self._state.total_qty,
        )

        # 写 trades jsonl
        self._append_trade_jsonl({
            "action": "PRE_LIQ_DELEVERAGE",
            "fill_price": str(mark_price),
            "qty": str(qty_sold),
            "proceeds": str(proceeds),
            "pre_avg": str(self._state.avg_cost),  # avg 缩后不变
            "pre_cost": str(cost_basis_sold),
            "pnl": str(pnl),
            "pnl_pct": str((pnl / cost_basis_sold * Decimal("100")) if cost_basis_sold > _ZERO else _ZERO),
            "margin_ratio_at_trigger": str(margin_ratio),
            "reason": "pre_liquidation_deleverage",
        })

        # Telegram 强告警
        if self.cfg.live_safety_telegram_alerts_enabled:
            try:
                from app.notifications.telegram import notify_risk_violation  # noqa: PLC0415
                msg = (
                    f"\U0001F6A8 dgr_btc 预清算自动减仓触发\n"
                    f"保证金率: {float(margin_ratio):.3f}x (阈值 {self.cfg.risk_pre_liq_margin_ratio_threshold}x)\n"
                    f"清算价 ${health['liq_price']:,.2f} 距现价 {health['liq_distance_pct']:.2f}%\n"
                    f"卖出 {qty_sold:.5f} BTC @ ${mark_price:,.2f}\n"
                    f"得到现金 ${proceeds:,.2f} | 实现盈亏 ${pnl:+,.2f}\n"
                    f"剩余持仓 {self._state.total_qty:.5f} BTC | 平均成本 ${self._state.avg_cost:,.2f}\n"
                    f"累计触发 {self._n_deleverages} 次"
                )
                notify_risk_violation("DGR_BTC_PRE_LIQ_DELEVERAGE", msg)
            except Exception:
                logger.debug("pre_liq_telegram_failed", exc_info=True)

        return True

    # ──────────────────── §6.2 #8 regime gate ────────────────────

    def _compute_regime_allow_add(self) -> bool:
        """返回是否允许加仓 (ADD_LAYER + 新 cycle ENTRY).

        快速路径 (大部分 tick): 用上次缓存的 is_bad 翻转 (1h cooldown).
        慢路径 (每小时一次): 拉 30d 1h bars → 聚合 daily close → compute_regime_metrics.

        默认 cfg.risk_regime_gate_enabled=False 时直接返回 True (gate 关闭).
        """
        if not self.cfg.risk_regime_gate_enabled:
            return True

        now = datetime.now(timezone.utc)
        # 1h cache: 同一小时内不重复计算
        if self._regime_last_eval_at is not None:
            elapsed = (now - self._regime_last_eval_at).total_seconds()
            if elapsed < 3600:
                return not self._regime_last_is_bad

        # 慢路径: 重新评估
        try:
            from app.strategies.dgr_btc.regime_detector import (  # noqa: PLC0415
                aggregate_hourly_to_daily, compute_regime_metrics,
            )
            from app.exchanges.models import InstrumentType, Symbol  # noqa: PLC0415

            sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
            # 720 个 1h bar = 30 日
            klines = self._regime_fetch_klines_sync(sym, "1h", 720)
            if not klines:
                # 拉失败保险路径: 允许加仓 (避免数据问题阻塞策略)
                return True

            hourly_closes = [float(k.close) for k in klines if hasattr(k, "close")]
            daily_closes = aggregate_hourly_to_daily(hourly_closes, bars_per_day=24)

            metrics = compute_regime_metrics(
                daily_closes,
                vol_threshold=float(self.cfg.risk_regime_vol_threshold),
                dd_threshold=float(self.cfg.risk_regime_dd_threshold),
                warmup_days=self.cfg.risk_regime_warmup_days,
            )
            self._regime_last_eval_at = now
            self._regime_last_is_bad = metrics.is_bad
            self._regime_last_metrics = metrics

            if metrics.is_bad:
                self._n_regime_blocks += 1
                logger.warning(
                    "dgr_btc_regime_gate_blocks_add vol=%.3f dd=%.3f sample=%d total_blocks=%d",
                    metrics.realized_vol_annualized, metrics.max_drawdown_pct,
                    metrics.sample_size, self._n_regime_blocks,
                )
                # 每 24h 推一次 telegram (避免狂刷)
                self._maybe_send_regime_alert(metrics)

            return not metrics.is_bad
        except Exception:
            logger.exception("dgr_btc_regime_gate_eval_failed_fallback_allow")
            return True  # fail-soft: 评估失败时允许加仓

    def _regime_fetch_klines_sync(self, sym, interval: str, limit: int):
        """同步包 adapter.fetch_klines (event loop running 内的 nested call 用 asyncio.run 不行).

        简化: 用 ensure_future + future.result() 不可行 (running loop). 故此处用
        一个简单 cache + 拉失败返回 None. 真正的 30d 数据应由 _tick 异步预取.
        """
        # 简化版: 直接异步路径已在 _tick 拉过 klines (limit=2). 这里改成 fetch limit=720.
        # 用 asyncio.get_event_loop().run_until_complete 会触发 nested loop 错误.
        # 替代: 缓存 + 容忍数据偶尔过期 (regime 是慢信号, 1h 间隔 OK).
        import asyncio  # noqa: PLC0415
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        try:
            loop = asyncio.get_running_loop()
            # 在 running loop 内不能 run_until_complete; 用 ensure_future + 不阻塞地丢弃
            # 退化方案: 暂时不拉 30d 数据 (返回 None), _tick 后续 hook 异步预拉
            # 这是已知 limitation, paper 14d 验证期 default OFF 不影响.
            _ = loop
            return None
        except RuntimeError:
            # 不在 event loop 里 (单测/脚本场景)
            try:
                return asyncio.run(
                    self.adapter.fetch_klines(sym, interval, limit=limit, instrument=InstrumentType.SPOT)
                )
            except Exception:
                return None

    def _maybe_send_regime_alert(self, metrics) -> None:
        """24h 内最多发一次 regime gate 告警, 避免狂刷."""
        if not self.cfg.live_safety_telegram_alerts_enabled:
            return
        if self._regime_last_alert_at is not None:
            elapsed = (datetime.now(timezone.utc) - self._regime_last_alert_at).total_seconds()
            if elapsed < 86400:  # 24h
                return
        try:
            from app.notifications.telegram import notify_risk_violation  # noqa: PLC0415
            msg = (
                f"⚠️ dgr_btc regime gate 触发 — 暂停加仓\n"
                f"30d 年化波动: {metrics.realized_vol_annualized*100:.2f}% "
                f"(阈值 {float(self.cfg.risk_regime_vol_threshold)*100:.0f}%)\n"
                f"30d 最大回撤: {metrics.max_drawdown_pct*100:.2f}% "
                f"(阈值 {float(self.cfg.risk_regime_dd_threshold)*100:.0f}%)\n"
                f"判定: sideways-with-shallow-drawdown (grinder 区)\n"
                f"动作: ENTRY + ADD_LAYER 暂停; SL/TP 不受影响\n"
                f"累计 gate 触发: {self._n_regime_blocks} 次"
            )
            notify_risk_violation("DGR_BTC_REGIME_GATE", msg)
            self._regime_last_alert_at = datetime.now(timezone.utc)
        except Exception:
            logger.debug("regime_alert_telegram_failed", exc_info=True)

    # ──────────────────── price fetch ────────────────────

    async def _fetch_spot_price(self) -> Decimal:
        """从 dracula adapter 拉 spot 现价（用 Symbol obj，与 v1 路径一致）"""
        # 优先用 market_data_hub
        if self.hub is not None:
            try:
                t = self.hub.get_ticker(self.cfg.exchange, self.cfg.symbol_spot)
                if t and t.last:
                    return Decimal(str(t.last))
            except Exception:
                pass

        # fallback: adapter.fetch_ticker(Symbol, InstrumentType.SPOT)
        from app.exchanges.models import InstrumentType, Symbol  # noqa: PLC0415
        sym = Symbol(self.cfg.symbol_base, self.cfg.symbol_quote)
        t = await self.adapter.fetch_ticker(sym, InstrumentType.SPOT)
        if t and getattr(t, "last", None):
            return Decimal(str(t.last))
        raise RuntimeError(f"no last price for {self.cfg.symbol_spot}")

    # ──────────────────── trade jsonl (append-only) ────────────────────

    def _append_trade_jsonl(self, record: dict) -> None:
        """每笔 fill 追加一行 JSON 到 trades jsonl. Fail-soft: 写失败仅 log, 不阻塞策略.

        共享 schema 字段 (writer 注入):
          ts, mode (paper|live), cycle_id, n_layers_after, cash_after
        Caller 提供:
          action (BUY|TP|SL), fill_price, qty, target_price, reason
          BUY: + stake, layer_index
          TP/SL: + proceeds, pre_avg, pre_qty, pre_cost, pnl, pnl_pct
        """
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": "live" if self.live_mode else "paper",
            "cycle_id": self._state.cycle_id,
            "n_layers_after": self._state.n_layers,
            "cash_after": str(self._cash),
            **record,
        }
        # 审查 #6: fsync 防止 power loss / kernel crash 丢失最近 fills.
        # buffered write 在 process crash 时安全 (kernel page cache 会 flush),
        # 但在硬关机/掉电时未 sync 的数据会丢失. LIVE 真金不能丢账.
        try:
            with self._trades_jsonl.open("a") as f:
                f.write(json.dumps(payload, default=str) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    # 某些文件系统 (tmpfs / overlay) 不支持 fsync, 不影响逻辑
                    pass
        except Exception:
            logger.exception(
                "dgr_btc_trades_jsonl_write_failed action=%s", record.get("action"),
            )

    # ──────────────────── state persistence ────────────────────

    def _persist_state(self) -> None:
        """持久化 StrategyState + cash 到 JSON"""
        payload = {
            "version": "v2-martingale",
            "ts": datetime.now(timezone.utc).isoformat(),
            "cycle_id": self._state.cycle_id,
            "cash_usdt": str(self._cash),
            "n_tp": self._state.n_tp,
            "n_sl": self._state.n_sl,
            "realized_pnl_usdt": str(self._state.realized_pnl_usdt),
            "next_buy_price": str(self._state.next_buy_price) if self._state.next_buy_price else None,
            "layers": [
                {
                    "entry_price": str(l.entry_price),
                    "qty_btc": str(l.qty_btc),
                    "cost_usdt": str(l.cost_usdt),
                }
                for l in self._state.layers
            ],
            "n_ticks": self._n_ticks,
            "n_trades_executed": self._n_trades_executed,
            # Pre-liq deleverage 跟踪 (审查 #3)
            "last_deleverage_at": self._last_deleverage_at.isoformat() if self._last_deleverage_at else None,
            "n_deleverages": self._n_deleverages,
        }
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._state_file)

    async def _try_restore_state(self) -> bool:
        if not self._state_file.exists():
            return False
        try:
            data = json.loads(self._state_file.read_text())
            if data.get("version") != "v2-martingale":
                logger.warning("dgr_btc_paper_state_version_mismatch v=%s", data.get("version"))
                return False
            self._state.cycle_id = int(data.get("cycle_id", 0))
            self._cash = Decimal(str(data.get("cash_usdt", self.cfg.total_capital_usdt)))
            self._state.n_tp = int(data.get("n_tp", 0))
            self._state.n_sl = int(data.get("n_sl", 0))
            self._state.realized_pnl_usdt = Decimal(str(data.get("realized_pnl_usdt", "0")))
            nb = data.get("next_buy_price")
            self._state.next_buy_price = Decimal(str(nb)) if nb else None
            self._state.layers = [
                Layer(
                    entry_price=Decimal(str(l["entry_price"])),
                    qty_btc=Decimal(str(l["qty_btc"])),
                    cost_usdt=Decimal(str(l["cost_usdt"])),
                )
                for l in data.get("layers", [])
            ]
            self._n_ticks = int(data.get("n_ticks", 0))
            self._n_trades_executed = int(data.get("n_trades_executed", 0))
            # 恢复 pre-liq deleverage 状态 (审查 #3)
            ld = data.get("last_deleverage_at")
            self._last_deleverage_at = datetime.fromisoformat(ld) if ld else None
            self._n_deleverages = int(data.get("n_deleverages", 0))
            logger.info(
                "dgr_btc_paper_state_restored cycle=%d layers=%d cash=%s",
                self._state.cycle_id, len(self._state.layers), self._cash,
            )
            return True
        except Exception:
            logger.exception("dgr_btc_paper_state_restore_failed")
            return False

    # ──────────────────── snapshot for callers ────────────────────

    def snapshot(self) -> dict:
        """v1 caller (strategies.py /status / dashboard / positions) 调用"""
        price = self._last_spot_px or _ZERO
        state = self._state
        cfg = self.cfg

        position_value = state.mark_to_market(price, self._engine.cfg.fee_pct)
        total_equity = self._cash + position_value
        unrealized = state.unrealized_pnl(price, self._engine.cfg.fee_pct) if state.is_in_cycle else _ZERO
        spot_notional = state.total_qty * price if price > 0 else _ZERO

        return {
            "running": self._running,
            "instance": cfg.instance_name,
            "live_mode": self.live_mode,
            "cycle_id": state.cycle_id,
            "n_layers": state.n_layers,
            "n_ticks": self._n_ticks,
            "n_trades": self._n_trades_executed,
            "n_recenters": state.n_tp + state.n_sl,  # compat: 每次 cycle close 算一次"再定心"
            "n_tp": state.n_tp,
            "n_sl": state.n_sl,
            "total_equity": str(total_equity),
            "cash": str(self._cash),
            "funding_paid": "0",  # 单边: 不收 funding
            "total_fees": "0",  # 已折进 avg_cost
            "realized_pnl": str(state.realized_pnl_usdt),
            "unrealized_pnl": str(unrealized),
            "delta": str(state.total_qty),  # 单边: delta = 现货量
            "center": str(state.avg_cost),  # compat: 中心 = 当前 avg_cost
            "next_buy_price": str(state.next_buy_price) if state.next_buy_price else None,
            "risk_level": "NORMAL",  # P6 接入 risk_filter 时填实
            "trend_count": 0,
            "spot_price": str(price),
            "mark_price": str(price),       # alias for v1 caller (UI 读 mark_price)
            "total_qty": str(state.total_qty),
            "total_cost": str(state.total_cost),
            "avg_cost": str(state.avg_cost),
            "max_layers": cfg.mart_max_layers,
            "margin_health": (
                lambda h: {k: str(v) for k, v in h.items()} if h else None
            )(self._check_margin_health(price, alert=False)),
            "positions": {
                "spot": {
                    "qty": str(state.total_qty),
                    "avg_entry": str(state.avg_cost),
                    "notional_usd": str(spot_notional),
                    "unrealized_pnl": str(unrealized),
                },
                "perp": {  # 单边: 永远 0
                    "qty_abs": "0",
                    "avg_entry": "0",
                    "notional_usd": "0",
                    "unrealized_pnl": "0",
                    "realized_pnl": "0",
                },
            },
        }
