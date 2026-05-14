"""Dashboard 响应 schema。"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel


class PnlPoint(BaseModel):
    date: date
    net_pnl_usd: str


class StrategyPerf(BaseModel):
    instance: str
    label: str
    realized_pnl: str
    unrealized_pnl: str
    total_pnl: str
    open_positions: int
    closed_positions: int


class DashboardSummary(BaseModel):
    net_pnl_usd: str
    realized_pnl_usd: str
    unrealized_pnl_usd: str
    today_funding_usd: str
    today_pnl_usd: str = "0"
    today_realized_usd: str = "0"
    today_unrealized_usd: str = "0"
    monthly_pnl_usd: str
    daily_drawdown_pct: str
    weekly_dd_pct: str
    margin_usage_pct: str
    api_error_rate_5m_pct: str
    ws_stability_pct: str
    total_equity_usd: str
    open_positions: int
    avg_apr_pct: str
    pnl_series_30d: list[PnlPoint]
    strategy_performance: list[StrategyPerf] = []
    # 每个交易所的 USD 等值（来自 balance_service.get_per_exchange_equity）
    # 形如 {"binance": "102.39", "okx": "0.00"}；缺数据时为空 dict
    equity_by_exchange: dict[str, str] = {}
    # 30 天年化 Sharpe（基于 pnl_series_30d / total_equity 算日 return）
    sharpe_30d: str = "0"
    # 单交易所最大占比 % (max(equity_by_exchange) / total_equity * 100)
    max_exchange_concentration_pct: str = "0"
    # 单币种最大占比 % (max symbol open notional / total open notional * 100)
    max_symbol_concentration_pct: str = "0"
    # 运维指标（5min 滑窗 in-memory）
    api_latency_p95_ms: str = "0"
    # {strategy: {p95_ms, count_5m}}
    scan_perf: dict = {}
    # {exchange: {calls_5m, error_rate_pct}}
    ccxt_health: dict = {}
    # Market Data Hub 健康（v0.4.5 跨策略共享行情缓存）
    # {exchange: {ticker_count, funding_count, ticker_age_s, funding_age_s, consecutive_failures}}
    market_data_hub: dict = {}
