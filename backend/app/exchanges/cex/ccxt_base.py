"""CCXTAdapter — 所有 CEX 共享的 CCXT 包装层

职责:
  1. 把 CCXT 返回的原始 dict 转换成统一数据模型 (models.py)
  2. 把 ccxt 异常映射到统一错误类型 (errors.py)
  3. 在每次 REST 调用前通过 TokenBucketRateLimiter 限速
  4. 内置指数退避重试 (NetworkError / RateLimitError)

子类 (BinanceAdapter 等) 只需覆盖:
  - 初始化多个 ccxt client (如 spot / usdm / coinm)
  - 按 instrument 路由到正确的 client
  - 交易所特有的字段差异处理

设计来源: docs/07_exchange_adapters.md §3.2
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

import ccxt.async_support as ccxt

from app.exchanges.base import ExchangeAdapter
from app.exchanges.errors import (
    AuthError,
    DataError,
    ExchangeMaintenanceError,
    InsufficientBalanceError,
    NetworkError,
    OrderRejectedError,
    RateLimitError,
    SymbolNotFoundError,
)
from app.exchanges.models import (
    Balance,
    BalanceEntry,
    FundingRate,
    InstrumentType,
    Kline,
    Order,
    OrderBook,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Subscription,
    Symbol,
    Ticker,
    TimeInForce,
)
from app.exchanges.rate_limiter import TokenBucketRateLimiter, get_global_limiter
from app.core.logging import get_logger

logger = get_logger(__name__)

# 重试配置
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.5   # seconds


def _to_decimal(value: Any) -> Decimal:
    """安全地将任意数值转为 Decimal,避免浮点精度问题"""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _map_order_status(ccxt_status: Optional[str]) -> OrderStatus:
    mapping: Dict[str, OrderStatus] = {
        "open": OrderStatus.OPEN,
        "closed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
        "rejected": OrderStatus.REJECTED,
        "expired": OrderStatus.EXPIRED,
        "partially_filled": OrderStatus.PARTIAL,
    }
    return mapping.get(ccxt_status or "", OrderStatus.PENDING)


def _map_side(ccxt_side: str) -> Side:
    return Side.BUY if ccxt_side.lower() == "buy" else Side.SELL


def _map_order_type(ccxt_type: str) -> OrderType:
    mapping: Dict[str, OrderType] = {
        "market": OrderType.MARKET,
        "limit": OrderType.LIMIT,
        "stop_market": OrderType.STOP_MARKET,
        "stop_limit": OrderType.STOP_LIMIT,
    }
    return mapping.get(ccxt_type.lower(), OrderType.LIMIT)


class CCXTAdapter(ExchangeAdapter):
    """所有 CEX 共享的 CCXT 包装

    子类必须在 __init__ 中:
      1. 创建 self._clients: Dict[InstrumentType, ccxt.Exchange]
      2. 设置 self._exchange_id (小写交易所名)
      3. 调用 super().__init__() 初始化 rate limiter
    """

    def __init__(
        self,
        exchange_id: str,
        api_key: str,
        api_secret: str,
        max_rpm: int = 600,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._exchange_id = exchange_id
        self._clients: Dict[InstrumentType, ccxt.Exchange] = {}
        # v0.4.3 改用跨策略全局共享池 — 12 策略共用同一桶，永不超 exchange limit
        # 旧字段 _rate_limiter 保留兼容外部读取（指向同一全局实例）
        self._rate_limiter = get_global_limiter(exchange_id, default_rpm=max_rpm)
        self._api_key = api_key
        self._api_secret = api_secret
        self._extra_params = extra_params or {}

    # ------------------------------------------------------------------
    # ExchangeAdapter metadata
    # ------------------------------------------------------------------

    @property
    def exchange_name(self) -> str:
        return self._exchange_id

    @property
    def supported_instruments(self) -> List[InstrumentType]:
        return list(self._clients.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self, instrument: InstrumentType) -> ccxt.Exchange:
        client = self._clients.get(instrument)
        if client is None:
            raise NotImplementedError(
                f"{self._exchange_id} adapter does not support {instrument.value}"
            )
        return client

    async def _call_with_retry(
        self, coro_fn: Callable, *args: Any, **kwargs: Any
    ) -> Any:
        """带限速 + 指数退避重试的 CCXT 调用包装"""
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            await self._rate_limiter.acquire()
            try:
                return await coro_fn(*args, **kwargs)
            except ccxt.RateLimitExceeded as e:
                retry_after = 5.0
                logger.warning(
                    "rate_limit_exceeded",
                    exchange=self._exchange_id,
                    attempt=attempt + 1,
                    retry_after=retry_after,
                )
                await asyncio.sleep(retry_after)
                last_exc = RateLimitError(
                    str(e),
                    exchange=self._exchange_id,
                    raw=e,
                    retry_after=retry_after,
                )
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "network_error",
                    exchange=self._exchange_id,
                    attempt=attempt + 1,
                    retry_in=delay,
                    error=str(e),
                )
                await asyncio.sleep(delay)
                last_exc = NetworkError(str(e), exchange=self._exchange_id, raw=e)
            except ccxt.AuthenticationError as e:
                raise AuthError(str(e), exchange=self._exchange_id, raw=e) from e
            except ccxt.InsufficientFunds as e:
                raise InsufficientBalanceError(
                    str(e), exchange=self._exchange_id, raw=e
                ) from e
            except ccxt.BadSymbol as e:
                raise SymbolNotFoundError(
                    str(e), exchange=self._exchange_id, raw=e
                ) from e
            except ccxt.InvalidOrder as e:
                raise OrderRejectedError(
                    str(e), exchange=self._exchange_id, raw=e
                ) from e
            except ccxt.OnMaintenance as e:
                raise ExchangeMaintenanceError(
                    str(e), exchange=self._exchange_id, raw=e
                ) from e
            except ccxt.ExchangeError as e:
                raise OrderRejectedError(
                    str(e), exchange=self._exchange_id, raw=e
                ) from e

        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def fetch_ticker(
        self, symbol: Symbol, instrument: InstrumentType = InstrumentType.SPOT
    ) -> Ticker:
        client = self._client(instrument)
        raw = await self._call_with_retry(
            client.fetch_ticker, symbol.to_ccxt()
        )
        return Ticker(
            symbol=symbol,
            bid=_to_decimal(raw.get("bid")),
            ask=_to_decimal(raw.get("ask")),
            last=_to_decimal(raw.get("last")),
            volume_24h=_to_decimal(raw.get("quoteVolume")),
            timestamp=int(raw.get("timestamp") or 0),
        )

    async def fetch_orderbook(
        self,
        symbol: Symbol,
        instrument: InstrumentType = InstrumentType.SPOT,
        depth: int = 10,
    ) -> OrderBook:
        client = self._client(instrument)
        raw = await self._call_with_retry(
            client.fetch_order_book, symbol.to_ccxt(), depth
        )
        bids = [
            (_to_decimal(p), _to_decimal(q)) for p, q in (raw.get("bids") or [])
        ]
        asks = [
            (_to_decimal(p), _to_decimal(q)) for p, q in (raw.get("asks") or [])
        ]
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=int(raw.get("timestamp") or 0),
        )

    async def fetch_klines(
        self,
        symbol: Symbol,
        interval: str,
        limit: int = 100,
        instrument: InstrumentType = InstrumentType.SPOT,
    ) -> List[Kline]:
        client = self._client(instrument)
        raw_list = await self._call_with_retry(
            client.fetch_ohlcv, symbol.to_ccxt(), interval, None, limit
        )
        result = []
        for raw in (raw_list or []):
            # CCXT OHLCV: [timestamp, open, high, low, close, volume]
            if len(raw) < 6:
                continue
            result.append(Kline(
                symbol=symbol,
                interval=interval,
                open=_to_decimal(raw[1]),
                high=_to_decimal(raw[2]),
                low=_to_decimal(raw[3]),
                close=_to_decimal(raw[4]),
                volume=_to_decimal(raw[5]),
                timestamp=int(raw[0]),
            ))
        return result

    async def fetch_funding_rate(self, symbol: Symbol) -> FundingRate:
        # 子类 (BinanceAdapter) 覆盖此方法,使用 usdm client
        raise NotImplementedError(
            f"{self._exchange_id}.fetch_funding_rate must be overridden"
        )

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    async def fetch_balance(self) -> Balance:
        client = self._client(InstrumentType.SPOT)
        raw = await self._call_with_retry(client.fetch_balance)
        entries = []
        for asset, data in (raw.get("total") or {}).items():
            if data is None:
                continue
            free_val = _to_decimal((raw.get("free") or {}).get(asset))
            locked_val = _to_decimal((raw.get("used") or {}).get(asset))
            entries.append(BalanceEntry(
                asset=asset.upper(),
                free=free_val,
                locked=locked_val,
            ))
        return Balance(
            entries=entries,
            timestamp=int(time.time() * 1000),
        )

    async def fetch_positions(self) -> List[Position]:
        raise NotImplementedError(
            f"{self._exchange_id}.fetch_positions must be overridden by subclass"
        )

    async def fetch_open_orders(
        self, symbol: Optional[Symbol] = None
    ) -> List[Order]:
        raise NotImplementedError(
            f"{self._exchange_id}.fetch_open_orders must be overridden"
        )

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Order:
        raise NotImplementedError(
            f"{self._exchange_id}.fetch_order must be overridden"
        )

    # ------------------------------------------------------------------
    # Trading
    # ------------------------------------------------------------------

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
        position_side: Optional[str] = None,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Order:
        """下单。

        Parameters
        ----------
        margin_mode:
            'cross' / 'isolated' — 现货保证金模式（仅 instrument=SPOT 时有效，
            用于做空：先借币再卖出）。
        side_effect:
            Binance 现货保证金特有：'MARGIN_BUY' 借币并交易；'AUTO_REPAY' 交易并还币。
        extra_params:
            其他直传 ccxt 的 params（高级用法，调用方自己保证 key 正确）。
        """
        client = self._client(instrument)
        params: Dict[str, Any] = {}
        # MARKET 订单不需要 timeInForce（Binance margin 严格校验报 -1106）
        if order_type != OrderType.MARKET:
            params["timeInForce"] = time_in_force.value
        if reduce_only:
            params["reduceOnly"] = True
        if post_only:
            params["postOnly"] = True
        if client_order_id:
            params["clientOrderId"] = client_order_id
        if margin_mode:
            params["marginMode"] = margin_mode
            # OKX UTA 还需显式 tdMode（CCXT 部分版本不会自动从 marginMode 翻译）
            if self._exchange_id == "okx":
                params["tdMode"] = margin_mode
        if side_effect:
            # sideEffectType 是 Binance 现货保证金特有；OKX UTA cross-margin 自动借/还，无此概念
            if self._exchange_id == "binance":
                params["sideEffectType"] = side_effect
        if position_side:
            # Binance Hedge 模式必填；One-way 模式忽略此字段
            params["positionSide"] = position_side.upper()
            # reduceOnly 不能与 hedge 模式同时存在（hedge 用 positionSide 区分）
            params.pop("reduceOnly", None)
        if extra_params:
            params.update(extra_params)

        raw = await self._call_with_retry(
            client.create_order,
            symbol.to_ccxt(),
            order_type.value,
            side.value,
            float(size),
            float(price) if price is not None else None,
            params,
        )
        return self._raw_to_order(raw, symbol, instrument)

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        raise NotImplementedError(
            f"{self._exchange_id}.cancel_order must be overridden"
        )

    async def cancel_all_orders(
        self, symbol: Optional[Symbol] = None
    ) -> int:
        raise NotImplementedError(
            f"{self._exchange_id}.cancel_all_orders must be overridden"
        )

    # ------------------------------------------------------------------
    # WebSocket (stub — subclass implements)
    # ------------------------------------------------------------------

    async def subscribe_orderbook(
        self,
        symbol: Symbol,
        instrument: InstrumentType,
        callback: Callable[[OrderBook], None],
    ) -> Subscription:
        raise NotImplementedError(
            f"{self._exchange_id} WebSocket not implemented yet"
        )

    async def subscribe_trades(
        self,
        symbol: Symbol,
        instrument: InstrumentType,
        callback: Callable,
    ) -> Subscription:
        raise NotImplementedError(
            f"{self._exchange_id} WebSocket not implemented yet"
        )

    async def subscribe_user_data(self, callback: Callable) -> Subscription:
        raise NotImplementedError(
            f"{self._exchange_id} WebSocket not implemented yet"
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def ping(self) -> int:
        client = next(iter(self._clients.values()))
        start = time.monotonic()
        await self._call_with_retry(client.fetch_time)
        return round((time.monotonic() - start) * 1000)

    async def get_server_time(self) -> int:
        client = next(iter(self._clients.values()))
        raw = await self._call_with_retry(client.fetch_time)
        return int(raw)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()

    # ------------------------------------------------------------------
    # Internal converters
    # ------------------------------------------------------------------

    def _raw_to_order(
        self,
        raw: Dict[str, Any],
        symbol: Symbol,
        instrument: InstrumentType,
    ) -> Order:
        if not raw:
            raise DataError(
                "Empty order response", exchange=self._exchange_id
            )
        return Order(
            order_id=str(raw.get("id") or ""),
            client_order_id=str(raw.get("clientOrderId") or ""),
            symbol=symbol,
            instrument=instrument,
            side=_map_side(raw.get("side") or "buy"),
            order_type=_map_order_type(raw.get("type") or "limit"),
            size=_to_decimal(raw.get("amount")),
            price=_to_decimal(raw.get("price")),
            filled=_to_decimal(raw.get("filled")),
            avg_fill_price=_to_decimal(raw.get("average")),
            status=_map_order_status(raw.get("status")),
            timestamp=int(raw.get("timestamp") or 0),
            exchange=self._exchange_id,
        )
