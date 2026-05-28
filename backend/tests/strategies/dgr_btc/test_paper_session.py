"""P4 验收: 新 paper_trading.py 单腿马丁 session

不测 LIVE 链路 / broker_adapter / inflight_manager (paper 模式不需要)。
重点测:
  1. caller contract 保持稳定（v1 API 兼容）
  2. tick loop 能驱动 engine
  3. state 持久化 round-trip
"""
import asyncio
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.paper_trading import (
    DgrBtcPaperSession,
    _MartingaleStrategyView,
    _PaperInflightStub,
    _PositionView,
)


@pytest.fixture
def tmpdir_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DGR_BTC_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def cfg():
    """Default config with Martingale fields populated"""
    return DgrBtcStrategyConfig()


@pytest.fixture
def mock_adapter():
    """Mock binance adapter that returns fixed price"""
    adapter = MagicMock()
    adapter.fetch_ticker = AsyncMock(return_value={"last": "75000.0"})
    return adapter


@pytest.fixture
def session(cfg, mock_adapter, tmpdir_state):
    return DgrBtcPaperSession(
        cfg=cfg,
        adapter=mock_adapter,
        tick_interval_seconds=0.01,  # fast tick
        live_mode=False,
    )


# ─── A. Caller contract: 关键属性可访问 ───


def test_caller_contract_session_attributes(session):
    """v1 caller 读这些属性必须存在"""
    assert hasattr(session, "cfg")
    assert hasattr(session, "strategy")
    assert hasattr(session, "inflight_manager")
    assert hasattr(session, "live_mode")
    assert hasattr(session, "_running")
    assert hasattr(session, "_last_spot_px")
    assert hasattr(session, "_last_perp_px")
    assert session._last_perp_px is None  # 单边: 恒 None


def test_caller_contract_strategy_view(session):
    """sess.strategy.* 必须可读 (telegram /positions 直读)"""
    s = session.strategy
    assert isinstance(s.spot_pos, _PositionView)
    assert isinstance(s.perp_pos, _PositionView)
    # 单边: perp 永远 0
    assert s.perp_pos.quantity == Decimal("0")
    # 初始: spot 也是 0 (尚未 tick)
    assert s.spot_pos.quantity == Decimal("0")
    assert s.center == Decimal("0")


def test_caller_contract_snapshot_keys(session):
    """snapshot() 返回的 dict 必须含 dashboard / positions 用的 keys"""
    snap = session.snapshot()
    required = {
        "running", "instance", "live_mode", "cycle_id", "n_layers",
        "n_tp", "n_sl", "n_recenters", "total_equity", "cash",
        "funding_paid", "total_fees", "realized_pnl", "unrealized_pnl",
        "delta", "center", "spot_price",
        "positions",
    }
    assert required.issubset(snap.keys())
    pos = snap["positions"]
    assert "spot" in pos and "perp" in pos
    assert {"qty", "avg_entry", "notional_usd", "unrealized_pnl"}.issubset(pos["spot"].keys())
    # 单边: perp 全 0
    assert pos["perp"]["qty_abs"] == "0"


def test_caller_contract_inflight_stub(session):
    assert session.inflight_manager.total_inflight() == 0


# ─── B. Tick driving engine ───


@pytest.mark.asyncio
async def test_start_fetches_initial_price(session, mock_adapter):
    await session.start()
    assert session._running is True
    assert session._last_spot_px == Decimal("75000.0")
    assert session.snapshot()["spot_price"] == "75000.0"


@pytest.mark.asyncio
async def test_first_tick_enters_layer_0(session, mock_adapter):
    """第一个 tick: 空仓 → engine 触发 ENTRY layer 0"""
    await session.start()
    # session._cash = initial; price = 75000
    initial_cash = session._cash
    await session._tick()

    # layer 0 已 fill
    assert session._state.n_layers == 1
    # cash 减少 (stake = initial × weights[0] = 760)
    assert session._cash < initial_cash
    expected_stake = session.cfg.total_capital_usdt * session.cfg.mart_layer_weights[0]
    # 容差: stake 应该是 760 (cash 减少 760)
    assert abs((initial_cash - session._cash) - expected_stake) < Decimal("0.01")


