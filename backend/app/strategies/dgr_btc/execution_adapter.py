"""
dgr_btc/execution_adapter.py — ExecutionAdapter abstraction (§6.2 #1)
====================================================================

Pre-revamp: paper_trading.py 内有两个 _process_decisions 分支
  - sync paper synthetic fill
  - async LIVE broker call (place_limit_maker / place_market_unwind)
Mode 分支散在 session 各处, 难审计 + 不可扩展 (回测引擎要第三套 fill 路径).

Revamp: 抽 ExecutionAdapter 抽象接口, 三个实现:
  - PaperBroker    — 合成 fill (paper session)
  - LiveBroker     — 调真实 binance broker_adapter (LIVE session)
  - BacktestBroker — 回测引擎用 (sync helper 暴露给 backtest_runner)

Session 只持 broker, _process_decisions 单分支 (await broker.place_buy(...)).
失败统一 raise BrokerError → caller fail-closed (不 apply_fill, 下 tick 重试).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.strategies.dgr_btc.engine import MartingaleEngine


_ZERO = Decimal("0")
_ONE = Decimal("1")


@dataclass(frozen=True)
class FillResult:
    """统一 fill 返回结构.

    BUY 语义:
      price = 实际成交价
      qty = 实际成交量
      cost_or_proceeds = 实际花掉的 stake (含 buy fee)
    SELL (TP / SL / unwind) 语义:
      price = 实际成交价
      qty = 实际成交量
      cost_or_proceeds = 实际收到的 proceeds (扣完 sell fee)
    broker_order_id: LIVE 真实订单 id; paper/backtest = None.
    """
    price: Decimal
    qty: Decimal
    cost_or_proceeds: Decimal
    broker_order_id: Optional[str] = None


class BrokerError(Exception):
    """Broker rejected / IO failed / 单未成交.

    Fail-closed contract: caller 收到必须 break 出 decision loop,
    不调 engine.apply_fill, 等下一 tick 重试.
    """

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message)
        self.original = original


class ExecutionAdapter(ABC):
    """统一执行接口.

    实现注意:
      - place_buy 接受 stake_usdt, 返回 FillResult.cost_or_proceeds 应为实际花费
        (LIVE: fill_px × qty × (1 + fee), paper/backtest: 同 stake_usdt 因 engine
        compute_fill_qty_buy 已折 fee 进 effective price)
      - place_unwind_sell 接受 qty, 返回 FillResult.cost_or_proceeds 应为实际收入
        (扣完 sell fee)
      - 任何失败 raise BrokerError (不返回 None / 不返回 0)
    """

    @abstractmethod
    async def place_buy(
        self,
        layer_index: int,
        target_price: Decimal,
        stake_usdt: Decimal,
    ) -> FillResult:
        """挂一档 BUY. 失败 raise BrokerError."""

    @abstractmethod
    async def place_unwind_sell(
        self,
        qty: Decimal,
        target_price: Decimal,
    ) -> FillResult:
        """全仓 SELL (TP / SL / 紧急平仓). 失败 raise BrokerError."""


# ─────────────────────── Backtest / Paper synthetic ───────────────────────


class BacktestBroker(ExecutionAdapter):
    """合成 fill: 用 MartingaleEngine 的 effective price 计算成交.

    No IO. Never fails. Used by:
      - backtest_runner.py (sync helper 路径)
      - paper session (PaperBroker 直接继承)
    """

    def __init__(self, engine: "MartingaleEngine"):
        self._engine = engine

    # sync helpers (backtest_runner 用 — 避免 sync→async 桥)
    def sync_place_buy(
        self, target_price: Decimal, stake_usdt: Decimal,
    ) -> FillResult:
        fill_px, qty = self._engine.compute_fill_qty_buy(stake_usdt, target_price)
        return FillResult(
            price=fill_px,
            qty=qty,
            cost_or_proceeds=stake_usdt,
            broker_order_id=None,
        )

    def sync_place_unwind_sell(
        self, qty: Decimal, target_price: Decimal,
    ) -> FillResult:
        proceeds = self._engine.compute_proceeds_sell(qty, target_price)
        return FillResult(
            price=target_price,
            qty=qty,
            cost_or_proceeds=proceeds,
            broker_order_id=None,
        )

    # async 接口 (paper session 用)
    async def place_buy(
        self,
        layer_index: int,
        target_price: Decimal,
        stake_usdt: Decimal,
    ) -> FillResult:
        return self.sync_place_buy(target_price, stake_usdt)

    async def place_unwind_sell(
        self,
        qty: Decimal,
        target_price: Decimal,
    ) -> FillResult:
        return self.sync_place_unwind_sell(qty, target_price)


class PaperBroker(BacktestBroker):
    """Paper session 用. 数学行为与 BacktestBroker 完全等价 (语义区分).

    保持 mirror_equivalence: paper ≡ backtest 来自共用同一 engine compute.
    """


# ─────────────────────────── LIVE broker wrap ───────────────────────────


class LiveBroker(ExecutionAdapter):
    """包 dgr_btc broker_adapter (binance ccxt 真实下单).

    BUY 路径: place_limit_maker(SPOT, BUY, target_price, est_qty, no_wait=False)
              等成交 ≤ 5s, 单未成交 raise BrokerError.
    SELL 路径: place_market_unwind(SPOT, SELL, qty)
               taker 紧急平腿; 失败 raise BrokerError.

    fee_pct 来自 EngineConfig.fee_pct, 用于把 broker 返回的真实 fill_price + qty
    折成 actual_stake / actual_proceeds (与 paper / backtest 同口径).
    """

    def __init__(self, broker_adapter: Any, engine_fee_pct: Decimal):
        self._adapter = broker_adapter
        self._fee_pct = engine_fee_pct

    async def place_buy(
        self,
        layer_index: int,
        target_price: Decimal,
        stake_usdt: Decimal,
    ) -> FillResult:
        from app.strategies.dgr_btc.types import MarketType, Side  # noqa: PLC0415

        if target_price <= _ZERO:
            raise BrokerError(f"invalid_target_price={target_price}")
        est_qty = stake_usdt / target_price
        try:
            trade = await self._adapter.place_limit_maker(
                MarketType.SPOT,
                Side.BUY,
                price=target_price,
                quantity=est_qty,
                no_wait=False,
            )
        except Exception as e:
            raise BrokerError(
                f"buy_reject layer={layer_index} target={target_price}: {str(e)[:120]}",
                original=e,
            ) from e

        fill_px = Decimal(str(getattr(trade, "price", target_price) or target_price))
        qty = Decimal(str(getattr(trade, "quantity", est_qty) or est_qty))
        if qty <= _ZERO or fill_px <= _ZERO:
            raise BrokerError(
                f"buy_zero_fill layer={layer_index} fill_px={fill_px} qty={qty}",
            )
        actual_stake = fill_px * qty * (_ONE + self._fee_pct)
        oid = (
            getattr(trade, "id", None)
            or getattr(trade, "order_id", None)
            or getattr(trade, "trade_id", None)
            or ""
        )
        return FillResult(
            price=fill_px,
            qty=qty,
            cost_or_proceeds=actual_stake,
            broker_order_id=str(oid) if oid else None,
        )

    async def place_unwind_sell(
        self,
        qty: Decimal,
        target_price: Decimal,
    ) -> FillResult:
        from app.strategies.dgr_btc.types import MarketType, Side  # noqa: PLC0415

        if qty <= _ZERO:
            raise BrokerError(f"invalid_sell_qty={qty}")
        try:
            trade = await self._adapter.place_market_unwind(
                MarketType.SPOT,
                Side.SELL,
                quantity=qty,
            )
        except Exception as e:
            raise BrokerError(
                f"sell_fail qty={qty}: {str(e)[:120]}",
                original=e,
            ) from e

        fill_px = Decimal(str(getattr(trade, "price", target_price) or target_price))
        actual_qty = Decimal(str(getattr(trade, "quantity", qty) or qty))
        if actual_qty <= _ZERO or fill_px <= _ZERO:
            raise BrokerError(
                f"sell_zero_fill fill_px={fill_px} qty={actual_qty}",
            )
        proceeds = fill_px * actual_qty * (_ONE - self._fee_pct)
        oid = (
            getattr(trade, "id", None)
            or getattr(trade, "order_id", None)
            or getattr(trade, "trade_id", None)
            or ""
        )
        return FillResult(
            price=fill_px,
            qty=actual_qty,
            cost_or_proceeds=proceeds,
            broker_order_id=str(oid) if oid else None,
        )
