"""单元测试 — services/balance_reconciler.py

R1+R2: 后台余额 + 持仓对账，单腿告警。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exchanges.models import InstrumentType
from app.services.balance_reconciler import (
    BalanceReconcilerService,
    ReconcileAlert,
    _normalize_symbol,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_adapter(spot_balance: dict, perp_positions: list) -> MagicMock:
    """构造 mock adapter，fetch_balance 返回 spot dict，_clients[PERP].fetch_positions 返回 list。"""
    adapter = MagicMock()
    adapter._api_key = "test-key"
    adapter.fetch_balance = AsyncMock(return_value=spot_balance)
    perp_client = MagicMock()
    perp_client.fetch_positions = AsyncMock(return_value=perp_positions)
    adapter._clients = {InstrumentType.PERPETUAL: perp_client}
    return adapter


def _make_db_position(uuid: str, position_id: int, status: str = "open") -> MagicMock:
    rec = MagicMock()
    rec.uuid = uuid
    rec.id = position_id
    rec.status = status
    return rec


def _make_db_leg(
    position_id: int,
    instrument: str = "perpetual",
    exchange: str = "binance",
    symbol: str = "FIL/USDT",
    size: str = "10",
) -> MagicMock:
    leg = MagicMock()
    leg.position_id = position_id
    leg.instrument_type = instrument
    leg.exchange = exchange
    leg.symbol = symbol
    leg.size = Decimal(size)
    return leg


def _patch_db(open_records: list, legs: list):
    """patch get_session 返回模拟的 select 结果。"""
    @asynccontextmanager
    async def fake_session():
        session = MagicMock()
        # _reconcile 调用 select(PositionRecord) 然后 select(PositionLegRecord)
        result_records = MagicMock()
        result_records.scalars.return_value.all.return_value = open_records
        result_legs = MagicMock()
        result_legs.scalars.return_value.all.return_value = legs

        call_state = {"i": 0}
        async def fake_execute(stmt):
            i = call_state["i"]
            call_state["i"] += 1
            if i == 0:
                return result_records
            return result_legs

        session.execute = fake_execute
        yield session

    return fake_session


# ---------------------------------------------------------------------------
# Cache 刷新
# ---------------------------------------------------------------------------


class TestRefresh:
    @pytest.mark.asyncio
    async def test_balance_cache_populated(self):
        adapter = _make_adapter(
            spot_balance={"USDT": {"free": "100", "total": "100", "used": "0"}},
            perp_positions=[],
        )
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        # patch DB 返回空（不触发对账逻辑细节）
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            await svc.run_once()
        assert "binance" in svc.balance_cache
        assert "USDT" in svc.balance_cache["binance"]
        assert svc.balance_cache["binance"]["USDT"]["free"] == "100"

    @pytest.mark.asyncio
    async def test_position_cache_populated(self):
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "FIL/USDT:USDT", "side": "short",
                 "contracts": 42.3, "entryPrice": 1.18, "unrealizedPnl": 0.01},
            ],
        )
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            await svc.run_once()
        assert "binance" in svc.position_cache
        assert len(svc.position_cache["binance"]) == 1
        assert svc.position_cache["binance"][0]["symbol"] == "FIL/USDT:USDT"

    @pytest.mark.asyncio
    async def test_zero_contracts_filtered(self):
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "FIL/USDT:USDT", "side": "short", "contracts": 0},
                {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5},
            ],
        )
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            await svc.run_once()
        # 0-contract position 应被过滤
        assert len(svc.position_cache["binance"]) == 1
        assert svc.position_cache["binance"][0]["symbol"] == "BTC/USDT:USDT"


# ---------------------------------------------------------------------------
# 对账：单腿暴露 / 残留 / 数量漂移
# ---------------------------------------------------------------------------


class TestReconcileSingleLegExposure:
    @pytest.mark.asyncio
    async def test_db_open_perp_missing_alerts_critical(self):
        """DB 有 perp leg OPEN 但真实 fetch_positions 不含此 symbol → critical 告警。"""
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[],  # 真实交易所 perp 0 持仓
        )
        rec = _make_db_position("pos-uuid-1", 1)
        leg = _make_db_leg(1, "perpetual", symbol="FIL/USDT")

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                await svc.run_once()
        critical = [a for a in svc.recent_alerts if a.severity == "critical"]
        assert len(critical) >= 1
        assert critical[0].type == "single_leg_exposure"
        assert critical[0].symbol == "FIL/USDT"

    @pytest.mark.asyncio
    async def test_db_open_spot_balance_short_alerts(self):
        """DB 有 spot leg OPEN 但真实 spot 余额 < leg.size → 单腿告警。"""
        adapter = _make_adapter(
            spot_balance={"FIL": {"free": "0.5", "total": "0.5", "used": "0"}},
            perp_positions=[],
        )
        rec = _make_db_position("pos-uuid-2", 2)
        leg = _make_db_leg(2, "spot", symbol="FIL/USDT", size="10")

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                await svc.run_once()
        alerts = [a for a in svc.recent_alerts if a.type == "single_leg_exposure"]
        assert any(a.symbol == "FIL/USDT" for a in alerts)


class TestReconcileOrphan:
    @pytest.mark.asyncio
    async def test_real_position_no_db_alerts_orphan(self):
        """真实交易所有 perp 持仓但 DB 无 OPEN → 残留告警。"""
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "DOGE/USDT:USDT", "side": "short", "contracts": 100},
            ],
        )
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                await svc.run_once()
        orphan = [a for a in svc.recent_alerts if a.type == "orphan_position"]
        assert len(orphan) == 1
        assert orphan[0].symbol == "DOGE/USDT"


class TestReconcileQtyDrift:
    @pytest.mark.asyncio
    async def test_perp_qty_drift_high_severity(self):
        """DB 期望 10 但真实 8 (20% 差) → high 告警。"""
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "FIL/USDT:USDT", "side": "short", "contracts": 8},
            ],
        )
        rec = _make_db_position("pos-uuid-3", 3)
        leg = _make_db_leg(3, "perpetual", symbol="FIL/USDT", size="10")
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                await svc.run_once()
        drifts = [a for a in svc.recent_alerts if a.type == "qty_drift"]
        assert len(drifts) >= 1
        assert drifts[0].severity == "high"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_strips_perp_settle_suffix(self):
        assert _normalize_symbol("FIL/USDT:USDT") == "FIL/USDT"

    def test_spot_unchanged(self):
        assert _normalize_symbol("BTC/USDT") == "BTC/USDT"


class TestSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_includes_caches_and_alerts(self):
        adapter = _make_adapter(
            spot_balance={"USDT": {"free": "100", "total": "100"}},
            perp_positions=[],
        )
        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            snap = await svc.run_once()
        assert "balance_cache" in snap
        assert "position_cache" in snap
        assert "recent_alerts" in snap
        assert snap["alerts_total"] == 0


class TestAutoUnwindOrphan:
    """TS_R2.b — orphan_position 自动反向平 perp。"""

    @pytest.mark.asyncio
    async def test_orphan_perp_short_auto_unwound_under_cap(self):
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "FIL/USDT:USDT", "side": "short",
                 "contracts": 10, "entryPrice": 1.5, "unrealizedPnl": 0},
            ],
        )
        # orphan 平仓走 perp_client.create_market_buy_order
        perp_client = adapter._clients[InstrumentType.PERPETUAL]
        perp_client.create_market_buy_order = AsyncMock(
            return_value={"id": "unwound-1", "filled": 10}
        )
        adapter.exchange_id = "binance"
        # 防干扰：清空 module-level _auto_unwind_attempted
        from app.services.balance_reconciler import _auto_unwind_attempted
        _auto_unwind_attempted.clear()

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                with patch.object(svc, "_notify_alert", new=MagicMock()):
                    await svc.run_once()
        # notional = 10 * 1.5 = 15 < $200 cap → 应触发
        perp_client.create_market_buy_order.assert_awaited_once()
        call = perp_client.create_market_buy_order.await_args
        assert call.args[1] == 10.0  # contracts
        assert call.kwargs["params"]["positionSide"] == "SHORT"

    @pytest.mark.asyncio
    async def test_orphan_above_cap_not_unwound(self):
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[
                {"symbol": "BTC/USDT:USDT", "side": "short",
                 "contracts": 0.01, "entryPrice": 60000, "unrealizedPnl": 0},
            ],
        )
        perp_client = adapter._clients[InstrumentType.PERPETUAL]
        perp_client.create_market_buy_order = AsyncMock(return_value={"id": "x"})
        adapter.exchange_id = "binance"
        from app.services.balance_reconciler import _auto_unwind_attempted
        _auto_unwind_attempted.clear()

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                with patch.object(svc, "_notify_alert", new=MagicMock()):
                    await svc.run_once()
        # notional = 0.01 * 60000 = 600 > $200 → 不触发
        perp_client.create_market_buy_order.assert_not_awaited()


class TestLivePnLComputation:
    """TS_R7 — live_pnl 用 hub ticker 计算 mark-to-market。"""

    @pytest.mark.asyncio
    async def test_pnl_computed_for_open_position(self):
        adapter = _make_adapter(spot_balance={}, perp_positions=[])
        # mock hub ticker
        hub = MagicMock()
        ticker = MagicMock()
        ticker.last = Decimal("65000")  # current
        ticker.bid = Decimal("64995")
        hub.get_ticker = MagicMock(return_value=ticker)

        rec = _make_db_position("uuid-pnl", 100)
        leg_spot = _make_db_leg(100, "spot", symbol="BTC/USDT", size="1")
        leg_spot.side = "buy"
        leg_spot.entry_price = Decimal("60000")
        leg_perp = _make_db_leg(100, "perpetual", symbol="BTC/USDT", size="1")
        leg_perp.side = "sell"
        leg_perp.entry_price = Decimal("60000")

        svc = BalanceReconcilerService(adapters={"binance": adapter}, market_data_hub=hub)
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg_spot, leg_perp])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                with patch.object(svc, "_notify_alert", new=MagicMock()):
                    await svc.run_once()
        assert "uuid-pnl" in svc.live_pnl_cache
        # spot long: (65000-60000)*1 = +5000
        # perp short: (60000-65000)*1 = -5000
        # delta-neutral → 0
        pnl = Decimal(svc.live_pnl_cache["uuid-pnl"]["unrealized_pnl"])
        assert pnl == Decimal("0.0000")

    @pytest.mark.asyncio
    async def test_pnl_skipped_when_no_hub(self):
        adapter = _make_adapter(spot_balance={}, perp_positions=[])
        rec = _make_db_position("uuid-x", 200)
        leg = _make_db_leg(200, "spot", symbol="BTC/USDT")
        svc = BalanceReconcilerService(adapters={"binance": adapter}, market_data_hub=None)
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                with patch.object(svc, "_notify_alert", new=MagicMock()):
                    await svc.run_once()
        assert svc.live_pnl_cache == {}


class TestStaleOrderCancellation:
    """T6/R12 — 残留挂单超 30min 未成交自动撤。"""

    @pytest.mark.asyncio
    async def test_stale_order_gets_cancelled(self):
        from datetime import timedelta
        adapter = _make_adapter(spot_balance={}, perp_positions=[])
        # 一个 35min 前的挂单
        old_ts = int((datetime.now(UTC) - timedelta(minutes=35)).timestamp() * 1000)
        stale_order = MagicMock()
        stale_order.order_id = "stale-123"
        stale_order.symbol = "BTC/USDT"
        stale_order.timestamp = old_ts
        stale_order.side = MagicMock(value="buy")
        stale_order.instrument = MagicMock(value="spot")
        adapter.fetch_open_orders = AsyncMock(return_value=[stale_order])
        adapter.cancel_order = AsyncMock(return_value=True)

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            with patch("app.services.risk_event_service.write_risk_event", new=AsyncMock()):
                await svc.run_once()
        adapter.cancel_order.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recent_order_not_cancelled(self):
        adapter = _make_adapter(spot_balance={}, perp_positions=[])
        recent_ts = int(datetime.now(UTC).timestamp() * 1000) - 60_000  # 1min ago
        recent_order = MagicMock()
        recent_order.order_id = "fresh-123"
        recent_order.timestamp = recent_ts
        adapter.fetch_open_orders = AsyncMock(return_value=[recent_order])
        adapter.cancel_order = AsyncMock(return_value=True)

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([], [])):
            await svc.run_once()
        adapter.cancel_order.assert_not_awaited()


class TestNoDuplicateAlerts:
    @pytest.mark.asyncio
    async def test_same_alert_only_fired_once(self):
        """同一 (uuid, leg) 告警两次后第二次不该再 append。"""
        adapter = _make_adapter(
            spot_balance={},
            perp_positions=[],
        )
        rec = _make_db_position("dedup-uuid", 9)
        leg = _make_db_leg(9, "perpetual", symbol="FIL/USDT")

        svc = BalanceReconcilerService(adapters={"binance": adapter})
        with patch("app.core.database.get_session", new=_patch_db([rec], [leg])):
            with patch.object(svc, "_persist_alert", new=AsyncMock()):
                await svc.run_once()
                first = len(svc.recent_alerts)
                await svc.run_once()
                second = len(svc.recent_alerts)
        assert first == 1
        assert second == 1  # 没增加
