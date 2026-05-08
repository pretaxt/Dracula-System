"""回测 API — 运行资金费率套利历史回测并返回绩效结果。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.backtest.engine import BacktestEngine
from app.backtest.models import BacktestConfig, FundingPeriod
from app.exchanges.models import Symbol
from app.risk.limits import RiskLimits
from app.services.market_service import get_klines

router = APIRouter(prefix="/backtest", tags=["backtest"])

UTC = timezone.utc
_MAX_LIMIT = 500


class BacktestRequest(BaseModel):
    symbol: str = Field(default="BTC", description="基础货币，如 BTC")
    exchange: str = Field(default="binance")
    days: int = Field(default=30, ge=7, le=90)
    initial_capital_usd: float = Field(default=10_000, ge=1_000)
    size_per_trade_usd: float = Field(default=500, ge=100)
    min_apr_pct: float = Field(default=10.0, ge=1.0)
    max_positions: int = Field(default=5, ge=1, le=20)
    stop_loss_pct: float = Field(default=2.0, ge=0.5)
    max_hold_hours: float = Field(default=168.0, ge=8.0)


class EquityPoint(BaseModel):
    ts: int
    equity: float
    open_pos: int


class TradeRow(BaseModel):
    opened_at: str
    closed_at: str | None
    symbol: str
    funding: float
    fees: float
    pnl: float


class BacktestResponse(BaseModel):
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int
    total_funding_usd: float
    total_fees_usd: float
    final_equity_usd: float
    periods_days: float
    equity_curve: list[EquityPoint]
    trades: list[TradeRow]


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(body: BacktestRequest, request: Request) -> BacktestResponse:
    """根据历史资金费率运行回测，返回完整绩效结果。"""
    adapters: dict[str, Any] = getattr(request.app.state, "adapters", None) or {}
    adapter = adapters.get(body.exchange)
    if adapter is None:
        raise HTTPException(status_code=503, detail=f"交易所 {body.exchange} 适配器未就绪")

    symbol = Symbol(body.symbol.upper(), "USDT")
    limit = min(body.days * 3 + 10, _MAX_LIMIT)

    try:
        funding_rates = await adapter.fetch_funding_rate_history(symbol, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"资金费率历史拉取失败: {e}") from e

    if not funding_rates:
        raise HTTPException(status_code=404, detail="无历史资金费率数据")

    klines = await get_klines(
        adapters, symbol=body.symbol, interval="1h",
        limit=min(body.days * 24 + 24, _MAX_LIMIT), exchange=body.exchange,
    )

    price_map: dict[int, Decimal] = {}
    for k in klines:
        bucket = int(k["time"]) // 3_600_000 * 3_600_000
        price_map[bucket] = Decimal(k["close"])

    def _nearest_price(ts_ms: int) -> Decimal:
        bucket = ts_ms // 3_600_000 * 3_600_000
        if bucket in price_map:
            return price_map[bucket]
        if price_map:
            closest = min(price_map.keys(), key=lambda kb: abs(kb - bucket))
            return price_map[closest]
        return Decimal("1")

    cutoff = datetime.now(UTC) - timedelta(days=body.days)
    periods: list[FundingPeriod] = []
    for fr in funding_rates:
        ts_ms = fr.next_funding_time or 0
        if ts_ms <= 0:
            continue
        ts_dt = datetime.fromtimestamp(ts_ms / 1000, UTC)
        if ts_dt < cutoff:
            continue
        price = _nearest_price(ts_ms)
        periods.append(FundingPeriod(
            symbol=symbol, exchange=body.exchange,
            timestamp=ts_dt, funding_rate=fr.rate,
            spot_price=price, perp_price=price,
        ))

    if not periods:
        raise HTTPException(status_code=404, detail="所选时间范围内无资金费率数据")

    config = BacktestConfig(
        symbol=symbol,
        exchange=body.exchange,
        initial_capital_usd=Decimal(str(body.initial_capital_usd)),
        size_per_trade_usd=Decimal(str(body.size_per_trade_usd)),
        risk_limits=RiskLimits(
            max_positions=body.max_positions,
            max_position_size_usd=Decimal(str(body.size_per_trade_usd * 2)),
            stop_loss_pct=Decimal(str(body.stop_loss_pct)),
            max_hold_hours=Decimal(str(body.max_hold_hours)),
            min_apr_pct=Decimal(str(body.min_apr_pct)),
        ),
    )
    result = await BacktestEngine(config).run(periods)

    return BacktestResponse(
        total_return_pct=float(result.total_return_pct),
        annualized_return_pct=float(result.annualized_return_pct),
        sharpe_ratio=float(result.sharpe_ratio),
        max_drawdown_pct=float(result.max_drawdown_pct),
        win_rate_pct=float(result.win_rate_pct),
        total_trades=result.total_trades,
        total_funding_usd=float(result.total_funding_usd),
        total_fees_usd=float(result.total_fees_usd),
        final_equity_usd=float(result.final_equity_usd),
        periods_days=float(result.periods_days),
        equity_curve=[
            EquityPoint(ts=int(pt.timestamp.timestamp() * 1000),
                        equity=float(pt.equity_usd), open_pos=pt.open_positions)
            for pt in result.equity_curve
        ],
        trades=[
            TradeRow(
                opened_at=p.opened_at.isoformat() if p.opened_at else "",
                closed_at=p.closed_at.isoformat() if p.closed_at else None,
                symbol=str(p.symbol),
                funding=float(p.funding_received),
                fees=float(p.fees_paid),
                pnl=float(p.realized_pnl + p.funding_received - p.fees_paid),
            )
            for p in result.all_positions
        ],
    )
