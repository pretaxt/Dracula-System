"""单元测试 — notifications/telegram_bot_handlers.py"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.notifications import telegram_bot_handlers as h


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _state(**fields) -> SimpleNamespace:
    """构造可读写的 app_state 替身。"""
    defaults = {
        "adapters": {},
        "paper_session": None,
        "paper_task": None,
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def _fake_position(symbol="BTC/USDT", notional="100", upnl="1.23",
                   funding="0.05", hours="2") -> MagicMock:
    p = MagicMock()
    p.symbol = symbol
    p.notional_usd = Decimal(notional)
    p.unrealized_pnl = Decimal(upnl)
    p.funding_received = Decimal(funding)
    p.holding_hours = Decimal(hours)
    return p


# ---------------------------------------------------------------------------
# _balance_handler
# ---------------------------------------------------------------------------


class TestBalanceHandler:
    @pytest.mark.asyncio
    async def test_no_adapters(self):
        out = await h._balance_handler(_state(adapters={}))
        assert "未初始化" in out or "尚未" in out

    @pytest.mark.asyncio
    async def test_aggregates_per_exchange(self, monkeypatch):
        async def _fake(adapters):
            return {"binance": Decimal("100.50"), "okx": Decimal("50.00")}

        monkeypatch.setattr(
            h.balance_service, "get_per_exchange_equity", _fake,
        )
        out = await h._balance_handler(_state(adapters={"binance": "x", "okx": "y"}))
        assert "BINANCE" in out
        assert "OKX" in out
        assert "100.50" in out
        assert "50.00" in out
        assert "150.50" in out  # total


# ---------------------------------------------------------------------------
# _positions_handler
# ---------------------------------------------------------------------------


class TestPositionsHandler:
    @pytest.fixture(autouse=True)
    def _patch_spot_perp(self, monkeypatch):
        """默认 stub spot_perp DB 读取为空，避免每个测试都要写。"""
        async def _empty():
            return 0, []
        monkeypatch.setattr(h, "_read_spot_perp_open_rows", _empty)

    @pytest.mark.asyncio
    async def test_no_session_and_no_spot_perp(self):
        out = await h._positions_handler(_state(paper_session=None))
        assert "无持仓" in out

    @pytest.mark.asyncio
    async def test_empty_positions(self):
        sess = MagicMock()
        sess._manager.open_positions = []
        out = await h._positions_handler(_state(paper_session=sess))
        assert "无持仓" in out

    @pytest.mark.asyncio
    async def test_with_positions(self):
        sess = MagicMock()
        sess._manager.open_positions = [
            _fake_position(symbol="BTC/USDT"),
            _fake_position(symbol="ETH/USDT", upnl="-0.5"),
        ]
        out = await h._positions_handler(_state(paper_session=sess))
        assert "BTC/USDT" in out
        assert "ETH/USDT" in out
        assert "(2)" in out
        assert "#01" in out  # 分组标题

    @pytest.mark.asyncio
    async def test_funding_rate_read_failure_logged_but_continues(self):
        sess = MagicMock()
        # property raises
        type(sess._manager).open_positions = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        out = await h._positions_handler(_state(paper_session=sess))
        # spot_perp 也空 → 整体 "无持仓"；funding-rate 异常已记日志
        assert "无持仓" in out

    @pytest.mark.asyncio
    async def test_only_spot_perp_positions(self, monkeypatch):
        async def _two_rows():
            return 2, [
                "  <code>BTC/USDT      </code> $50N  entry+0.500%  uPnL+0.020",
                "  <code>ETH/USDT      </code> $50N  entry+0.350%  uPnL-0.010",
            ]
        monkeypatch.setattr(h, "_read_spot_perp_open_rows", _two_rows)
        out = await h._positions_handler(_state(paper_session=None))
        assert "(2)" in out
        assert "#04" in out
        assert "BTC/USDT" in out

    @pytest.mark.asyncio
    async def test_combined_groups(self, monkeypatch):
        sess = MagicMock()
        sess._manager.open_positions = [_fake_position(symbol="SOL/USDT")]

        async def _one_sp():
            return 1, ["  <code>BTC/USDT      </code> $50N  entry+0.500%  uPnL+0.020"]
        monkeypatch.setattr(h, "_read_spot_perp_open_rows", _one_sp)

        out = await h._positions_handler(_state(paper_session=sess))
        assert "(2)" in out
        assert "#01" in out
        assert "#04" in out
        assert "SOL/USDT" in out
        assert "BTC/USDT" in out


# ---------------------------------------------------------------------------
# _pause_handler / _resume_handler
# ---------------------------------------------------------------------------


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_when_already_paused(self):
        out = await h._pause_handler(_state(paper_session=None, paper_task=None))
        assert "已处于暂停" in out

    @pytest.mark.asyncio
    async def test_pause_when_running(self, monkeypatch):
        task = MagicMock()
        task.done.return_value = False
        state = _state(paper_session=MagicMock(), paper_task=task)

        called = {}

        async def _fake_stop(s):
            called["stop"] = s
            return True

        monkeypatch.setattr(h.strategy_control, "stop_paper", _fake_stop)
        out = await h._pause_handler(state)
        assert called["stop"] is state
        assert "暂停" in out

    @pytest.mark.asyncio
    async def test_resume_when_already_running(self):
        task = MagicMock()
        task.done.return_value = False
        out = await h._resume_handler(
            _state(paper_session=MagicMock(), paper_task=task),
        )
        assert "已在运行" in out

    @pytest.mark.asyncio
    async def test_resume_when_paused(self, monkeypatch):
        called = {}

        async def _fake_start(s):
            called["start"] = s
            return True

        monkeypatch.setattr(h.strategy_control, "start_paper", _fake_start)
        state = _state(paper_session=None, paper_task=None)
        out = await h._resume_handler(state)
        assert called["start"] is state
        assert "恢复" in out


# ---------------------------------------------------------------------------
# _status_handler
# ---------------------------------------------------------------------------


class TestStatusHandler:
    @pytest.fixture(autouse=True)
    def _patch_spot_perp(self, monkeypatch):
        async def _empty():
            return 0, []
        monkeypatch.setattr(h, "_read_spot_perp_open_rows", _empty)

    @pytest.mark.asyncio
    async def test_status_paused_no_adapters(self):
        out = await h._status_handler(_state())
        assert "暂停" in out
        assert "Dracula" in out

    @pytest.mark.asyncio
    async def test_status_running_with_balance(self, monkeypatch):
        async def _fake(adapters):
            return {"binance": Decimal("200.00")}

        monkeypatch.setattr(
            h.balance_service, "get_per_exchange_equity", _fake,
        )
        sess = MagicMock()
        sess._manager.open_positions = [_fake_position(), _fake_position()]
        task = MagicMock()
        task.done.return_value = False

        out = await h._status_handler(_state(
            adapters={"binance": "x"},
            paper_session=sess,
            paper_task=task,
        ))
        assert "运行" in out
        assert "200.00" in out
        assert "2" in out  # #01 position count


# ---------------------------------------------------------------------------
# build_command_handlers
# ---------------------------------------------------------------------------


class TestBuildHandlers:
    def test_factory_returns_command_handlers(self):
        ch = h.build_command_handlers(_state())
        for name in ("balance", "positions", "pause", "resume", "status", "help"):
            cb = getattr(ch, name)
            assert callable(cb)

    @pytest.mark.asyncio
    async def test_help_returns_text(self):
        ch = h.build_command_handlers(_state())
        out = await ch.help()
        assert "/balance" in out
        assert "/余额" in out
        assert "/help" in out


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_fmt_usd_none(self):
        assert h._fmt_usd(None) == "—"

    def test_fmt_usd_decimal(self):
        assert h._fmt_usd(Decimal("1234.5")) == "$1,234.50"

    def test_fmt_usd_invalid_falls_back(self):
        assert h._fmt_usd("not a number") == "not a number"

    def test_is_paper_running_false_when_task_done(self):
        task = MagicMock()
        task.done.return_value = True
        assert h._is_paper_running(
            _state(paper_session=MagicMock(), paper_task=task)
        ) is False

    def test_is_paper_running_true(self):
        task = MagicMock()
        task.done.return_value = False
        assert h._is_paper_running(
            _state(paper_session=MagicMock(), paper_task=task)
        ) is True
