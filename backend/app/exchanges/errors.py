"""交易所错误层级

设计原则:
  - 所有适配器只抛这里定义的异常,不把 ccxt/SDK 原生异常暴露给策略层
  - 策略层和风控引擎按类型判断处理方式(是否重试、是否熔断)
  - 错误携带原始 exchange 和 symbol 信息,方便日志溯源

错误传播规则 (来自 docs/07_exchange_adapters.md §8.2):
  NetworkError              → 可重试 3 次;累计触发 Tier-3 熔断
  RateLimitError            → 等待后重试;不触发风控
  AuthError                 → 不重试;立即停止该交易所
  InsufficientBalanceError  → 不重试;通知策略调整仓位
  OrderRejectedError        → 不重试;记录日志,可修正后重试
  ExchangeMaintenanceError  → 5 分钟后重试;记录并通知
"""
from __future__ import annotations

from typing import Optional


class ExchangeError(Exception):
    """所有适配器错误的基类"""

    def __init__(
        self,
        message: str,
        *,
        exchange: str = "",
        symbol: str = "",
        raw: Optional[Exception] = None,
    ) -> None:
        super().__init__(message)
        self.exchange = exchange
        self.symbol = symbol
        self.raw = raw   # 原始异常,用于调试

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.exchange:
            parts.append(f"exchange={self.exchange!r}")
        if self.symbol:
            parts.append(f"symbol={self.symbol!r}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# 可重试类
# ---------------------------------------------------------------------------


class NetworkError(ExchangeError):
    """网络层问题(连接超时、读超时、DNS 失败等)

    策略:重试最多 3 次,指数退避 (0.5s / 1s / 2s)
    风控:5 分钟内累计 3 次 → Tier-3 熔断
    """


class RateLimitError(ExchangeError):
    """请求频率超过交易所限制

    策略:等待 retry_after 秒后重试
    风控:不触发风控
    """

    def __init__(
        self,
        message: str,
        *,
        exchange: str = "",
        symbol: str = "",
        raw: Optional[Exception] = None,
        retry_after: float = 5.0,
    ) -> None:
        super().__init__(message, exchange=exchange, symbol=symbol, raw=raw)
        self.retry_after = retry_after


class ExchangeMaintenanceError(ExchangeError):
    """交易所维护中 (503 / 维护公告)

    策略:5 分钟后重试
    风控:记录并通知
    """


# ---------------------------------------------------------------------------
# 不可重试类
# ---------------------------------------------------------------------------


class AuthError(ExchangeError):
    """API Key/Secret 认证失败,或权限不足

    策略:不重试
    风控:立即停止该交易所的所有操作,通知用户轮换密钥
    """


class InsufficientBalanceError(ExchangeError):
    """账户余额不足以完成操作

    策略:不重试
    风控:通知策略层调整仓位大小
    """

    def __init__(
        self,
        message: str,
        *,
        exchange: str = "",
        symbol: str = "",
        raw: Optional[Exception] = None,
        required: Optional[str] = None,
        available: Optional[str] = None,
    ) -> None:
        super().__init__(message, exchange=exchange, symbol=symbol, raw=raw)
        self.required = required
        self.available = available


class OrderRejectedError(ExchangeError):
    """订单被交易所拒绝(参数错误、最小下单量、价格偏离过大等)

    策略:不重试(需要修正参数后才能重试)
    风控:记录日志
    """


class SymbolNotFoundError(ExchangeError):
    """币种在该交易所不存在或已下架

    策略:不重试
    风控:触发相关持仓的紧急处理流程
    """


class DataError(ExchangeError):
    """交易所返回了格式异常或缺失的数据

    通常是 API 版本变更或临时故障,可视情况重试
    """
