"""System 路由 — 交易所实时健康 / 活动流 / 扫描宇宙 / API 凭据管理。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.system import (
    ActivityOut,
    ActivityResponse,
    ExchangeHealthOut,
    ExchangeHealthResponse,
)
from app.services.exchange_credentials import (
    list_credentials_metadata,
    save_credentials,
)
from app.services.system_service import get_exchange_health, get_recent_activity

router = APIRouter(prefix="/system", tags=["system"])


class SymbolsResponse(BaseModel):
    total: int
    symbols: list[str]  # ["BTC/USDT", "ETH/USDT", ...]


@router.get("/symbols", response_model=SymbolsResponse)
async def list_symbols(_: CurrentUser, request: Request) -> SymbolsResponse:
    """返回当前策略扫描的全部 USDT 永续标的（按字母序）。

    数据源：main.py 启动时从 binance + okx 适配器聚合，存于 app.state.symbols。
    """
    syms = getattr(request.app.state, "symbols", None) or []
    formatted = sorted({f"{s.base}/{s.quote}" for s in syms})
    return SymbolsResponse(total=len(formatted), symbols=formatted)


@router.get("/exchanges/health", response_model=ExchangeHealthResponse)
async def exchanges_health(_: CurrentUser, request: Request) -> ExchangeHealthResponse:
    """6 交易所实时延迟 ping(适配器存在的真实测,缺失的返回 unconfigured)。"""
    adapters = getattr(request.app.state, "adapters", None) or {}
    raw = await get_exchange_health(adapters)
    return ExchangeHealthResponse(data=[ExchangeHealthOut(**r) for r in raw])


@router.get("/activity", response_model=ActivityResponse)
async def activity(
    _: CurrentUser,
    db: DbSession,
    limit: int = Query(default=10, ge=1, le=50),
) -> ActivityResponse:
    """最近活动:开仓 / 平仓 / 资金费入账(从 PositionRecord 真实衍生)。"""
    raw = await get_recent_activity(db, limit=limit)
    return ActivityResponse(data=[ActivityOut(**r) for r in raw])


# ---------------------------------------------------------------------------
# 交易所 API 凭据管理 — UI 设置页可读/写
# ---------------------------------------------------------------------------


class ExchangeCredentialOut(BaseModel):
    exchange: str
    configured: bool
    api_key_preview: str        # masked: "abc123...wxyz"
    has_passphrase: bool
    updated_at: str | None


class ExchangeCredentialsResponse(BaseModel):
    data: list[ExchangeCredentialOut]


class ExchangeCredentialPatch(BaseModel):
    api_key: str | None = Field(default=None)
    api_secret: str | None = Field(default=None)
    passphrase: str | None = Field(default=None)


@router.get("/exchange-credentials", response_model=ExchangeCredentialsResponse)
async def list_exchange_credentials(_: CurrentUser) -> ExchangeCredentialsResponse:
    """列出每个交易所的凭据配置状态（不返明文 secret）。"""
    rows = list_credentials_metadata()
    return ExchangeCredentialsResponse(
        data=[ExchangeCredentialOut(**r) for r in rows]
    )


@router.post("/exchange-credentials/{exchange}", response_model=ExchangeCredentialOut)
async def update_exchange_credentials(
    exchange: str,
    body: ExchangeCredentialPatch,
    _: CurrentUser,
    request: Request,
) -> ExchangeCredentialOut:
    """写入指定交易所凭据（atomic + chmod 600），随后**热重载**对应 adapter + broker。

    - 凭据持久化到 /opt/dracula/state/exchange_credentials.json
    - 立即关闭旧 adapter，新建带新 key 的 adapter，原地替换
    - 若实盘模式且 OrderExecutor.broker 是 dict，同步重建该交易所的 LiveBroker
    - **无需重启容器**

    支持 binance / okx。空字符串字段被忽略（不覆盖）。
    """
    if exchange not in ("binance", "okx"):
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")
    patch = body.model_dump(exclude_none=True)
    try:
        save_credentials(exchange, patch)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 热重载 adapter + broker（失败仅 warning，重启容器仍能让文件凭据生效）
    try:
        await _hot_reload_exchange(request.app.state, exchange)
    except Exception as exc:
        # 不阻塞 — 用户至少把凭据存下来了，重启容器可恢复
        from app.core.logging import get_logger  # noqa: PLC0415
        get_logger(__name__).exception(
            "exchange_hot_reload_failed", exchange=exchange, error=str(exc)[:200]
        )

    for row in list_credentials_metadata():
        if row["exchange"] == exchange:
            return ExchangeCredentialOut(**row)
    raise HTTPException(status_code=500, detail="failed to read back credentials")


async def _hot_reload_exchange(app_state, exchange: str) -> None:
    """关闭旧 adapter，构造新 adapter（带文件凭据），替换 app.state + broker dict。"""
    from app.core.config import get_settings  # noqa: PLC0415
    from app.core.logging import get_logger  # noqa: PLC0415
    from app.execution.live_broker import LiveBroker  # noqa: PLC0415
    from app.services.exchange_credentials import get_exchange_credentials  # noqa: PLC0415
    from decimal import Decimal  # noqa: PLC0415

    logger = get_logger(__name__)
    settings = get_settings()
    creds = get_exchange_credentials(exchange)

    adapters = getattr(app_state, "adapters", {}) or {}
    old_adapter = adapters.get(exchange)
    if old_adapter is not None:
        try:
            await old_adapter.close()
        except Exception:
            logger.debug("old_adapter_close_failed", exchange=exchange)

    if exchange == "binance":
        from app.exchanges.cex.binance import BinanceAdapter  # noqa: PLC0415
        new_adapter = BinanceAdapter(
            api_key=creds.get("api_key") or settings.binance_api_key,
            api_secret=creds.get("api_secret") or settings.binance_api_secret,
        )
    elif exchange == "okx":
        from app.exchanges.cex.okx import OKXAdapter  # noqa: PLC0415
        new_adapter = OKXAdapter(
            api_key=creds.get("api_key") or settings.okx_api_key,
            api_secret=creds.get("api_secret") or settings.okx_api_secret,
            passphrase=creds.get("passphrase") or settings.okx_api_passphrase,
        )
    else:
        return

    adapters[exchange] = new_adapter  # 同 dict 引用 → scanner / runner 自动看到新值
    logger.info("exchange_adapter_hot_reloaded", exchange=exchange)

    # 同步重建 LiveBroker（仅 live_mode 且 broker 是 dict 时）
    paper_session = getattr(app_state, "paper_session", None)
    if paper_session is None:
        return
    executor = getattr(paper_session, "_executor", None)
    if executor is None:
        return
    broker = getattr(executor, "_broker", None)
    if not isinstance(broker, dict):
        return  # paper 模式或未启用，跳过
    # 重建该 exchange 的 LiveBroker
    fee_rate = getattr(broker.get(exchange), "fee_rate", Decimal("0.0002")) if exchange in broker else Decimal("0.0002")
    perp_lev = getattr(broker.get(exchange), "_perp_leverage", Decimal("5")) if exchange in broker else Decimal("5")
    if creds.get("api_key") or (
        (settings.binance_api_key if exchange == "binance" else settings.okx_api_key)
    ):
        broker[exchange] = LiveBroker(
            adapter=new_adapter, fee_rate=fee_rate, perp_leverage=perp_lev,
        )
        logger.info("live_broker_hot_reloaded", exchange=exchange)
