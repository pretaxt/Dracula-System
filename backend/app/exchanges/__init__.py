"""交易所适配层公共入口

快速导入:
  from app.exchanges import ExchangeAdapter, BinanceAdapter
  from app.exchanges.models import Symbol, FundingRate, Side, OrderType
  from app.exchanges.errors import ExchangeError, NetworkError, AuthError
"""
from app.exchanges.base import ExchangeAdapter
from app.exchanges.cex import BinanceAdapter
from app.exchanges.errors import (
    AuthError,
    DataError,
    ExchangeError,
    ExchangeMaintenanceError,
    InsufficientBalanceError,
    NetworkError,
    OrderRejectedError,
    RateLimitError,
    SymbolNotFoundError,
)
from app.exchanges.models import (
    Balance,
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

__all__ = [
    # Base
    "ExchangeAdapter",
    # CEX
    "BinanceAdapter",
    # Models
    "Symbol",
    "Ticker",
    "OrderBook",
    "FundingRate",
    "Kline",
    "Order",
    "Position",
    "Balance",
    "Subscription",
    "Side",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "InstrumentType",
    # Errors
    "ExchangeError",
    "NetworkError",
    "RateLimitError",
    "AuthError",
    "InsufficientBalanceError",
    "OrderRejectedError",
    "SymbolNotFoundError",
    "ExchangeMaintenanceError",
    "DataError",
]
