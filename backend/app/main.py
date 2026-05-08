"""Dracula System — FastAPI 应用入口

启动方式::

    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

lifespan 会读取配置、初始化交易所适配器，并启动资金费率扫描循环。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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
    # Top market cap (5)
    Symbol("BTC", "USDT"),
    Symbol("ETH", "USDT"),
    Symbol("SOL", "USDT"),
    Symbol("BNB", "USDT"),
    Symbol("XRP", "USDT"),
    # Major L1/L2 (10)
    Symbol("DOGE", "USDT"),
    Symbol("ADA", "USDT"),
    Symbol("AVAX", "USDT"),
    Symbol("DOT", "USDT"),
    Symbol("LINK", "USDT"),
    Symbol("LTC", "USDT"),
    Symbol("ATOM", "USDT"),
    Symbol("NEAR", "USDT"),
    Symbol("APT", "USDT"),
    Symbol("TRX", "USDT"),
    # DeFi / mid-cap (10)
    Symbol("UNI", "USDT"),
    Symbol("AAVE", "USDT"),
    Symbol("ARB", "USDT"),
    Symbol("OP", "USDT"),
    Symbol("FIL", "USDT"),
    Symbol("INJ", "USDT"),
    Symbol("SUI", "USDT"),
    Symbol("SEI", "USDT"),
    Symbol("LDO", "USDT"),
    Symbol("BCH", "USDT"),
    # Established / liquidity-tested (5)
    Symbol("ETC", "USDT"),
    Symbol("FTM", "USDT"),
    Symbol("MANA", "USDT"),
    Symbol("SAND", "USDT"),
    Symbol("RUNE", "USDT"),
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

    try:
        from app.exchanges.cex.okx import OKXAdapter  # noqa: PLC0415

        adapters["okx"] = OKXAdapter(
            api_key=settings.okx_api_key,
            api_secret=settings.okx_api_secret,
            passphrase=settings.okx_api_passphrase,
        )
        logger.info("exchange_adapter_ready", exchange="okx")
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="okx")

    # --- Start funding-rate runner (扫描 → DB + Redis) ---
    runner: FundingRateRunner | None = None
    runner_task: asyncio.Task | None = None  # type: ignore[type-arg]

    # --- Start paper trading session (扫描 → 执行) ---
    from app.strategies.funding_rate.paper_trading import PaperTradingSession  # noqa: PLC0415
    paper_session: PaperTradingSession | None = None
    paper_task: asyncio.Task | None = None  # type: ignore[type-arg]

    if adapters:
        # --- Dynamic symbol universe: union of all USDT perpetuals from active adapters ---
        # 启动时一次性拉取（每个 adapter ~一次 API call）；不再硬编码白名单。
        # 失败的 adapter 用 _DEFAULT_SYMBOLS 兜底以避免完全瘫痪。
        symbol_set: set = set()
        for ex_name, ad in adapters.items():
            try:
                listed = await ad.list_usdt_perpetual_symbols()
                symbol_set.update((s.base, s.quote) for s in listed)
                logger.info("symbol_universe_loaded", exchange=ex_name, count=len(listed))
            except Exception:
                logger.exception("symbol_universe_load_failed", exchange=ex_name)
        if symbol_set:
            scan_symbols = [Symbol(b, q) for b, q in sorted(symbol_set)]
        else:
            scan_symbols = _DEFAULT_SYMBOLS  # fallback
            logger.warning("symbol_universe_fallback_to_default", count=len(scan_symbols))
        logger.info("symbol_universe_total", total=len(scan_symbols))

        runner = FundingRateRunner(
            adapters=adapters,
            symbols=scan_symbols,
            config=scanner_config,
            scan_interval_seconds=_SCAN_INTERVAL_SECONDS,
        )
        runner_task = asyncio.create_task(runner.run_forever(), name="funding_rate_runner")
        logger.info("funding_rate_runner_task_created")

        if strategy_cfg.get("enabled", False):
            _live_mode = settings.trading_mode.lower() == "live"
            paper_session = build_paper_session(
                cfg=strategy_cfg,
                adapters=adapters,
                symbols=scan_symbols,
                scan_interval_seconds=_SCAN_INTERVAL_SECONDS,
                live_mode=_live_mode,
            )
            logger.info("trading_mode", mode=settings.trading_mode, live=_live_mode)
            # 重启时从 DB 恢复仓位，避免去重失效导致重复开仓
            await paper_session.restore()
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

    # --- Liquidation watcher (实盘双腿同步保护，paper 默认关) ---
    liquidation_watcher = None
    if (
        settings.liquidation_watcher_enabled
        and settings.binance_api_key
        and paper_session is not None
    ):
        from app.safety import LiquidationWatcher  # noqa: PLC0415
        from app.notifications import notify_perp_liquidated  # noqa: PLC0415
        from app.risk.models import ExitReason  # noqa: PLC0415

        _captured_session = paper_session

        async def _on_liquidation(symbol, raw_event: dict) -> None:
            """永续被强平时的紧急回调：找到对应仓位（可能多笔）→ close → 通知。"""
            positions = _captured_session._manager.get_by_symbol(symbol) or []
            if not positions:
                logger.warning("liquidation_no_matching_position", symbol=str(symbol))
                notify_perp_liquidated(
                    str(symbol), raw_event.get("S", "?"),
                    raw_event.get("q", "?"), raw_event.get("ap", "?"),
                )
                return
            for pos in positions:
                try:
                    await _captured_session._executor.close_position(
                        pos.id, reason=ExitReason.PERP_LIQ_RISK,
                    )
                    logger.info(
                        "perp_liquidation_handled",
                        symbol=str(symbol), position_id=pos.id[:8],
                    )
                except Exception:
                    logger.exception(
                        "perp_liquidation_close_failed",
                        symbol=str(symbol), position_id=pos.id[:8],
                    )
            notify_perp_liquidated(
                str(symbol), raw_event.get("S", "?"),
                raw_event.get("q", "?"), raw_event.get("ap", "?"),
            )

        liquidation_watcher = LiquidationWatcher(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            on_liquidation=_on_liquidation,
        )
        await liquidation_watcher.start()
        logger.info("liquidation_watcher_enabled")
    else:
        logger.info(
            "liquidation_watcher_disabled",
            reason=(
                "flag_off" if not settings.liquidation_watcher_enabled
                else "no_binance_key" if not settings.binance_api_key
                else "no_paper_session"
            ),
        )

    # --- Start spot-perp basis scanner (B.1) + paper trading (B.2) ---
    spot_perp_runner = None
    spot_perp_task: asyncio.Task | None = None  # type: ignore[type-arg]
    spot_perp_paper = None
    spot_perp_paper_task: asyncio.Task | None = None  # type: ignore[type-arg]
    if adapters:
        try:
            from app.strategies.spot_perp_basis.runner import SpotPerpRunner  # noqa: PLC0415
            from app.strategies.spot_perp_basis.scanner import (  # noqa: PLC0415
                SpotPerpBasisScanner,
                SpotPerpConfig,
            )
            from app.strategies.spot_perp_basis.paper_trading import (  # noqa: PLC0415
                SpotPerpPaperSession,
            )
            sp_scanner = SpotPerpBasisScanner(
                adapters=adapters,
                config=SpotPerpConfig(),
            )
            spot_perp_runner = SpotPerpRunner(
                scanner=sp_scanner, scan_interval_seconds=60.0
            )
            spot_perp_task = asyncio.create_task(
                spot_perp_runner.run_forever(), name="spot_perp_runner"
            )
            logger.info("spot_perp_runner_task_created")

            spot_perp_paper = SpotPerpPaperSession(
                runner=spot_perp_runner, tick_interval_seconds=60.0
            )
            spot_perp_paper_task = asyncio.create_task(
                spot_perp_paper.run_forever(), name="spot_perp_paper_session"
            )
            logger.info("spot_perp_paper_session_task_created")
        except Exception:
            logger.exception("spot_perp_init_failed")

    app.state.runner = runner
    app.state.paper_session = paper_session
    app.state.paper_task = paper_task
    app.state.strategy_cfg = strategy_cfg
    app.state.adapters = adapters
    app.state.symbols = scan_symbols if 'scan_symbols' in locals() else _DEFAULT_SYMBOLS
    app.state.startup_time = datetime.now(timezone.utc)
    app.state.spot_perp_runner = spot_perp_runner
    app.state.spot_perp_task = spot_perp_task
    app.state.spot_perp_paper = spot_perp_paper
    app.state.spot_perp_paper_task = spot_perp_paper_task
    app.state.liquidation_watcher = liquidation_watcher

    yield  # ← application handles requests here

    # --- Graceful shutdown ---
    if liquidation_watcher is not None:
        await liquidation_watcher.stop()
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

    if spot_perp_paper is not None:
        spot_perp_paper.stop()
    if spot_perp_paper_task is not None:
        spot_perp_paper_task.cancel()
        try:
            await spot_perp_paper_task
        except asyncio.CancelledError:
            pass

    if spot_perp_runner is not None:
        spot_perp_runner.stop()
    if spot_perp_task is not None:
        spot_perp_task.cancel()
        try:
            await spot_perp_task
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
