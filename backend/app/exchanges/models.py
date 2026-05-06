"""统一数据模型

所有交易所适配器使用这些模型作为输入/输出,策略层完全不感知底层交易所差异。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class InstrumentType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    QUARTERLY = "quarterly"
    OPTION = "option"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    def opposite(self) -> "Side":
        return Side.SELL if self == Side.BUY else Side.BUY


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    GTC = "GTC"   # Good Till Cancelled
    IOC = "IOC"   # Immediate or Cancel
    FOK = "FOK"   # Fill or Kill
    GTX = "GTX"   # Post-only (Good Till Crossing)


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    """币种对,与交易所无关的标准表示。

    base:  "BTC"
    quote: "USDT"
    """

    base: str
    quote: str

    def __str__(self) -> str:
        return f"{self.base}/{self.quote}"

    def to_ccxt(self) -> str:
        """CCXT 统一格式: BTC/USDT"""
        return f"{self.base}/{self.quote}"

    def to_binance_spot(self) -> str:
        """Binance 现货格式: BTCUSDT"""
        return f"{self.base}{self.quote}"

    def to_binance_perp(self) -> str:
        """Binance 永续格式: BTCUSDT (与现货相同,由 client 区分)"""
        return f"{self.base}{self.quote}"

    @classmethod
    def from_ccxt(cls, ccxt_symbol: str) -> "Symbol":
        """从 CCXT 格式解析: 'BTC/USDT' -> Symbol('BTC', 'USDT')"""
        # CCXT perp symbols like "BTC/USDT:USDT" — strip settlement suffix
        base_part = ccxt_symbol.split(":")[0]
        parts = base_part.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid CCXT symbol: {ccxt_symbol!r}")
        return cls(base=parts[0].upper(), quote=parts[1].upper())


# ---------------------------------------------------------------------------
# Market data models
# ---------------------------------------------------------------------------


@dataclass
class Ticker:
    symbol: Symbol
    bid: Decimal
    ask: Decimal
    last: Decimal
    volume_24h: Decimal        # quote 计价的 24h 成交量
    timestamp: int             # Unix ms

    @property
    def spread_bps(self) -> Decimal:
        """买卖价差 (基点)"""
        if self.bid <= 0:
            return Decimal("0")
        return (self.ask - self.bid) / self.bid * Decimal("10000")

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


@dataclass
class OrderBook:
    symbol: Symbol
    bids: List[Tuple[Decimal, Decimal]]   # [(price, size), ...] 降价排列
    asks: List[Tuple[Decimal, Decimal]]   # [(price, size), ...] 升价排列
    timestamp: int                         # Unix ms

    def depth_usd(self, levels: int = 5) -> Tuple[Decimal, Decimal]:
        """前 N 档的买方/卖方总深度 (USD 名义价值)"""
        bid_depth = sum(p * q for p, q in self.bids[:levels])
        ask_depth = sum(p * q for p, q in self.asks[:levels])
        return bid_depth, ask_depth

    def spread_bps(self) -> Decimal:
        if not self.bids or not self.asks:
            return Decimal("9999")
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        if best_bid <= 0:
            return Decimal("9999")
        return (best_ask - best_bid) / best_bid * Decimal("10000")


@dataclass
class FundingRate:
    symbol: Symbol
    exchange: str
    rate: Decimal                    # 当期资金费率,如 0.0001 = 0.01%
    next_funding_time: int           # Unix ms
    funding_interval_hours: int      # 结算间隔: 8 或 1
    predicted_rate: Optional[Decimal] = None   # 预测下期费率(部分交易所提供)

    @property
    def apr(self) -> Decimal:
        """当期资金费率年化 (APR)"""
        periods_per_year = Decimal(str(24 // self.funding_interval_hours * 365))
        return self.rate * periods_per_year

    @property
    def is_positive(self) -> bool:
        return self.rate > Decimal("0")


@dataclass
class Kline:
    symbol: Symbol
    interval: str      # "1m", "5m", "1h", etc.
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal    # base asset volume
    timestamp: int     # 开盘时间 Unix ms


# ---------------------------------------------------------------------------
# Account / trading models
# ---------------------------------------------------------------------------


@dataclass
class BalanceEntry:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass
class Balance:
    entries: List[BalanceEntry] = field(default_factory=list)
    timestamp: int = 0

    def get(self, asset: str) -> Optional[BalanceEntry]:
        asset = asset.upper()
        for e in self.entries:
            if e.asset == asset:
                return e
        return None

    def free(self, asset: str) -> Decimal:
        entry = self.get(asset)
        return entry.free if entry else Decimal("0")


@dataclass
class Order:
    order_id: str
    client_order_id: str
    symbol: Symbol
    instrument: InstrumentType
    side: Side
    order_type: OrderType
    size: Decimal              # 下单数量 (base asset)
    price: Decimal             # 限价单价格; market=0
    filled: Decimal            # 已成交数量
    avg_fill_price: Decimal    # 成交均价
    status: OrderStatus
    timestamp: int             # 创建时间 Unix ms
    exchange: str = ""

    @property
    def remaining(self) -> Decimal:
        return self.size - self.filled

    @property
    def is_done(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )


@dataclass
class Position:
    symbol: Symbol
    instrument: InstrumentType
    side: Side
    size: Decimal              # 持仓数量 (base asset)
    entry_price: Decimal
    mark_price: Decimal
    margin: Decimal            # 占用保证金
    unrealized_pnl: Decimal
    leverage: Decimal
    exchange: str = ""
    liquidation_price: Optional[Decimal] = None

    @property
    def notional(self) -> Decimal:
        return self.size * self.mark_price

    @property
    def margin_ratio_pct(self) -> Decimal:
        """保证金率百分比 (0-100)"""
        if self.notional <= 0:
            return Decimal("100")
        return self.margin / self.notional * Decimal("100")


# ---------------------------------------------------------------------------
# WebSocket / subscription
# ---------------------------------------------------------------------------


@dataclass
class Subscription:
    """WebSocket 订阅句柄,调用 cancel() 取消订阅"""

    exchange: str
    channel: str
    symbol: Optional[Symbol] = None
    _cancel_fn: Optional[object] = field(default=None, repr=False)

    async def cancel(self) -> None:
        if self._cancel_fn is not None:
            await self._cancel_fn()  # type: ignore[operator]
