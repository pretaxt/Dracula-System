"""ExchangeAdapter — 交易所适配器抽象基类

所有 CEX / DEX 适配器必须实现这个接口。
策略层只依赖这个接口,完全不知道底层是哪家交易所。

设计来源: docs/07_exchange_adapters.md §2.1
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Callable, List, Optional

from app.exchanges.models import (
    Balance,
    FundingRate,
    InstrumentType,
    Kline,
    Order,
    OrderBook,
    OrderType,
    Position,
    Side,
    Subscription,
    Symbol,
    Ticker,
    TimeInForce,
)


class ExchangeAdapter(ABC):
    """交易所统一接口

    子类实现所有 abstractmethod。
    非必须的功能(如期权)可以 raise NotImplementedError,但必须显式声明。
    """

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """交易所标识符,小写: 'binance' / 'bybit' / 'okx' ..."""

    @property
    @abstractmethod
    def supported_instruments(self) -> List[InstrumentType]:
        """该适配器支持的合约类型列表"""

    # ------------------------------------------------------------------
    # 行情数据(公开,无需 API Key)
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_ticker(
        self, symbol: Symbol, instrument: InstrumentType = InstrumentType.SPOT
    ) -> Ticker:
        """拉取最新 ticker (bid/ask/last/volume)"""

    @abstractmethod
    async def fetch_orderbook(
        self,
        symbol: Symbol,
        instrument: InstrumentType = InstrumentType.SPOT,
        depth: int = 10,
    ) -> OrderBook:
        """拉取订单簿快照"""

    @abstractmethod
    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        """拉取当期资金费率 (只对 PERPETUAL 有意义)"""

    @abstractmethod
    async def fetch_klines(
        self,
        symbol: Symbol,
        interval: str,
        limit: int = 100,
        instrument: InstrumentType = InstrumentType.SPOT,
    ) -> List[Kline]:
        """拉取 K 线数据

        interval: "1m" / "5m" / "1h" / "4h" / "1d"
        limit: 返回最近 N 根
        """

    # ------------------------------------------------------------------
    # 账户数据(需要 API Key)
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_balance(self) -> Balance:
        """拉取账户余额"""

    @abstractmethod
    async def fetch_positions(self) -> List[Position]:
        """拉取当前持仓列表"""

    @abstractmethod
    async def fetch_open_orders(
        self, symbol: Optional[Symbol] = None
    ) -> List[Order]:
        """拉取当前挂单;symbol=None 返回所有币种"""

    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        """查询单笔订单状态"""

    # ------------------------------------------------------------------
    # 交易操作(需要 API Key + 交易权限)
    # ------------------------------------------------------------------

    @abstractmethod
    async def place_order(
        self,
        symbol: Symbol,
        instrument: InstrumentType,
        side: Side,
        order_type: OrderType,
        size: Decimal,
        price: Optional[Decimal] = None,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        post_only: bool = False,
        client_order_id: Optional[str] = None,
        margin_mode: Optional[str] = None,
        side_effect: Optional[str] = None,
        extra_params: Optional[dict] = None,
    ) -> Order:
        """下单

        返回订单对象(含 order_id 和初始状态)。
        只抛 errors.py 中定义的异常,不暴露 ccxt/SDK 原生异常。

        margin_mode/side_effect (D.2.c): Binance spot 保证金做空支持，
        其他交易所不支持时静默忽略或抛 NotImplementedError。
        """

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        """取消单笔订单;返回是否成功"""

    @abstractmethod
    async def cancel_all_orders(
        self, symbol: Optional[Symbol] = None
    ) -> int:
        """取消所有挂单;返回取消数量"""

    # ------------------------------------------------------------------
    # WebSocket 订阅
    # ------------------------------------------------------------------

    @abstractmethod
    async def subscribe_orderbook(
        self,
        symbol: Symbol,
        instrument: InstrumentType,
        callback: Callable[[OrderBook], None],
    ) -> Subscription:
        """订阅实时订单簿推送"""

    @abstractmethod
    async def subscribe_trades(
        self,
        symbol: Symbol,
        instrument: InstrumentType,
        callback: Callable,
    ) -> Subscription:
        """订阅实时成交推送"""

    @abstractmethod
    async def subscribe_user_data(
        self, callback: Callable
    ) -> Subscription:
        """订阅账户数据推送(订单更新、成交通知、余额变化)"""

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    @abstractmethod
    async def ping(self) -> int:
        """连通性测试;返回往返延迟 (ms)"""

    @abstractmethod
    async def get_server_time(self) -> int:
        """返回交易所服务器时间 (Unix ms)"""

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """释放连接资源(HTTP session、WS 连接等)

        子类按需重写;默认 no-op。
        """
