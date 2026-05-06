"""Strategies 请求 / 响应 schema。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator


class StrategyConfig(BaseModel):
    min_apr_pct: str | None = None
    max_position_notional_usd: str | None = None
    max_concurrent_positions: int | None = None
    scan_interval_seconds: float | None = None


class StrategyStatusResponse(BaseModel):
    paper_running: bool
    runner_running: bool
    last_scan_at: datetime | None
    open_positions: int
    current_config: StrategyConfig


class StrategyActionResponse(BaseModel):
    paper_running: bool
    timestamp: datetime


class ConfigPatchRequest(BaseModel):
    min_apr_pct: str | None = None
    max_position_notional_usd: str | None = None
    max_concurrent_positions: int | None = None
    scan_interval_seconds: float | None = None

    @field_validator("min_apr_pct")
    @classmethod
    def validate_apr(cls, v: str | None) -> str | None:
        if v is not None and float(v) < 0:
            raise ValueError("min_apr_pct must be non-negative")
        return v

    @field_validator("scan_interval_seconds")
    @classmethod
    def validate_interval(cls, v: float | None) -> float | None:
        if v is not None and v < 10:
            raise ValueError("scan_interval_seconds must be >= 10")
        return v
