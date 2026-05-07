"""System 路由响应 schema — 交易所健康 / 活动流。"""
from __future__ import annotations

from pydantic import BaseModel


class ExchangeHealthOut(BaseModel):
    name: str
    status: str  # active | warn | critical | unconfigured
    ping_ms: int | None = None


class ExchangeHealthResponse(BaseModel):
    data: list[ExchangeHealthOut]


class ActivityOut(BaseModel):
    icon: str  # up | check | warn | zap | x
    text: str
    time: str


class ActivityResponse(BaseModel):
    data: list[ActivityOut]
