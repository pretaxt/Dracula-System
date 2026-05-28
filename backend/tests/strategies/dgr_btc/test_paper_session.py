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
    triggered = sess._trigger_pre_liq_deleverage(Decimal("65000"))

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
    triggered = sess._trigger_pre_liq_deleverage(Decimal("75000"))
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
    assert sess._trigger_pre_liq_deleverage(Decimal("65000")) is True
    assert sess._n_deleverages == 1

    # 紧跟着再调一次 (cooldown 内) — 应跳过
    assert sess._trigger_pre_liq_deleverage(Decimal("65000")) is False
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

    assert sess._trigger_pre_liq_deleverage(Decimal("60000")) is False
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


@pytest.mark.asyncio
async def test_pre_liq_deleverage_no_leverage(cfg, tmpdir_state):
    """无杠杆 (leverage=1) 时不触发 (无强平风险)"""
    adapter = _make_klines_adapter("75000.0")
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, tick_interval_seconds=0.01)
    sess.cfg.leverage = 1
    await sess.start()
    await sess._tick()

    # 即便价格暴跌, 1x spot 没强平也不应触发
    assert sess._trigger_pre_liq_deleverage(Decimal("30000")) is False
    assert sess._n_deleverages == 0