@pytest.mark.asyncio
async def test_snapshot_after_layer_0_shows_position(session, mock_adapter):
    await session.start()
    await session._tick()
    snap = session.snapshot()

    assert snap["n_layers"] == 1
    assert Decimal(snap["positions"]["spot"]["qty"]) > 0
    # delta = spot qty (单边)
    assert snap["delta"] == snap["positions"]["spot"]["qty"]
    # center = avg_cost > 0
    assert Decimal(snap["center"]) > 0


# ─── C. State persistence round-trip ───


@pytest.mark.asyncio
async def test_state_persistence_round_trip(cfg, mock_adapter, tmpdir_state):
    # session 1: 跑一个 tick, 进 layer 0
    s1 = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, tick_interval_seconds=0.01)
    await s1.start()
    await s1._tick()
    n_layers_1 = s1._state.n_layers
    avg_cost_1 = s1._state.avg_cost
    cash_1 = s1._cash
    cycle_id_1 = s1._state.cycle_id
    s1._persist_state()
    state_file = s1._state_file
    assert state_file.exists()

    # session 2: 恢复状态
    s2 = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, tick_interval_seconds=0.01)
    restored = await s2._try_restore_state()
    assert restored is True
    assert s2._state.n_layers == n_layers_1
    assert s2._state.avg_cost == avg_cost_1
    assert s2._cash == cash_1
    assert s2._state.cycle_id == cycle_id_1


# ─── D. live_mode flag passthrough ───


def test_live_mode_uses_different_state_file(cfg, mock_adapter, tmpdir_state):
    s_paper = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, live_mode=False)
    s_live = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, live_mode=True)
    assert s_paper._state_file != s_live._state_file
    assert "paper" in s_paper._state_file.name


# ─── E. Trades JSONL ledger (append-only, LIVE 对账+回放) ───


def _make_kline_mock(close: str, low: str = None):
    """Build a kline-shaped mock (has .close, .low attributes)"""
    k = MagicMock()
    k.close = close
    k.low = low or close
    return k


def _make_klines_adapter(close: str):
    """Build adapter with both fetch_klines and fetch_ticker returning live-shaped objects"""
    adapter = MagicMock()
    adapter.fetch_klines = AsyncMock(return_value=[_make_kline_mock(close)])
    ticker = MagicMock()
    ticker.last = close
    adapter.fetch_ticker = AsyncMock(return_value=ticker)
    return adapter


def test_jsonl_path_differs_paper_vs_live(cfg, mock_adapter, tmpdir_state):
    s_paper = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, live_mode=False)
    s_live = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, live_mode=True)
    assert s_paper._trades_jsonl != s_live._trades_jsonl
    assert s_paper._trades_jsonl.name == "dgr_btc_trades.jsonl"
    assert s_live._trades_jsonl.name == "dgr_btc_live_trades.jsonl"


@pytest.mark.asyncio
async def test_buy_appends_jsonl_line(cfg, tmpdir_state):
    """ENTRY (layer 0 BUY) 写一条 JSON 行到 trades jsonl"""
    import json

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    await sess.start()
    assert not sess._trades_jsonl.exists()  # 起初无文件
    await sess._tick()

    assert sess._state.n_layers == 1
    assert sess._trades_jsonl.exists()
    lines = sess._trades_jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["mode"] == "paper"
    assert rec["action"] == "BUY"
    assert rec["layer_index"] == 0
    assert rec["cycle_id"] == 0
    assert rec["n_layers_after"] == 1
    assert Decimal(rec["fill_price"]) > Decimal("0")
    assert Decimal(rec["qty"]) > Decimal("0")
    assert Decimal(rec["stake"]) > Decimal("0")
    assert Decimal(rec["cash_after"]) < cfg.total_capital_usdt  # 减了 stake


