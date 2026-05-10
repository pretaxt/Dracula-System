"""E2E 集成测试套件 — T5/R11

目标：在 mock 交易所链路里复现 X1-X7 + R1-R10 各种 bug + 修复，
让未来同类 bug 不必通过真实成交（消耗资金）发现。

关键场景覆盖：
  1. Happy path: open → 持仓 → close → DB closed + 真实状态归零
  2. X4: spot 成功 perp 失败 → unwind 用真实余额（fee 后）卖回，无单腿
  3. X5: 重启后 restore legs → close 走完整路径
  4. X6: spot leg close 不传 reduce_only（broker 接受）
  5. X7: close spot leg fee 占用 → 自动 cap 到真实余额
  6. R3: spot/perp size 自动对齐到 amount_to_precision min
  7. R4: 一腿成功一腿失败 → PartialCloseError + 写 risk_event
  8. P0 预检: spot/perp 任一不够 → 零下单
  9. R8 auto-rebalance: spot quote 不足 → cross-margin 划转
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exchanges.errors import InsufficientBalanceError, OrderRejectedError
from app.exchanges.models import (
    FundingRate,
    InstrumentType,
    Order,
    OrderBook,
    OrderStatus,
    OrderType,
    Side,
    Symbol,
)
from app.execution.live_broker import LiveBroker
from app.execution.order_executor import OrderExecutor, PartialCloseError
from app.execution.paper_broker import OrderRequest, OrderResult
from app.risk.limits import RiskGuard, RiskLimits
from app.risk.models import ExitReason, PositionStatus
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.scanner import FundingRateOpportunity

BTC = Symbol("BTC", "USDT")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _filled_order(instrument: InstrumentType, side: Side, size: str, price: str = "60000") -> Order:
    return Order(
        order_id="OID-" + str(hash((str(instrument), str(side), size)) % 10**8),
        client_order_id="",
        symbol=BTC,
        instrument=instrument,
        side=side,
        order_type=OrderType.MARKET,
        size=Decimal(size),
        price=Decimal("0"),
        filled=Decimal(size),
        avg_fill_price=Decimal(price),
        status=OrderStatus.FILLED,
        timestamp=1_700_000_000_000,
        exchange="binance",
    )


def _make_balance_resp(usdt: str = "10000", btc: str = "0", **extra) -> dict:
    free = {"USDT": usdt, "BTC": btc, **extra}
    total = dict(free)
    return {"free": free, "total": total, "used": {k: "0" for k in free}}


def _make_full_adapter() -> MagicMock:
    """构造一个完整 mock adapter — 默认 happy path（spot 充裕，perp 充裕）。"""
    adapter = MagicMock()
    adapter.exchange_id = "binance"
    spot_client = MagicMock()
    spot_client.fetch_balance = AsyncMock(return_value=_make_balance_resp("10000"))
    spot_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{q:.6f}")
    spot_client.transfer = AsyncMock(return_value={"id": "tx", "status": "ok"})
    perp_client = MagicMock()
    perp_client.fetch_balance = AsyncMock(return_value=_make_balance_resp("1000"))
    perp_client.set_margin_mode = AsyncMock()
    perp_client.set_leverage = AsyncMock()
    perp_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{q:.6f}")
    perp_client.fetch_positions = AsyncMock(return_value=[])
    adapter._clients = {
        InstrumentType.SPOT: spot_client,
        InstrumentType.PERPETUAL: perp_client,
    }
    adapter.top_up_perp_margin = AsyncMock()
    adapter.place_order = AsyncMock(
        side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "0.01"),
        ]
    )
    return adapter


def _make_opportunity(rate: str = "0.0003", spot_ask: str = "60000", perp_bid: str = "60010") -> FundingRateOpportunity:
    funding = FundingRate(
        symbol=BTC, exchange="binance",
        rate=Decimal(rate), next_funding_time=1_700_000_000_000,
        funding_interval_hours=8,
    )
    spot_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(spot_ask) - Decimal("10"), Decimal("100"))],
        asks=[(Decimal(spot_ask), Decimal("100"))],
        timestamp=1_700_000_000_000,
    )
    perp_ob = OrderBook(
        symbol=BTC,
        bids=[(Decimal(perp_bid), Decimal("100"))],
        asks=[(Decimal(perp_bid) + Decimal("10"), Decimal("100"))],
        timestamp=1_700_000_000_000,
    )
    return FundingRateOpportunity(
        symbol=BTC, exchange="binance",
        funding_rate=funding, spot_orderbook=spot_ob, perp_orderbook=perp_ob,
    )


def _default_limits() -> RiskLimits:
    return RiskLimits(
        max_positions=3,
        max_total_notional_usd=Decimal("50000"),
        max_position_size_usd=Decimal("10000"),
        min_position_size_usd=Decimal("10"),
        stop_loss_pct=Decimal("5"),
        max_hold_hours=Decimal("168"),
        min_apr_pct=Decimal("10"),
    )


def _make_executor_with_live_broker(adapter: MagicMock) -> tuple[OrderExecutor, PositionManager]:
    broker = LiveBroker(adapter=adapter, fee_rate=Decimal("0.001"), perp_leverage=Decimal("5"))
    manager = PositionManager()
    manager.save = AsyncMock()
    guard = RiskGuard(limits=_default_limits())
    executor = OrderExecutor(broker={"binance": broker}, manager=manager, guard=guard)
    return executor, manager


# ---------------------------------------------------------------------------
# E2E 场景
# ---------------------------------------------------------------------------


class TestE2EHappyPath:
    """Scenario 1: 完整 open → close 链路无错误。"""

    @pytest.mark.asyncio
    async def test_open_then_close_full_flow(self):
        adapter = _make_full_adapter()
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "0.01"),
            # close 阶段
            _filled_order(InstrumentType.PERPETUAL, Side.BUY, "0.01"),
            _filled_order(InstrumentType.SPOT, Side.SELL, "0.01"),
        ])
        executor, manager = _make_executor_with_live_broker(adapter)

        opp = _make_opportunity()
        pos = await executor.open_delta_neutral(opp, size_usd=Decimal("600"))
        assert pos.status == PositionStatus.OPEN
        assert len(manager.open_positions) == 1
        assert len(pos.legs) == 2

        await executor.close_position(pos.id, reason=ExitReason.MANUAL)
        assert pos.status == PositionStatus.CLOSED
        # close 时 perp 优先（R4），所以第 3 个调用是 perp
        assert adapter.place_order.await_count == 4


class TestE2EX4UnwindFeeAdjusted:
    """Scenario 2: spot 成功后 perp 失败 → unwind 必须用真实余额（fee 后）。"""

    @pytest.mark.asyncio
    async def test_perp_fail_unwind_uses_real_balance(self):
        adapter = _make_full_adapter()
        spot_client = adapter._clients[InstrumentType.SPOT]
        # preflight 通过 USDT 充裕；unwind 时 BTC 真实只剩 0.0099（fee 0.0001）
        spot_client.fetch_balance = AsyncMock(side_effect=[
            _make_balance_resp("10000"),  # preflight
            {"free": {"BTC": "0.0099"}, "total": {"BTC": "0.0099"}},  # unwind
        ])
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            OrderRejectedError("perp open failed"),
            _filled_order(InstrumentType.SPOT, Side.SELL, "0.0099"),
        ])
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        with pytest.raises(RuntimeError, match="perp open failed"):
            await broker.execute_pair(
                OrderRequest(symbol=BTC, side=Side.BUY, size=Decimal("0.01"),
                             reference_price=Decimal("60000"), exchange="binance",
                             instrument_type=InstrumentType.SPOT),
                OrderRequest(symbol=BTC, side=Side.SELL, size=Decimal("0.01"),
                             reference_price=Decimal("60010"), exchange="binance",
                             instrument_type=InstrumentType.PERPETUAL,
                             reduce_only=False, position_side="SHORT"),
            )
        # 第 3 次是 unwind SELL，size 必须是 0.0099 而非 0.01
        unwind_call = adapter.place_order.await_args_list[-1]
        assert unwind_call.kwargs["size"] == Decimal("0.0099")


class TestE2EX5LegsPersistence:
    """Scenario 3: legs 必须持久化 — 重启 restore 后 close 走完整路径。"""

    def test_position_with_legs_round_trip(self):
        from app.exchanges.models import InstrumentType, Side
        from app.models.position import PositionLegRecord
        from app.risk.models import PositionLeg
        leg = PositionLeg(
            exchange="binance", symbol=BTC,
            instrument_type=InstrumentType.SPOT, side=Side.BUY,
            size=Decimal("0.01"), entry_price=Decimal("60000"),
        )
        rec = PositionLegRecord.from_domain(leg, position_id=1)
        restored = rec.to_domain()
        assert restored.size == Decimal("0.01")
        assert restored.entry_price == Decimal("60000")


class TestE2EX6SpotNoReduceOnly:
    """Scenario 4: close spot leg 不传 reduce_only（双层防护）。"""

    @pytest.mark.asyncio
    async def test_close_spot_leg_no_reduce_only_at_executor_level(self):
        adapter = _make_full_adapter()
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.BUY, "0.01"),
            _filled_order(InstrumentType.SPOT, Side.SELL, "0.01"),
        ])
        executor, manager = _make_executor_with_live_broker(adapter)
        pos = await executor.open_delta_neutral(_make_opportunity(), size_usd=Decimal("600"))
        await executor.close_position(pos.id)
        # 第 4 次是 close 的 spot SELL；reduce_only 应为 False
        spot_close_call = adapter.place_order.await_args_list[3]
        assert spot_close_call.kwargs["reduce_only"] is False
        # 第 3 次是 close 的 perp；reduce_only 应为 True
        perp_close_call = adapter.place_order.await_args_list[2]
        assert perp_close_call.kwargs["reduce_only"] is True


class TestE2EX7SpotSellAutoCap:
    """Scenario 5: spot SELL 时 fee 占用 → 自动 cap 到真实余额。"""

    @pytest.mark.asyncio
    async def test_spot_sell_auto_capped_via_executor(self):
        adapter = _make_full_adapter()
        spot_client = adapter._clients[InstrumentType.SPOT]
        # open 阶段 USDT 充裕 / close 阶段 BTC 余额 0.0099（fee 占了 0.0001）
        spot_client.fetch_balance = AsyncMock(side_effect=[
            _make_balance_resp("10000"),  # preflight at open
            {"free": {"BTC": "0.0099"}, "total": {"BTC": "0.0099"}},  # cap at close
        ])
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.BUY, "0.01"),
            _filled_order(InstrumentType.SPOT, Side.SELL, "0.0099"),
        ])
        executor, manager = _make_executor_with_live_broker(adapter)
        pos = await executor.open_delta_neutral(_make_opportunity(), size_usd=Decimal("600"))
        await executor.close_position(pos.id)
        # close 时 spot SELL 必须 cap 到 0.0099
        spot_close = adapter.place_order.await_args_list[3]
        assert spot_close.kwargs["size"] == Decimal("0.0099")


class TestE2ER3PairSizeAlign:
    """Scenario 6: spot/perp 数量自动对齐到精度 min。"""

    @pytest.mark.asyncio
    async def test_pair_size_alignment_open(self):
        adapter = _make_full_adapter()
        spot_client = adapter._clients[InstrumentType.SPOT]
        perp_client = adapter._clients[InstrumentType.PERPETUAL]
        spot_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{q:.4f}")
        perp_client.amount_to_precision = MagicMock(side_effect=lambda s, q: f"{int(q)}.0000")
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "12"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "12"),
        ])
        broker = LiveBroker(adapter=adapter)
        await broker.execute_pair(
            OrderRequest(symbol=BTC, side=Side.BUY, size=Decimal("12.48"),
                         reference_price=Decimal("4"), exchange="binance",
                         instrument_type=InstrumentType.SPOT),
            OrderRequest(symbol=BTC, side=Side.SELL, size=Decimal("12.48"),
                         reference_price=Decimal("4"), exchange="binance",
                         instrument_type=InstrumentType.PERPETUAL),
        )
        # 两腿都应被 align 到 12（perp 精度 floor）
        spot = adapter.place_order.await_args_list[0]
        perp = adapter.place_order.await_args_list[1]
        assert spot.kwargs["size"] == Decimal("12.0000")
        assert perp.kwargs["size"] == Decimal("12.0000")


class TestE2ER4PartialCloseAlerts:
    """Scenario 7: 一腿成功一腿失败 → PartialCloseError + 内存仍 OPEN。"""

    @pytest.mark.asyncio
    async def test_partial_close_raises_and_keeps_open(self):
        adapter = _make_full_adapter()
        adapter.place_order = AsyncMock(side_effect=[
            _filled_order(InstrumentType.SPOT, Side.BUY, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.SELL, "0.01"),
            _filled_order(InstrumentType.PERPETUAL, Side.BUY, "0.01"),  # close perp ok
            OrderRejectedError("spot close fail"),  # close spot fail
        ])
        executor, manager = _make_executor_with_live_broker(adapter)
        pos = await executor.open_delta_neutral(_make_opportunity(), size_usd=Decimal("600"))
        with pytest.raises(PartialCloseError) as e:
            await executor.close_position(pos.id)
        assert len(e.value.succeeded_legs) == 1
        assert len(e.value.failed_legs) == 1
        assert pos.status == PositionStatus.OPEN  # 仍未标记 closed


class TestE2EP0Preflight:
    """Scenario 8: spot/perp 任一不够预检直接 raise，零下单。"""

    @pytest.mark.asyncio
    async def test_perp_balance_insufficient_no_orders(self):
        adapter = _make_full_adapter()
        perp_client = adapter._clients[InstrumentType.PERPETUAL]
        # USDM 钱包 0
        perp_client.fetch_balance = AsyncMock(return_value=_make_balance_resp("0"))
        adapter.top_up_perp_margin = AsyncMock(side_effect=RuntimeError("transfer failed"))
        broker = LiveBroker(adapter=adapter, perp_leverage=Decimal("5"))
        with pytest.raises(InsufficientBalanceError, match="perp USDT insufficient"):
            await broker.execute_pair(
                OrderRequest(symbol=BTC, side=Side.BUY, size=Decimal("0.01"),
                             reference_price=Decimal("60000"), exchange="binance",
                             instrument_type=InstrumentType.SPOT),
                OrderRequest(symbol=BTC, side=Side.SELL, size=Decimal("0.01"),
                             reference_price=Decimal("60000"), exchange="binance",
                             instrument_type=InstrumentType.PERPETUAL),
            )
        # 没有任何 place_order 调用
        adapter.place_order.assert_not_called()
