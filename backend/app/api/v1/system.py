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

    支持 binance / okx / bitget / bybit / htx。空字符串字段被忽略（不覆盖）。
    """
    if exchange not in ("binance", "okx", "bitget", "bybit", "htx"):
        raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")
    patch = body.model_dump(exclude_none=True)
    try:
        save_credentials(exchange, patch)
    except ValueError as e:
        # P1-8: 旧 detail=str(e) 可能泄漏内部 trace / SQL / API key 片段
        # 改用固定 message，详细异常仅写日志
        from app.core.logging import get_logger as _get_logger  # noqa: PLC0415
        _get_logger(__name__).warning(
            "save_credentials_validation_failed",
            exchange=exchange, error=str(e)[:200],
        )
        raise HTTPException(
            status_code=400,
            detail=f"凭据验证失败（{exchange}）；请检查格式与权限",
        ) from e

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


@router.post("/consolidate")
async def consolidate_balances(
    _: CurrentUser,
    request: Request,
    exchange: str | None = None,
) -> dict:
    """把多钱包余额（USDM perp / cross-margin / funding）划转到 spot。

    用户明确指令：避免多钱包余额计算偏差，平仓后自动归集到 spot。
    本端点用于**手动立即**触发归集（reconciler 也会在每次平仓事件 + 5min 周期触发）。

    Parameters
    ----------
    exchange: 仅归集指定 CEX；省略 = 全部
    """
    state = request.app.state
    adapters = getattr(state, "adapters", {}) or {}
    targets = [exchange] if exchange else None
    try:
        from app.services.balance_consolidator import consolidate_to_spot  # noqa: PLC0415
        result = await consolidate_to_spot(adapters, targets)
        # 清 reconciler cache 让 dashboard 立即重读
        rec = getattr(state, "balance_reconciler", None)
        if rec is not None and hasattr(rec, "balance_cache"):
            rec.balance_cache.clear()
    except Exception as exc:
        from app.core.logging import get_logger  # noqa: PLC0415
        get_logger(__name__).exception("manual_consolidate_failed")
        raise HTTPException(
            status_code=500, detail=f"consolidate failed: {str(exc)[:200]}",
        ) from exc
    return {
        "status": "ok",
        "result": result,
        "summary": {
            ex: {
                "transfer_count": len(r.get("transfers", [])),
                "error_count": len(r.get("errors", [])),
            } for ex, r in result.items()
        },
    }


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
    elif exchange == "bitget":
        from app.exchanges.cex.bitget import BitgetAdapter  # noqa: PLC0415
        new_adapter = BitgetAdapter(
            api_key=creds.get("api_key") or settings.bitget_api_key,
            api_secret=creds.get("api_secret") or settings.bitget_api_secret,
            passphrase=creds.get("passphrase") or settings.bitget_api_passphrase,
        )
    elif exchange == "bybit":
        from app.exchanges.cex.bybit import BybitAdapter  # noqa: PLC0415
        new_adapter = BybitAdapter(
            api_key=creds.get("api_key") or settings.bybit_api_key,
            api_secret=creds.get("api_secret") or settings.bybit_api_secret,
        )
    elif exchange == "htx":
        from app.exchanges.cex.htx import HTXAdapter  # noqa: PLC0415
        new_adapter = HTXAdapter(
            api_key=creds.get("api_key") or settings.htx_api_key,
            api_secret=creds.get("api_secret") or settings.htx_api_secret,
        )
    else:
        return

    adapters[exchange] = new_adapter  # 同 dict 引用 → scanner / runner 自动看到新值
    logger.info("exchange_adapter_hot_reloaded", exchange=exchange)

    # 同步更新 BalanceReconcilerService 的 adapter dict + 清 balance_cache
    # （否则 reconciler 持有 lifespan 启动时的 authed 子集，hot_reload 后看不到新 CEX）
    reconciler = getattr(app_state, "balance_reconciler", None)
    if reconciler is not None and getattr(new_adapter, "_api_key", ""):
        try:
            reconciler._adapters[exchange] = new_adapter
            # 清掉该 exchange 的 cache，下次 tick 重新 fetch
            if hasattr(reconciler, "balance_cache"):
                reconciler.balance_cache.pop(exchange, None)
            if hasattr(reconciler, "position_cache"):
                reconciler.position_cache.pop(exchange, None)
            logger.info("reconciler_adapter_synced", exchange=exchange)
        except Exception:
            logger.exception("reconciler_adapter_sync_failed", exchange=exchange)

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