@pytest.mark.asyncio
async def test_tp_appends_second_jsonl_line(cfg, tmpdir_state):
    """ENTRY → 价格涨过 avg×1.04 → TP 触发 → jsonl 第二行 action=TP, pnl>0"""
    import json

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    await sess.start()
    await sess._tick()  # ENTRY @ 75000
    assert sess._state.n_layers == 1

    # 翻转 mock 为高价让 TP 触发 (avg×1.04 ≈ 78045; 给 79000 留余地)
    adapter.fetch_klines = AsyncMock(return_value=[_make_kline_mock("79000.0")])
    await sess._tick()

    lines = sess._trades_jsonl.read_text().strip().splitlines()
    assert len(lines) == 2
    rec_buy = json.loads(lines[0])
    rec_tp = json.loads(lines[1])
    assert rec_buy["action"] == "BUY"
    assert rec_tp["action"] == "TP"
    assert Decimal(rec_tp["pnl"]) > Decimal("0")
    assert Decimal(rec_tp["proceeds"]) > Decimal(rec_tp["pre_cost"])
    assert rec_tp["n_layers_after"] == 0  # cycle closed


@pytest.mark.asyncio
async def test_jsonl_write_failure_does_not_crash_tick(cfg, tmpdir_state, monkeypatch):
    """Fail-soft: jsonl 写失败 (例如磁盘满 / 权限) 不能阻塞 tick / engine.apply_fill"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    await sess.start()

    # 让 jsonl 的 open 抛 PermissionError (但不能影响 state file open)
    real_open = Path.open

    def selective_boom(self, *args, **kwargs):
        if self.name.endswith("trades.jsonl"):
            raise PermissionError("simulated")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", selective_boom)
    # tick 仍应完成, layer 仍应入仓 (apply_fill 在 jsonl 写之前)
    await sess._tick()
    assert sess._state.n_layers == 1


# ─── F. Pre-liquidation auto-deleverage (审查 #3 救命级) ───


def _mock_unhealthy_margin(sess, ratio: Decimal = Decimal("1.05")):
    """Helper: monkey-patch _check_margin_health to return a fake low margin_ratio.
    避免依赖具体 leverage/notional 数学, 单纯测 deleverage 逻辑.
    """
    def _fake(self_, mark_price, alert=True):
        return {
            "leverage": 10,
            "collateral_usdt": Decimal("1000"),
            "notional_usdt": Decimal("10000"),
            "unrealized_pnl": Decimal("-450"),
            "equity_usdt": Decimal("550"),
            "maintenance_req_usdt": Decimal("500"),
            "margin_ratio": ratio,
            "liq_price": Decimal("62000"),
            "liq_distance_pct": Decimal("3.2"),
        }
    sess._check_margin_health = _fake.__get__(sess, type(sess))


@pytest.mark.asyncio
async def test_pre_liq_deleverage_triggers_below_threshold(cfg, tmpdir_state):
    """当 margin_ratio < threshold 时, 自动减仓 50%, cash 增加 proceeds"""
    import json

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 10
    await sess.start()
    await sess._tick()  # ENTRY L0 @ 75000

    pre_qty = sess._state.total_qty
    pre_avg = sess._state.avg_cost
    pre_cash = sess._cash
    assert pre_qty > Decimal("0")
    assert sess._n_deleverages == 0

    # 注入低 margin_ratio (1.05 < 1.10 threshold)
    _mock_unhealthy_margin(sess, ratio=Decimal("1.05"))
    triggered = await sess._trigger_pre_liq_deleverage(Decimal("65000"))

    assert triggered is True, "应该触发 deleverage"
    assert sess._n_deleverages == 1
    # 持仓减半
    assert sess._state.total_qty == pre_qty * Decimal("0.5")
    # avg_cost 保持不变 (按比例缩, cost/qty 同时减半)
    assert sess._state.avg_cost == pre_avg
    # cash 增加 (proceeds from selling 50% at $65000)
    assert sess._cash > pre_cash
    # jsonl 应写一行 PRE_LIQ_DELEVERAGE
    lines = sess._trades_jsonl.read_text().strip().splitlines()
    rec_last = json.loads(lines[-1])
    assert rec_last["action"] == "PRE_LIQ_DELEVERAGE"
    assert rec_last["reason"] == "pre_liquidation_deleverage"


@pytest.mark.asyncio
async def test_pre_liq_deleverage_skips_above_threshold(cfg, tmpdir_state):
    """margin_ratio 安全时不触发"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 10
    await sess.start()
    await sess._tick()  # ENTRY @ 75000

    # 当前价 = entry, margin_ratio 在初始资本下应该 >> 1.1
    triggered = await sess._trigger_pre_liq_deleverage(Decimal("75000"))
    assert triggered is False
    assert sess._n_deleverages == 0


