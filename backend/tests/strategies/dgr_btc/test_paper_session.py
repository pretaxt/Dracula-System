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
