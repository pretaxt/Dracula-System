"""Dracula System — FastAPI 应用入口

启动方式::

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

lifespan 会读取配置、初始化交易所适配器，并启动资金费率扫描循环。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger
from app.exchanges.models import Symbol
from app.strategies.funding_rate.runner import FundingRateRunner
from app.strategies.funding_rate.scanner import ScannerConfig
from app.strategies.funding_rate.session_factory import build_paper_session

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Symbols to scan (will move to config/DB in Week 5)
# ---------------------------------------------------------------------------

_DEFAULT_SYMBOLS = [
    Symbol("BTC", "USDT"),
    Symbol("ETH", "USDT"),
    Symbol("SOL", "USDT"),
    Symbol("BNB", "USDT"),
    Symbol("XRP", "USDT"),
]

_SCAN_INTERVAL_SECONDS = 60.0
_STRATEGY_CONFIG_PATH = "config/strategies/funding_rate_main.yaml"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("dracula_starting", environment=settings.environment)

    # --- Load funding-rate strategy config ---
    try:
        with open(_STRATEGY_CONFIG_PATH) as f:
            strategy_cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("funding_rate_config_not_found", path=_STRATEGY_CONFIG_PATH)
        strategy_cfg = {}

    scanner_config = ScannerConfig.from_yaml(strategy_cfg)

    # --- Build exchange adapters (lazy: no network call at init) ---
    adapters: dict = {}
    try:
        from app.exchanges.cex.binance import BinanceAdapter  # noqa: PLC0415

        adapters["binance"] = BinanceAdapter(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
        )
        logger.info("exchange_adapter_ready", exchange="binance")
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="binance")

    # --- Start funding-rate runner (扫描 → DB + Redis) ---
    runner: FundingRateRunner | None = None
    runner_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # --- Start paper trading session (扫描 → 执行) ---
    from app.strategies.funding_rate.paper_trading import PaperTradingSession  # noqa: PLC0415
    paper_session: PaperTradingSession | None = None
    paper_task: asyncio.Task | None = None  # type: ignore[type-arg]

    if adapters:
        runner = FundingRateRunner(
            adapters=adapters,
            symbols=_DEFAULT_SYMBOLS,
            config=scanner_config,
            scan_interval_seconds=_SCAN_INTERVAL_SECONDS,
        )
        runner_task = asyncio.create_task(runner.run_forever(), name="funding_rate_runner")
        logger.info("funding_rate_runner_task_created")

        if strategy_cfg.get("enabled", False):
            paper_session = build_paper_session(
                cfg=strategy_cfg,
                adapters=adapters,
                symbols=_DEFAULT_SYMBOLS,
                scan_interval_seconds=_SCAN_INTERVAL_SECONDS,
            )
            paper_task = asyncio.create_task(
                paper_session.run_forever(), name="paper_trading_session"
            )
            logger.info(
                "paper_trading_session_started",
                instance=strategy_cfg.get("instance_name", "funding_rate_main"),
            )
        else:
            logger.info("paper_trading_disabled_in_config")
    else:
        logger.warning("no_adapters_available_runner_not_started")

    app.state.runner = runner
    app.state.paper_session = paper_session

    yield  # ← application handles requests here

    # --- Graceful shutdown ---
    if paper_session is not None:
        await paper_session.stop()
    if paper_task is not None:
        paper_task.cancel()
        try:
            await paper_task
        except asyncio.CancelledError:
            pass

    if runner is not None:
        await runner.stop()
    if runner_task is not None:
        runner_task.cancel()
        try:
            await runner_task
        except asyncio.CancelledError:
            pass

    for name, adapter in adapters.items():
        try:
            await adapter.close()
            logger.info("exchange_adapter_closed", exchange=name)
        except Exception:
            logger.exception("exchange_adapter_close_failed", exchange=name)

    logger.info("dracula_shutdown_complete")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    settings = get_settings()

    _app = FastAPI(
        title="Dracula System",
        description="Multi-strategy crypto arbitrage trading system",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    _app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.environment == "development" else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api.v1 import router as v1_router  # noqa: PLC0415

    _app.include_router(v1_router, prefix="/api/v1")

    # Root-level health endpoint for Docker / load-balancer probes
    @_app.get("/health", tags=["health"])
    async def root_health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return _app


app = create_app()