@pytest.mark.asyncio
async def test_pre_liq_deleverage_cooldown(cfg, tmpdir_state):
    """1h cooldown 内同样的 trigger 条件应被忽略"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 10
    await sess.start()
    await sess._tick()
    _mock_unhealthy_margin(sess, ratio=Decimal("1.05"))

    # 第一次触发
    assert await sess._trigger_pre_liq_deleverage(Decimal("65000")) is True
    assert sess._n_deleverages == 1

    # 紧跟着再调一次 (cooldown 内) — 应跳过
    assert await sess._trigger_pre_liq_deleverage(Decimal("65000")) is False
    assert sess._n_deleverages == 1


@pytest.mark.asyncio
async def test_pre_liq_deleverage_disabled(cfg, tmpdir_state):
    """cfg.risk_pre_liq_enabled = False 时不触发"""
    adapter = _make_klines_adapter("75000.0")
    cfg.risk_pre_liq_enabled = False
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 10
    await sess.start()
    await sess._tick()

    assert await sess._trigger_pre_liq_deleverage(Decimal("60000")) is False
    assert sess._n_deleverages == 0


@pytest.mark.asyncio
async def test_trades_jsonl_fsynced_on_write(cfg, tmpdir_state, monkeypatch):
    """审查 #6: 每笔 fill 必须 fsync 防止 power loss 丢账"""
    import os as _os

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    await sess.start()

    fsync_calls = []
    real_fsync = _os.fsync

    def spy_fsync(fd):
        fsync_calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr("app.strategies.dgr_btc.paper_trading.os.fsync", spy_fsync)
    await sess._tick()  # ENTRY → triggers 1 jsonl write
    # fsync 至少被调一次 (BUY 写一行)
    assert len(fsync_calls) >= 1, "fsync 应在每次 jsonl 写后被调用"


# ─── G. Startup inflight reconcile (审查 #2 LIVE 重启恢复) ───


@pytest.mark.asyncio
async def test_reconcile_skipped_when_not_live_mode(cfg, mock_adapter, tmpdir_state):
    """paper 模式不调 broker, reconcile 直接 skip"""
    sess = DgrBtcPaperSession(cfg=cfg, adapter=mock_adapter, live_mode=False, tick_interval_seconds=0.01)
    # 不传 broker_adapter, 走 paper path
    await sess._reconcile_inflight_on_startup()  # should not raise, returns silently
    assert True  # no exception = pass


@pytest.mark.asyncio
async def test_reconcile_recovers_dgr_orphans(cfg, tmpdir_state):
    """LIVE 模式启动时, broker 有 3 个 dgr_ 挂单 → 全 register_local (需注入真 InflightOrderManager)"""
    from app.strategies.dgr_btc.inflight_manager import InflightOrderManager

    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fake_broker.fetch_open_orders = AsyncMock(return_value=[
        {"clientOrderId": "dgr_l1_abc123", "id": "broker_id_1", "side": "BUY", "price": "70000"},
        {"clientOrderId": "dgr_l2_def456", "id": "broker_id_2", "side": "BUY", "price": "66500"},
        {"clientOrderId": "dgr_tp_ghi789", "id": "broker_id_3", "side": "SELL", "price": "78000"},
        {"clientOrderId": "not_dgr_ignored", "id": "other_strategy", "side": "BUY", "price": "50000"},  # 别的策略
    ])
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    # 替换 _PaperInflightStub 为真 InflightOrderManager (LIVE 实际部署需类似 wiring)
    sess.inflight_manager = InflightOrderManager(
        max_inflight_per_side=10, broker_adapter=fake_broker, live_mode=True,
    )

    assert sess.inflight_manager.total_inflight() == 0
    await sess._reconcile_inflight_on_startup()

    # 3 个 dgr_ 单都被 register, 第 4 个非 dgr 不动
    assert sess.inflight_manager.total_inflight() == 3
    # broker 被调用过
    fake_broker.fetch_open_orders.assert_called_once()


