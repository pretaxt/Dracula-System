"""Phase H paper_trading 路径单测.

覆盖 _sync_fills / _maintain_resting_pairs / _place_pair 三个核心方法
通过 mock broker_adapter + 真实 inflight_manager 验证调用契约。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.inflight_manager import InflightOrderManager
from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession
from app.strategies.dgr_btc.types import MarketState, MarketType, Side, Trade


def _mk_cfg() -> DgrBtcStrategyConfig:
    """生成最小可用的 dgr_btc 配置."""
    return DgrBtcStrategyConfig(
        instance_name="test",
        enabled=True,
        symbol_base="BTC",
        symbol_quote="USDT",
        symbol_spot="BTC/USDT",
        symbol_perp="BTC/USDT:USDT",
        exchange="binance",
        total_capital_usdt=Decimal("15000"),
        spot_initial_btc=Decimal("0.01"),
        short_initial_btc=Decimal("0.01"),
        reserve_ratio=Decimal("0"),
        grid_center_price=Decimal("76000"),
        grid_step_usdt=Decimal("500"),
        grid_qty_per_grid=Decimal("0.001"),
        width_pct=Decimal("0.15"),
        recenter_trigger_pct=Decimal("0.12"),
        max_spot_btc=Decimal("0.2"),
        max_short_btc=Decimal("0.2"),
        delta_target=Decimal("0"),
        delta_upper_limit=Decimal("0.02"),
        delta_lower_limit=Decimal("-0.02"),
        leverage=10,
        risk_trend_grids_threshold=5,
    )


def _mk_session(live_mode: bool = True, pre_place: bool = True) -> DgrBtcPaperSession:
    """创建 session 实例（不调 start, 避免拉外部价）."""
    import os
    if pre_place:
        os.environ["DGR_BTC_PRE_PLACE"] = "1"
    else:
        os.environ["DGR_BTC_PRE_PLACE"] = "0"
    cfg = _mk_cfg()
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=MagicMock(), tick_interval_seconds=30.0,
        live_mode=live_mode,
        broker_adapter=AsyncMock(),
    )
    # 模拟 start() 完成的状态：手工实例化 strategy
    from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy
    sess.strategy = DgrBtcStrategy(cfg, start_price=Decimal("76000"))
    return sess


def _mk_market() -> MarketState:
    return MarketState(
        timestamp=datetime.now(timezone.utc),
        spot_price=Decimal("76000"),
        perp_price=Decimal("76000"),
    )


# ============================================================
# pre_place_enabled 开关
# ============================================================

class TestPrePlaceGate:
    @pytest.mark.asyncio
    async def test_sync_fills_noop_when_disabled(self) -> None:
        sess = _mk_session(live_mode=False, pre_place=False)
        await sess._sync_fills()
        # broker 不应被调
        sess._broker_adapter.fetch_open_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_maintain_noop_when_disabled(self) -> None:
        sess = _mk_session(live_mode=False, pre_place=False)
        await sess._maintain_resting_pairs(_mk_market())
        sess._broker_adapter.place_limit_maker.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_fills_noop_when_broker_none(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        sess._broker_adapter = None
        # 不能崩
        await sess._sync_fills()


# ============================================================
# _sync_fills: 检测 fill → on_trade + jsonl
# ============================================================

class TestSyncFillsFillDetection:
    @pytest.mark.asyncio
    async def test_fill_triggers_on_trade(self, tmp_path, monkeypatch) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        # register 一个 inflight
        sess.inflight_manager.register_local(
            order_id="abc", market=MarketType.SPOT,
            grid_level=Decimal("76500"), side="SELL",
        )
        # mock broker: 该 order 已不在 open_orders, fetch_order 返回 closed+filled
        sess._broker_adapter.fetch_open_orders = AsyncMock(return_value=[])
        sess._broker_adapter.fetch_order = AsyncMock(return_value={
            "id": "abc", "status": "closed", "filled": "0.001",
            "average": "76500", "price": "76500",
        })
        # patch persist (避免写文件)
        monkeypatch.setattr(sess, "_persist_trade_jsonl", lambda *a, **kw: None)
        # spy on strategy.on_trade
        on_trade_calls = []
        original_on_trade = sess.strategy.on_trade
        def spy_on_trade(trade):
            on_trade_calls.append(trade)
            return original_on_trade(trade)
        sess.strategy.on_trade = spy_on_trade

        await sess._sync_fills()

        # 验证: fetch_order 被调 + on_trade 被调 + inflight 被移除
        sess._broker_adapter.fetch_order.assert_called_once()
        assert len(on_trade_calls) == 1
        assert on_trade_calls[0].quantity == Decimal("0.001")
        assert on_trade_calls[0].is_maker is True
        assert sess.inflight_manager.count(MarketType.SPOT) == 0


class TestSyncFillsOrphanRecovery:
    @pytest.mark.asyncio
    async def test_orphan_registered_locally(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        # 本地无 inflight, broker 有一单（restart 场景）
        sess._broker_adapter.fetch_open_orders = AsyncMock(return_value=[
            {"id": "orphan1", "clientOrderId": "dgr_orphan1",
             "_dgr_market": "spot", "price": "76500", "side": "sell", "status": "open"},
        ])
        await sess._sync_fills()
        # 验证: orphan 被 register
        assert sess.inflight_manager.count(MarketType.SPOT) == 1
        ios = sess.inflight_manager.get_inflight(MarketType.SPOT)
        assert ios[0].order_id == "orphan1"


class TestSyncFillsSingleLegAlert:
    @pytest.mark.asyncio
    async def test_single_leg_fill_warns(self, monkeypatch) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        # spot 单消失 (fill)，perp 同 grid 还活
        sess.inflight_manager.register_local(
            "spot1", MarketType.SPOT, Decimal("76500"), "SELL",
        )
        sess.inflight_manager.register_local(
            "perp1", MarketType.PERP, Decimal("76500"), "BUY",
        )
        sess._broker_adapter.fetch_open_orders = AsyncMock(return_value=[
            {"id": "perp1", "clientOrderId": "dgr_perp1",
             "_dgr_market": "swap", "price": "76500", "side": "buy", "status": "open"},
        ])
        sess._broker_adapter.fetch_order = AsyncMock(return_value={
            "id": "spot1", "status": "closed", "filled": "0.001",
            "average": "76500",
        })
        monkeypatch.setattr(sess, "_persist_trade_jsonl", lambda *a, **kw: None)
        notify_calls = []
        sess._notify = AsyncMock(side_effect=lambda msg: notify_calls.append(msg))

        await sess._sync_fills()

        # 应触发 Telegram 单腿告警
        assert len(notify_calls) >= 1
        assert "单腿 fill" in notify_calls[0]


# ============================================================
# _maintain_resting_pairs: 派单 / skip / cancel_all
# ============================================================

class TestMaintainSkipExisting:
    @pytest.mark.asyncio
    async def test_skip_when_grid_already_inflight(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        # 上下两格都已 inflight
        center = sess.strategy.center  # 76000
        step = sess.cfg.grid_step_usdt
        sess.inflight_manager.register_local(
            "u1", MarketType.SPOT, center + step, "SELL",
        )
        sess.inflight_manager.register_local(
            "l1", MarketType.SPOT, center - step, "BUY",
        )
        sess._broker_adapter.place_limit_maker = AsyncMock()

        await sess._maintain_resting_pairs(_mk_market())

        # 不应再派单
        sess._broker_adapter.place_limit_maker.assert_not_called()


class TestMaintainTrendHaltCancelAll:
    @pytest.mark.asyncio
    async def test_trend_halt_cancels_all(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        sess.inflight_manager.register_local(
            "a", MarketType.SPOT, Decimal("76500"), "SELL",
        )
        # mock trend halt active
        sess.strategy.grid.is_trending = lambda threshold: True
        sess._broker_adapter.cancel_order_by_market = AsyncMock(return_value=True)

        await sess._maintain_resting_pairs(_mk_market())

        sess._broker_adapter.cancel_order_by_market.assert_called_once()
        assert sess.inflight_manager.count(MarketType.SPOT) == 0


class TestMaintainRecenterCancelsThenPlaces:
    @pytest.mark.asyncio
    async def test_recenter_triggers_cancel_then_place(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        sess._last_maintain_center = Decimal("75000")  # 老 center
        # strategy.center 实际 76000 → 检测到 recenter
        sess.inflight_manager.register_local(
            "old", MarketType.SPOT, Decimal("75500"), "SELL",
        )
        sess._broker_adapter.cancel_order_by_market = AsyncMock(return_value=True)
        def _trade(oid, mkt, side, px):
            return Trade(
                trade_id=oid, order_id=oid,
                symbol="BTC/USDT" if mkt == MarketType.SPOT else "BTC/USDT:USDT",
                market=mkt, side=side, price=Decimal(px), quantity=Decimal("0"),
                fee=Decimal("0"), is_maker=True,
                timestamp=datetime.now(timezone.utc), grid_level=None,
            )
        sess._broker_adapter.place_limit_maker = AsyncMock(side_effect=[
            _trade("us", MarketType.SPOT, Side.SELL, "76500"),
            _trade("up", MarketType.PERP, Side.SELL, "76500"),
            _trade("ls", MarketType.SPOT, Side.BUY,  "75500"),
            _trade("lp", MarketType.PERP, Side.BUY,  "75500"),
        ])

        await sess._maintain_resting_pairs(_mk_market())

        sess._broker_adapter.cancel_order_by_market.assert_called()
        # paired_inverse: 2 对 × 2 腿 = 4 张单
        assert sess._broker_adapter.place_limit_maker.call_count == 4


# ============================================================
# _place_pair: 单腿 reject 时回滚
# ============================================================

class TestPlacePairBothMaker:
    """paired_inverse: 上穿双 SELL / 下穿双 BUY, 两腿都 LIMIT_MAKER."""

    @pytest.mark.asyncio
    async def test_both_legs_placed_upper_sell(self) -> None:
        sess = _mk_session(live_mode=True, pre_place=True)
        sess._broker_adapter.place_limit_maker = AsyncMock(side_effect=[
            Trade(trade_id="ts", order_id="spot_ok", symbol="BTC/USDT",
                  market=MarketType.SPOT, side=Side.SELL,
                  price=Decimal("77321"), quantity=Decimal("0"),
                  fee=Decimal("0"), is_maker=True,
                  timestamp=datetime.now(timezone.utc), grid_level=None),
            Trade(trade_id="tp", order_id="perp_ok", symbol="BTC/USDT:USDT",
                  market=MarketType.PERP, side=Side.SELL,
                  price=Decimal("77321"), quantity=Decimal("0"),
                  fee=Decimal("0"), is_maker=True,
                  timestamp=datetime.now(timezone.utc), grid_level=None),
        ])

        await sess._place_pair(
            spot_level=Decimal("77321"), perp_level=Decimal("77321"),
            qty=Decimal("0.001"),
            spot_side=Side.SELL, perp_side=Side.SELL,
            market_state=_mk_market(),
        )

        assert sess._broker_adapter.place_limit_maker.call_count == 2
        assert sess.inflight_manager.count(MarketType.SPOT) == 1
        assert sess.inflight_manager.count(MarketType.PERP) == 1

    @pytest.mark.asyncio
    async def test_spot_reject_no_perp_attempt(self) -> None:
        from app.strategies.dgr_btc.broker_adapter import RejectError
        sess = _mk_session(live_mode=True, pre_place=True)
        sess._broker_adapter.place_limit_maker = AsyncMock(side_effect=RejectError("crossed"))

        await sess._place_pair(
            spot_level=Decimal("76500"), perp_level=Decimal("76500"),
            qty=Decimal("0.001"),
            spot_side=Side.SELL, perp_side=Side.SELL,
            market_state=_mk_market(),
        )

        assert sess.inflight_manager.count(MarketType.SPOT) == 0
        assert sess.inflight_manager.count(MarketType.PERP) == 0

    @pytest.mark.asyncio
    async def test_perp_reject_cancels_spot(self) -> None:
        from app.strategies.dgr_btc.broker_adapter import RejectError
        sess = _mk_session(live_mode=True, pre_place=True)
        sess._broker_adapter.place_limit_maker = AsyncMock(side_effect=[
            Trade(trade_id="ts", order_id="spot_ok", symbol="BTC/USDT",
                  market=MarketType.SPOT, side=Side.SELL,
                  price=Decimal("77321"), quantity=Decimal("0"),
                  fee=Decimal("0"), is_maker=True,
                  timestamp=datetime.now(timezone.utc), grid_level=None),
            RejectError("post_only_crossed"),
        ])
        sess._broker_adapter.cancel_order_by_market = AsyncMock(return_value=True)

        await sess._place_pair(
            spot_level=Decimal("77321"), perp_level=Decimal("77321"),
            qty=Decimal("0.001"),
            spot_side=Side.SELL, perp_side=Side.SELL,
            market_state=_mk_market(),
        )

        sess._broker_adapter.cancel_order_by_market.assert_called_once_with(
            "spot_ok", MarketType.SPOT,
        )
        assert sess.inflight_manager.count(MarketType.SPOT) == 0
        assert sess.inflight_manager.count(MarketType.PERP) == 0
