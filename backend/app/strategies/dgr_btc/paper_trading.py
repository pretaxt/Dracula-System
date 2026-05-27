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

        # 持久化路径
        state_dir = Path(os.environ.get("DGR_BTC_STATE_DIR", "/app/state"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self._state_file = state_dir / (
            "dgr_btc_state.json" if live_mode else "dgr_btc_paper_state.json"
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
        """启动: restore state + 拉初始价"""
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

        self._running = True

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
        self._process_decisions(close, low)

        try:
            self._persist_state()
        except Exception:
            logger.exception("dgr_btc_paper_persist_failed")

    def _process_decisions(self, price: Decimal, low: Decimal) -> None:
        """核心决策循环 (sync, 不含 IO)，与 MartingaleBacktestRunner 同形。

        被 _tick() 调用 (LIVE/paper, low=price 简化)
        也可被 mirror test 调用 (传 bar 真实 low) 验证 LIVE/backtest 等价。
        """
        for _ in range(self._engine.cfg.max_layers + 3):
            d = self._engine.decide(self._state, price, low)
            if d.kind == DecisionKind.NOOP:
                break

            if d.kind == DecisionKind.ADD_LAYER:
                stake = d.stake_usdt
                if self._cash < stake:
                    stake = self._cash
                if stake <= _ZERO:
                    break
                fill_px, qty = self._engine.compute_fill_qty_buy(stake, d.target_price)
                self._cash -= stake
                self._engine.apply_fill(self._state, d, fill_px, qty, stake)
                self._n_trades_executed += 1
                logger.info(
                    "dgr_btc_paper_BUY layer=%d price=%s qty=%s stake=%s reason=%s",
                    d.layer_index, fill_px, qty, stake, d.reason,
                )
                # 推送 fill 表 (telegram + log)
                header = (
                    f"🟢 dgr_btc BUY L{d.layer_index} @ ${fill_px:,.2f}\n"
                    f"成交: {qty:.5f} BTC | 投入 ${stake:,.2f}"
                )
                self._send_fill_snapshot(header)

            elif d.kind in (DecisionKind.TAKE_PROFIT, DecisionKind.STOP_LOSS):
                # capture pre-fill state for P&L calc
                pre_cost = self._state.total_cost
                pre_qty = self._state.total_qty
                pre_avg = self._state.avg_cost
                proceeds = self._engine.compute_proceeds_sell(self._state.total_qty, d.target_price)
                qty = self._state.total_qty
                self._cash += proceeds
                self._engine.apply_fill(self._state, d, d.target_price, qty, proceeds)
                self._n_trades_executed += 1
                action = "TP" if d.kind == DecisionKind.TAKE_PROFIT else "SL"
                logger.info(
                    "dgr_btc_paper_%s_SELL qty=%s proceeds=%s reason=%s",
                    action, qty, proceeds, d.reason,
                )
                # 推送 cycle close 表
                pnl = proceeds - pre_cost
                pnl_pct = (pnl / pre_cost * Decimal("100")) if pre_cost > _ZERO else _ZERO
                emoji = "💰" if action == "TP" else "🔴"
                header = (
                    f"{emoji} dgr_btc {action} cycle {self._state.cycle_id - 1} closed\n"
                    f"卖出: {pre_qty:.5f} BTC @ ${d.target_price:,.2f}\n"
                    f"avg ${pre_avg:,.2f} → 毛收入 ${proceeds:,.2f}\n"
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