@pytest.mark.asyncio
async def test_reconcile_stub_path_logs_but_does_not_register(cfg, tmpdir_state):
    """paper stub 模式: 检测到 dgr_ 挂单时 log + telegram, 但不 register (stub 无 update_from_open_orders)"""
    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fake_broker.fetch_open_orders = AsyncMock(return_value=[
        {"clientOrderId": "dgr_l1_orphan", "id": "boid", "side": "BUY", "price": "70000"},
    ])
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    # 保留默认 _PaperInflightStub
    await sess._reconcile_inflight_on_startup()
    # stub.total_inflight 仍 0 (没 register)
    assert sess.inflight_manager.total_inflight() == 0


@pytest.mark.asyncio
async def test_reconcile_fail_soft_on_broker_error(cfg, tmpdir_state, caplog):
    """broker.fetch_open_orders 抛异常时, reconcile 不阻塞启动 (fail-soft)"""
    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fake_broker.fetch_open_orders = AsyncMock(side_effect=ConnectionError("simulated"))
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    # 不应抛, 只 log
    await sess._reconcile_inflight_on_startup()
    # 应有 warning log
    assert any("reconcile_fetch_open_orders_failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_reconcile_handles_empty_open_orders(cfg, tmpdir_state):
    """broker 端无任何挂单 → log + 0 recovery"""
    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fake_broker.fetch_open_orders = AsyncMock(return_value=[])
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    await sess._reconcile_inflight_on_startup()
    assert sess.inflight_manager.total_inflight() == 0


# ─── F.2 B1 修复: LIVE 模式 pre-liq deleverage 必须真调 broker ───


def _inject_layer(sess, entry_price: str = "75000", qty: str = "0.328", cost: str = "24616"):
    """手动注入一个 layer 到 state, 跳过 _tick BUY 路径 (避免依赖 broker.place_buy mock).

    用于 pre_liq deleverage 测试聚焦在 deleverage 本身, 不被 ENTRY 路径干扰.
    """
    from app.strategies.dgr_btc.engine import Layer
    sess._state.layers = [
        Layer(
            entry_price=Decimal(entry_price),
            qty_btc=Decimal(qty),
            cost_usdt=Decimal(cost),
        )
    ]
    sess._state.cycle_id = 0
    sess._state.next_buy_price = Decimal(entry_price) * Decimal("0.95")
    sess._cash = Decimal("175384")  # = 200000 - 24616


@pytest.mark.asyncio
async def test_pre_liq_deleverage_live_calls_broker(cfg, tmpdir_state):
    """LIVE 模式触发 deleverage 时必须调 broker.place_market_unwind, 不是只改本地账面.

    B1 致命 bug 修复 (审查 round 2 risk-manager 发现):
    commit 5dc23cd 原版 paper/LIVE 都直接改 state.layers + cash,
    LIVE 切换后 binance 实际仓位不动 → 强平照样发生.
    修复后 LIVE 必走 broker.place_market_unwind, 用真实 fill_px/qty 更新本地账面.
    """
    from app.strategies.dgr_btc.types import MarketType, Side

    adapter = _make_klines_adapter("75000.0")
    # Mock LIVE broker: place_market_unwind 返回 fill 对象
    fake_broker = MagicMock()
    fill_trade = MagicMock()
    fill_trade.price = "65100"  # 真实成交价 (vs mark_price 65000)
    fill_trade.quantity = "0.164"  # 真实成交 qty (= 0.328 × 50%)
    fake_broker.place_market_unwind = AsyncMock(return_value=fill_trade)

    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    sess.cfg.leverage = 10
    # 注: 不走 _tick 避免 broker.place_buy mock 麻烦, 直接手动注入 layer
    _inject_layer(sess)

    _mock_unhealthy_margin(sess, ratio=Decimal("1.05"))
    triggered = await sess._trigger_pre_liq_deleverage(Decimal("65000"))

    assert triggered is True
    # 关键断言: broker.place_market_unwind 被真调
    fake_broker.place_market_unwind.assert_called_once()
    call_args = fake_broker.place_market_unwind.call_args
    assert call_args.args[0] == MarketType.SPOT
    assert call_args.args[1] == Side.SELL
    # quantity 应为持仓的 50%
    assert call_args.kwargs["quantity"] > Decimal("0")
    # jsonl 应记 mode=LIVE
    import json as _json
    lines = sess._trades_jsonl.read_text().strip().splitlines()
    rec = _json.loads(lines[-1])
    assert rec["action"] == "PRE_LIQ_DELEVERAGE"
    assert rec["mode"] == "LIVE"


@pytest.mark.asyncio
async def test_pre_liq_deleverage_live_broker_reject_fail_closed(cfg, tmpdir_state):
    """LIVE broker 拒单时: fail-closed (本地账面不动, 等下 tick 重试).

    比"本地以为已减仓但 binance 没动"安全得多.
    """
    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fake_broker.place_market_unwind = AsyncMock(side_effect=ConnectionError("simulated reject"))

    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    sess.cfg.leverage = 10
    _inject_layer(sess)

    pre_qty = sess._state.total_qty
    pre_cash = sess._cash
    pre_n_deleverages = sess._n_deleverages

    _mock_unhealthy_margin(sess, ratio=Decimal("1.05"))
    triggered = await sess._trigger_pre_liq_deleverage(Decimal("65000"))

    # 关键: 触发失败, 本地账面**不变** (fail-closed)
    assert triggered is False
    assert sess._state.total_qty == pre_qty  # 持仓没变
    assert sess._cash == pre_cash  # cash 没变
    assert sess._n_deleverages == pre_n_deleverages  # 计数器没增
    # 没有 cooldown 锚点更新 (允许下 tick 重试)
    assert sess._last_deleverage_at is None


# ─── F.3 round 2 B3: KILL switch flat-on-trigger ───


@pytest.mark.asyncio
async def test_kill_halt_action_returns_without_flat(cfg, tmpdir_state, monkeypatch, tmp_path):
    """默认 kill_action='halt' 时: _tick 检测 KILL 文件 → 直接 return, 不平仓"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.risk_kill_action = "halt"  # 显式默认
    _inject_layer(sess)
    pre_qty = sess._state.total_qty

    # 写 KILL 文件
    kill_file = tmp_path / "dgr_btc_KILL"
    monkeypatch.setattr("os.path.exists", lambda p: str(p) == "/app/state/dgr_btc_KILL")
    # mock 路径检查
    import os as _os
    monkeypatch.setattr(_os.path, "exists", lambda p: p == "/app/state/dgr_btc_KILL")

    await sess._tick()
    # halt 模式下持仓不变
    assert sess._state.total_qty == pre_qty
    assert sess._kill_flat_executed is False


@pytest.mark.asyncio
async def test_kill_flat_action_executes_full_unwind_paper(cfg, tmpdir_state, monkeypatch):
    """kill_action='flat' + paper 模式: KILL 触发后强制全平 (合成 fill)"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.risk_kill_action = "flat"
    _inject_layer(sess)
    sess._last_spot_px = Decimal("70000")  # 触发价

    pre_cash = sess._cash
    assert sess._state.is_in_cycle

    # mock KILL 文件存在
    import os as _os
    monkeypatch.setattr(_os.path, "exists", lambda p: p == "/app/state/dgr_btc_KILL")

    await sess._tick()
    # 平仓后 layers 清空, cash 增加 proceeds
    assert sess._state.n_layers == 0
    assert sess._cash > pre_cash
    assert sess._kill_flat_executed is True


@pytest.mark.asyncio
async def test_kill_flat_action_calls_broker_in_live(cfg, tmpdir_state, monkeypatch):
    """kill_action='flat' + LIVE 模式: 调 broker.place_market_unwind 真下平仓单"""
    from app.strategies.dgr_btc.types import MarketType, Side

    adapter = _make_klines_adapter("75000.0")
    fake_broker = MagicMock()
    fill_trade = MagicMock()
    fill_trade.price = "70100"
    fill_trade.quantity = "0.328"
    fake_broker.place_market_unwind = AsyncMock(return_value=fill_trade)

    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True,
        broker_adapter=fake_broker, tick_interval_seconds=0.01,
    )
    sess.cfg.risk_kill_action = "flat"
    _inject_layer(sess)
    sess._last_spot_px = Decimal("70000")

    import os as _os
    monkeypatch.setattr(_os.path, "exists", lambda p: p == "/app/state/dgr_btc_KILL")

    await sess._tick()
    # LIVE: broker 必调
    fake_broker.place_market_unwind.assert_called_once()
    call_args = fake_broker.place_market_unwind.call_args
    assert call_args.args[0] == MarketType.SPOT
    assert call_args.args[1] == Side.SELL
    assert sess._state.n_layers == 0
    assert sess._kill_flat_executed is True


