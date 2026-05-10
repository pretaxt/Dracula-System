"""System 路由响应 schema — 交易所健康 / 活动流。"""
from __future__ import annotations

from pydantic import BaseModel


class ExchangeHealthOut(BaseModel):
    name: str
    status: str  # active | warn | critical | unconfigured | no_credentials
    ping_ms: int | None = None
    has_credentials: bool = False  # 是否配了 trading API key（决定能否 fetch_balance/下单）


class ExchangeHealthResponse(BaseModel):
    data: list[ExchangeHealthOut]


class ActivityOut(BaseModel):
    icon: str  # up | check | warn | zap | x
    text: str
    time: str


class ActivityResponse(BaseModel):
    data: list[ActivityOut]
