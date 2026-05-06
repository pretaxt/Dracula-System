"""FundingRates 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OpportunityOut(BaseModel):
    exchange: str
    symbol: str
    funding_rate: str
    apr_pct: str
    next_funding_time: datetime | None
    instrument_type: str
    history_positive: int | None


class OpportunitiesResponse(BaseModel):
    data: list[OpportunityOut]
    snapshot_at: datetime | None


class FundingHistoryOut(BaseModel):
    exchange: str
    symbol: str
    funding_rate: str
    apr_pct: str
    recorded_at: datetime


class FundingHistoryMeta(BaseModel):
    page: int
    page_size: int
    total: int


class FundingHistoryResponse(BaseModel):
    data: list[FundingHistoryOut]
    meta: FundingHistoryMeta