# ─── F.4 round 2: max_forced_holds 24h KILL ───


def test_max_forced_holds_kill_triggers_after_threshold(cfg, tmpdir_state, tmp_path, monkeypatch):
    """满 max_layers 层 + 卡死超 24h → 写 KILL switch 文件"""
    from datetime import timedelta

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.risk_max_forced_holds_hours = 24

    # 注入满 4 层 (max_layers=4)
    from app.strategies.dgr_btc.engine import Layer
    sess._state.layers = [
        Layer(entry_price=Decimal("75000"), qty_btc=Decimal("0.05"), cost_usdt=Decimal("3750")),
        Layer(entry_price=Decimal("71250"), qty_btc=Decimal("0.07"), cost_usdt=Decimal("4988")),
        Layer(entry_price=Decimal("67688"), qty_btc=Decimal("0.10"), cost_usdt=Decimal("6769")),
        Layer(entry_price=Decimal("64303"), qty_btc=Decimal("0.15"), cost_usdt=Decimal("9645")),
    ]
    sess._cash = Decimal("174848")

    # 模拟 25 小时前满层
    sess._full_layer_since = datetime.now(timezone.utc) - timedelta(hours=25)

    # 用 monkeypatch 替换 Path.touch 验证 KILL 文件写入
    touched_paths: list[str] = []
    real_touch = Path.touch
    monkeypatch.setattr(Path, "touch", lambda self: touched_paths.append(str(self)))

    sess._check_max_forced_holds_kill()
    # 应触发 KILL 文件创建
    kill_files = [p for p in touched_paths if "FULL_LAYER_TIMEOUT" in p]
    assert len(kill_files) == 1


