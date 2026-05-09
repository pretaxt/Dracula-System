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
    trading_mode: str = "paper"  # "paper" | "live"，从 settings.trading_mode 透传


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


class SpotPerpOpportunityOut(BaseModel):
    symbol: str
    exchange: str
    spot_price: str
    perp_price: str
    basis_abs: str
    basis_pct: str
    direction: str
    timestamp_ms: int


class SpotPerpOpportunitiesResponse(BaseModel):
    running: bool
    last_scan_at: datetime | None
    # 当前生效的入场门槛（供 UI 渲染"距入场"列）
    entry_pct: str = "0"                # 通用兜底
    entry_pct_premium: str = "0"        # premium 方向独立阈值（0 = 回退 entry_pct）
    entry_pct_discount: str = "0"       # discount 方向独立阈值（0 = 回退 entry_pct）
    data: list[SpotPerpOpportunityOut]


# ---------------------------------------------------------------------------
# funding-rate 实时机会（候选展示，含 passes_entry 标志）
# ---------------------------------------------------------------------------


class FundingRateOpportunityOut(BaseModel):
    symbol: str                         # "BTC/USDT"
    exchange: str                       # "binance" | "okx"
    apr_pct: str                        # 年化费率百分比
    funding_rate: str                   # 单期资金费率
    funding_interval_hours: float       # 通常 8.0
    next_funding_time_ms: int           # 下次结算时间戳
    history_positive_count: int         # 近 N 期正费率次数
    history_total_count: int
    spot_depth_usd: str                 # 现货 ask 深度 (USD)
    perp_depth_usd: str                 # 永续 ask 深度
    passes_entry: bool                  # True = 满足实盘开仓门槛 (APR ≥ min_apr_pct)
    distance_to_entry_pct: str          # max(0, min_apr_pct - apr_pct) — UI "距离入场" 显示


class FundingRateOpportunitiesResponse(BaseModel):
    running: bool
    last_scan_at: datetime | None
    min_apr_pct: str                    # 当前实盘入场门槛（供 UI 显示参考线）
    scan_threshold_apr_pct: str         # 当前候选展示门槛
    data: list[FundingRateOpportunityOut]


# ---------------------------------------------------------------------------
# spot-perp 策略配置（D.1.5 — UI 调阈值用）
# ---------------------------------------------------------------------------


class SpotPerpConfigResponse(BaseModel):
    enabled: bool
    entry_pct: str          # "0.30" 表示 0.30%
    # c — per-direction 阈值（"0" 表示回退用 entry_pct）
    entry_pct_premium: str = "0"
    entry_pct_discount: str = "0"
    exit_pct: str           # "0.03"
    max_hold_hours: str     # "12"
    min_hold_minutes: str = "5"  # 防噪音平仓的最短持仓时长
    # a — 基差扩大止损阈值（"0" 禁用）
    stop_basis_widening_pct: str = "0.50"
    max_concurrent: int
    notional_per_position: str
    direction_filter: str   # "premium" | "discount" | "both"
    scan_threshold_pct: str
    candidate_symbols: list[str]
    exchanges: list[str]
    live_mode: bool         # 当前是否实盘运行（透传）
    session_running: bool = False  # session task 是否在跑（用于 UI 显示启停按钮）


class SpotPerpConfigPatchRequest(BaseModel):
    """所有字段可选，仅传需更新的项。"""
    entry_pct: str | None = None
    entry_pct_premium: str | None = None
    entry_pct_discount: str | None = None
    exit_pct: str | None = None
    max_hold_hours: str | None = None
    min_hold_minutes: str | None = None
    stop_basis_widening_pct: str | None = None
    max_concurrent: int | None = None
    notional_per_position: str | None = None
    direction_filter: str | None = None
    scan_threshold_pct: str | None = None

    @field_validator("entry_pct", "entry_pct_premium", "entry_pct_discount",
                     "exit_pct", "scan_threshold_pct",
                     "max_hold_hours", "min_hold_minutes",
                     "stop_basis_widening_pct",
                     "notional_per_position")
    @classmethod
    def validate_non_negative(cls, v: str | None) -> str | None:
        if v is not None and float(v) < 0:
            raise ValueError("must be non-negative")
        return v

    @field_validator("max_concurrent")
    @classmethod
    def validate_concurrent(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("max_concurrent must be >= 1")
        return v

    @field_validator("direction_filter")
    @classmethod
    def validate_direction(cls, v: str | None) -> str | None:
        if v is not None and v.lower() not in {"premium", "discount", "both"}:
            raise ValueError("direction_filter must be premium|discount|both")
        return v.lower() if v else v