def test_max_forced_holds_skipped_under_threshold(cfg, tmpdir_state):
    """满层但未超阈值 (1h) 不触发"""
    from datetime import timedelta
    from app.strategies.dgr_btc.engine import Layer

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.risk_max_forced_holds_hours = 24
    sess._state.layers = [
        Layer(entry_price=Decimal("75000"), qty_btc=Decimal(f"0.{i+1}"), cost_usdt=Decimal(f"{(i+1)*1000}"))
        for i in range(4)
    ]
    sess._full_layer_since = datetime.now(timezone.utc) - timedelta(hours=1)  # 仅 1h
    # 不应抛 / 不应触发 KILL
    sess._check_max_forced_holds_kill()  # 不抛即通过


def test_max_forced_holds_resets_when_not_full(cfg, tmpdir_state):
    """非满层时 _full_layer_since 必须重置"""
    from datetime import timedelta
    from app.strategies.dgr_btc.engine import Layer

    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess._state.layers = [Layer(entry_price=Decimal("75000"), qty_btc=Decimal("0.1"), cost_usdt=Decimal("7500"))]
    sess._full_layer_since = datetime.now(timezone.utc) - timedelta(hours=10)

    sess._check_max_forced_holds_kill()
    # 非满层 → 锚点重置
    assert sess._full_layer_since is None


@pytest.mark.asyncio
async def test_pre_liq_deleverage_no_leverage(cfg, tmpdir_state):
    """无杠杆 (leverage=1) 时不触发 (无强平风险)"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 1
    await sess.start()
    await sess._tick()

    # 即便价格暴跌, 1x spot 没强平也不应触发
    assert await sess._trigger_pre_liq_deleverage(Decimal("30000")) is False
    assert sess._n_deleverages == 0
