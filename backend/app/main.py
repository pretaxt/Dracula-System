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

_SCAN_INTERVAL_SECONDS = 60.0  # 默认值；按 yaml `scanning.scan_interval_seconds` 覆盖


def _read_scan_interval(cfg: dict, default: float = _SCAN_INTERVAL_SECONDS) -> float:
    """从策略 yaml 读 ``scanning.scan_interval_seconds``，缺省回退默认值。"""
    try:
        scanning = (cfg or {}).get("scanning") or {}
        return float(scanning.get("scan_interval_seconds", default))
    except (TypeError, ValueError):
        return default
_STRATEGY_CONFIG_PATH = "config/strategies/funding_rate_main.yaml"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    logger.info("dracula_starting", environment=settings.environment)

    # P0-β TaskSupervisor — 监控所有 long-running task，crash 后告警 + /health 反映
    from app.core.task_supervisor import TaskSupervisor  # noqa: PLC0415
    task_supervisor = TaskSupervisor()
    app.state.task_supervisor = task_supervisor

    # --- Load funding-rate strategy config ---
    try:
        with open(_STRATEGY_CONFIG_PATH) as f:
            strategy_cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("funding_rate_config_not_found", path=_STRATEGY_CONFIG_PATH)
        strategy_cfg = {}

    # 应用运行时 overrides（UI 通过 /risk/limits PATCH 持久化的值）
    from app.services.runtime_overrides import (  # noqa: PLC0415
        apply_to_strategy_cfg,
        load_overrides,
    )
    _overrides = load_overrides()
    if _overrides:
        strategy_cfg = apply_to_strategy_cfg(strategy_cfg, _overrides)
        logger.info("runtime_overrides_applied", keys=list(_overrides.keys()))

    scanner_config = ScannerConfig.from_yaml(strategy_cfg)

    # --- Build exchange adapters (lazy: no network call at init) ---
    # 凭据优先级：UI 写入的 /app/state/exchange_credentials.json > .env
    from app.services.exchange_credentials import get_exchange_credentials  # noqa: PLC0415

    adapters: dict = {}
    try:
        from app.exchanges.cex.binance import BinanceAdapter  # noqa: PLC0415

        _bn = get_exchange_credentials("binance")
        adapters["binance"] = BinanceAdapter(
            api_key=_bn.get("api_key") or settings.binance_api_key,
            api_secret=_bn.get("api_secret") or settings.binance_api_secret,
        )
        logger.info(
            "exchange_adapter_ready",
            exchange="binance",
            source="file" if _bn.get("api_key") else "env",
        )
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="binance")

    try:
        from app.exchanges.cex.okx import OKXAdapter  # noqa: PLC0415

        _okx = get_exchange_credentials("okx")
        adapters["okx"] = OKXAdapter(
            api_key=_okx.get("api_key") or settings.okx_api_key,
            api_secret=_okx.get("api_secret") or settings.okx_api_secret,
            passphrase=_okx.get("passphrase") or settings.okx_api_passphrase,
        )
        logger.info(
            "exchange_adapter_ready",
            exchange="okx",
            source="file" if _okx.get("api_key") else "env",
        )
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="okx")

    # Bitget — 公开行情即可（无 key 也能扫），有 key 时 trading 启用
    try:
        from app.exchanges.cex.bitget import BitgetAdapter  # noqa: PLC0415
        _bg = get_exchange_credentials("bitget")
        adapters["bitget"] = BitgetAdapter(
            api_key=_bg.get("api_key") or settings.bitget_api_key,
            api_secret=_bg.get("api_secret") or settings.bitget_api_secret,
            passphrase=_bg.get("passphrase") or settings.bitget_api_passphrase,
        )
        logger.info("exchange_adapter_ready", exchange="bitget",
                    source="file" if _bg.get("api_key") else "env")
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="bitget")

    # Bybit
    try:
        from app.exchanges.cex.bybit import BybitAdapter  # noqa: PLC0415
        _by = get_exchange_credentials("bybit")
        adapters["bybit"] = BybitAdapter(
            api_key=_by.get("api_key") or settings.bybit_api_key,
            api_secret=_by.get("api_secret") or settings.bybit_api_secret,
        )
        logger.info("exchange_adapter_ready", exchange="bybit",
                    source="file" if _by.get("api_key") else "env")
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="bybit")

    # HTX
    try:
        from app.exchanges.cex.htx import HTXAdapter  # noqa: PLC0415
        _ht = get_exchange_credentials("htx")
        adapters["htx"] = HTXAdapter(
            api_key=_ht.get("api_key") or settings.htx_api_key,
            api_secret=_ht.get("api_secret") or settings.htx_api_secret,
        )
        logger.info("exchange_adapter_ready", exchange="htx",
                    source="file" if _ht.get("api_key") else "env")
    except Exception:
        logger.exception("exchange_adapter_init_failed", exchange="htx")

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

        fr_scan_interval = _read_scan_interval(strategy_cfg)
        fr_window_min = float(
            (strategy_cfg or {}).get("scanning", {}).get("window_only_minutes", 0) or 0
        )
        runner = FundingRateRunner(
            adapters=adapters,
            symbols=scan_symbols,
            config=scanner_config,
            scan_interval_seconds=fr_scan_interval,
            window_only_minutes=fr_window_min,
        )
        runner_task = asyncio.create_task(runner.run_forever(), name="funding_rate_runner")
        task_supervisor.register("funding_rate_runner", runner_task)
        logger.info("funding_rate_runner_task_created")

        if strategy_cfg.get("enabled", False):
            _live_mode = settings.trading_mode.lower() == "live"
            paper_session = build_paper_session(
                cfg=strategy_cfg,
                adapters=adapters,
                symbols=scan_symbols,
                scan_interval_seconds=fr_scan_interval,
                live_mode=_live_mode,
            )
            logger.info("trading_mode", mode=settings.trading_mode, live=_live_mode)
            # 重启时从 DB 恢复仓位，避免去重失效导致重复开仓
            await paper_session.restore()
            paper_task = asyncio.create_task(
                paper_session.run_forever(), name="paper_trading_session"
            )
            task_supervisor.register("paper_trading_session", paper_task)
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

        # W6 listenKey 连续续签失败 N 次 → halt 所有策略 + Telegram critical
        async def _on_listen_key_critical_failure(msg: str) -> None:
            try:
                from app.notifications import notify_reconcile_alert  # noqa: PLC0415
                notify_reconcile_alert(
                    alert_type="listen_key_renewal_failed",
                    severity="critical",
                    exchange="binance",
                    symbol="*",
                    explanation=msg,
                )
            except Exception:
                logger.exception("notify_listen_key_failure_failed")
            # 停掉所有 paper session — fail-safe halt
            try:
                from app.services import strategy_control  # noqa: PLC0415
                stopped: list[str] = []
                if strategy_control.is_perp_basis_paper_running(app.state):
                    if await strategy_control.stop_perp_basis_paper(app.state):
                        stopped.append("#02")
                # #01 paper session
                fr = getattr(app.state, "paper_session", None)
                fr_task = getattr(app.state, "paper_task", None)
                if fr is not None and fr_task is not None and not fr_task.done():
                    if await strategy_control.stop_paper(app.state):
                        stopped.append("#01")
                logger.error(
                    "listen_key_critical_halted_strategies",
                    stopped=stopped,
                )
            except Exception:
                logger.exception("listen_key_critical_halt_failed")

        liquidation_watcher = LiquidationWatcher(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            on_liquidation=_on_liquidation,
            on_critical_failure=_on_listen_key_critical_failure,
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

    # --- 跨所 polling-based liquidation watcher (P1-9: 覆盖所有 perp 交易所) ---
    # 不依赖 WS 私有数据流的兜底网；每 30s 全所 fetch_positions vs 期望持仓集对账
    # 期望集合并自所有 paper session（#01 funding_rate + #02 perp_basis + #04 spot_perp）
    polling_watchers: list = []
    if settings.liquidation_watcher_enabled and adapters:
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        from app.notifications import notify_perp_liquidated  # noqa: PLC0415
        from app.risk.models import ExitReason  # noqa: PLC0415
        from app.safety import PollingLiquidationWatcher  # noqa: PLC0415

        # binance 已有 WS-based watcher，跳过避免重复
        _polling_targets = ["okx", "bybit", "htx", "bitget"]

        def _build_expected_getter(target_ex: str):
            """每个交易所一个独立 getter，闭包捕获 target_ex。

            W5 后 getter 改 async — 因为 spot_perp 期望集需查 DB。
            PollingLiquidationWatcher._tick 已兼容 sync + async return。
            """
            async def _get_expected() -> set[tuple[str, str]]:
                out: set[tuple[str, str]] = set()
                # #01 funding_rate
                fr = getattr(app.state, "paper_session", None)
                if fr is not None:
                    for pos in fr._manager.open_positions:
                        for leg in pos.legs:
                            if (leg.instrument_type == InstrumentType.PERPETUAL
                                    and leg.exchange == target_ex
                                    and leg.size > 0):
                                out.add((str(leg.symbol), leg.side.value))
                # #02 perp_basis
                pb = getattr(app.state, "perp_basis_paper", None)
                if pb is not None:
                    for pos in pb._manager.open_positions:
                        for leg in pos.legs:
                            if (leg.instrument_type == InstrumentType.PERPETUAL
                                    and leg.exchange == target_ex
                                    and leg.size > 0):
                                out.add((str(leg.symbol), leg.side.value))
                # W5 #04 spot_perp 纳入：DB 直读 PositionLegRecord
                # 之前注释说"主要在 binance 上跑"但 OKX UTA discount 方向也开 perp，
                # 不纳入会导致 OKX perp 强平裸奔到 reconciler 30s 兜底
                try:
                    from sqlalchemy import select  # noqa: PLC0415
                    from app.core.database import get_session  # noqa: PLC0415
                    from app.models.position import (  # noqa: PLC0415
                        PositionLegRecord, PositionRecord,
                    )
                    async with get_session() as _sess:
                        stmt = (
                            select(PositionLegRecord)
                            .join(
                                PositionRecord,
                                PositionLegRecord.position_id == PositionRecord.id,
                            )
                            .where(PositionRecord.strategy_instance == "spot_perp_main")
                            .where(PositionRecord.status == "open")
                            .where(PositionLegRecord.exchange == target_ex)
                            .where(PositionLegRecord.instrument_type == "perpetual")
                        )
                        rows = (await _sess.execute(stmt)).scalars().all()
                        for leg in rows:
                            if leg.size and float(leg.size) > 0:
                                # leg.side 已存为 "buy"/"sell" 字符串
                                out.add((str(leg.symbol), str(leg.side).lower()))
                except Exception:
                    logger.exception("polling_get_expected_spot_perp_failed",
                                     exchange=target_ex)
                return out
            return _get_expected

        def _build_on_liquidation(target_ex: str):
            """每个交易所一个独立回调。"""
            async def _on_liq(symbol, raw_event: dict) -> None:
                handled_any = False
                for sess_attr in ("paper_session", "perp_basis_paper"):
                    sess = getattr(app.state, sess_attr, None)
                    if sess is None:
                        continue
                    positions = sess._manager.get_by_symbol(symbol) or []
                    relevant = [
                        p for p in positions
                        if any(l.exchange == target_ex for l in p.legs)
                    ]
                    for pos in relevant:
                        try:
                            close_fn = getattr(sess, "close_position", None)
                            if close_fn:
                                await close_fn(pos.id, reason="perp_liq_risk")
                            elif sess_attr == "paper_session":
                                await sess._executor.close_position(
                                    pos.id, reason=ExitReason.PERP_LIQ_RISK,
                                )
                            logger.info(
                                "polling_liquidation_handled",
                                exchange=target_ex,
                                strategy=sess_attr, symbol=str(symbol),
                                position_id=pos.id[:8],
                            )
                            handled_any = True
                        except Exception:
                            logger.exception(
                                "polling_close_failed",
                                exchange=target_ex,
                                strategy=sess_attr, symbol=str(symbol),
                            )
                if not handled_any:
                    logger.warning(
                        "polling_no_matching_position",
                        exchange=target_ex, symbol=str(symbol),
                    )
                notify_perp_liquidated(str(symbol), raw_event.get("side", "?"), "?", "?")
            return _on_liq

        for ex_name in _polling_targets:
            ad = adapters.get(ex_name)
            if ad is None or not getattr(ad, "_api_key", ""):
                logger.info("polling_watcher_skipped", exchange=ex_name, reason="no_api_key")
                continue
            try:
                w = PollingLiquidationWatcher(
                    adapter=ad,
                    exchange_name=ex_name,
                    on_liquidation=_build_on_liquidation(ex_name),
                    get_expected_positions=_build_expected_getter(ex_name),
                    interval_seconds=30.0,
                )
                await w.start()
                polling_watchers.append(w)
                logger.info("polling_liquidation_watcher_enabled", exchange=ex_name)
            except Exception:
                logger.exception("polling_watcher_init_failed", exchange=ex_name)

    app.state.polling_liquidation_watchers = polling_watchers

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
                SpotPerpStrategyConfig,
            )

            # D.1.5: 加载 spot_perp_main.yaml + 应用 UI overrides
            _sp_cfg_path = "config/strategies/spot_perp_main.yaml"
            try:
                with open(_sp_cfg_path) as _f:
                    _sp_yaml = yaml.safe_load(_f) or {}
            except FileNotFoundError:
                logger.warning("spot_perp_yaml_not_found", path=_sp_cfg_path)
                _sp_yaml = {}

            sp_strategy_cfg = SpotPerpStrategyConfig.from_yaml(_sp_yaml)

            # 应用 runtime_overrides 中的 spot_perp.* 子节
            from app.services.runtime_overrides import load_overrides  # noqa: PLC0415
            _sp_overrides = (load_overrides() or {}).get("spot_perp") or {}
            if _sp_overrides:
                sp_strategy_cfg = sp_strategy_cfg.apply_overrides(_sp_overrides)
                logger.info("spot_perp_overrides_applied", keys=list(_sp_overrides.keys()))

            # scanner 候选币 / 交易所 / 阈值取自 yaml（与 session 一致）
            sp_scanner_symbols = (
                sp_strategy_cfg.candidate_symbols
                or list(SpotPerpConfig().symbols)
            )
            sp_scanner_exchanges = (
                sp_strategy_cfg.exchanges
                or list(adapters.keys())
            )
            sp_scanner = SpotPerpBasisScanner(
                adapters=adapters,
                config=SpotPerpConfig(
                    min_basis_pct=sp_strategy_cfg.scan_threshold_pct,
                    symbols=sp_scanner_symbols,
                ),
                exchanges=sp_scanner_exchanges,
            )
            sp_scan_interval = _read_scan_interval(_sp_yaml)
            spot_perp_runner = SpotPerpRunner(
                scanner=sp_scanner, scan_interval_seconds=sp_scan_interval
            )
            spot_perp_task = asyncio.create_task(
                spot_perp_runner.run_forever(), name="spot_perp_runner"
            )
            task_supervisor.register("spot_perp_runner", spot_perp_task)
            logger.info("spot_perp_runner_task_created")

            # 实盘 broker 路由（同 funding_rate）
            from decimal import Decimal as _Dec  # noqa: PLC0415
            from app.execution.live_broker import LiveBroker  # noqa: PLC0415

            _sp_live = settings.trading_mode.lower() == "live"
            _sp_brokers: dict | None = None
            if _sp_live:
                _sp_brokers = {
                    ex: LiveBroker(adapter=ad, fee_rate=_Dec("0.0004"),
                                   perp_leverage=_Dec("3"))
                    for ex, ad in adapters.items()
                    if getattr(ad, "_api_key", "")
                }
                if not _sp_brokers:
                    logger.warning("spot_perp_live_no_authed_adapters_falling_back_paper")
                    _sp_live = False
                    _sp_brokers = None

            # 单笔 notional 优先级: env SPOT_PERP_NOTIONAL_USD > yaml > 默认
            _sp_notional_env = settings.spot_perp_notional_usd
            if _sp_notional_env:
                sp_strategy_cfg = sp_strategy_cfg.apply_overrides(
                    {"notional_per_position": _sp_notional_env}
                )

            spot_perp_paper = SpotPerpPaperSession(
                runner=spot_perp_runner,
                tick_interval_seconds=60.0,
                live_mode=_sp_live,
                brokers=_sp_brokers,
                strategy_config=sp_strategy_cfg,
            )
            spot_perp_paper_task = asyncio.create_task(
                spot_perp_paper.run_forever(), name="spot_perp_paper_session"
            )
            task_supervisor.register("spot_perp_paper_session", spot_perp_paper_task)
            logger.info(
                "spot_perp_paper_session_task_created",
                live=_sp_live,
                notional=str(sp_strategy_cfg.notional_per_position),
                entry_pct=str(sp_strategy_cfg.entry_pct),
                exit_pct=str(sp_strategy_cfg.exit_pct),
                max_concurrent=sp_strategy_cfg.max_concurrent,
                direction_filter=sp_strategy_cfg.direction_filter,
            )
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
    # 兼容字段：app.state.okx_polling_watcher 旧引用（reconciliation 等可能读）
    app.state.okx_polling_watcher = next(
        (w for w in polling_watchers if w._exchange_name == "okx"), None,
    )

    # --- Market Data Hub (跨策略共享行情，去重 API 调用) ---
    market_data_hub = None
    if adapters:
        try:
            from app.core.market_data_hub import MarketDataHub, set_market_data_hub  # noqa: PLC0415
            market_data_hub = MarketDataHub(adapters=adapters)
            await market_data_hub.start()
            set_market_data_hub(market_data_hub)
            logger.info("market_data_hub_initialized")
        except Exception:
            logger.exception("market_data_hub_init_failed")
    app.state.market_data_hub = market_data_hub

    # P0-α: 把 hub 注入 funding_rate scanner（之前 runner 在 hub 创建前实例化，
    # 这里做 late-bind，让 scanner 复用 hub 的 bulk funding cache，从 5-6 分钟
    # 全量扫描降到 30-90 秒）
    if runner is not None and market_data_hub is not None:
        try:
            runner._scanner._hub = market_data_hub
            logger.info("funding_rate_scanner_hub_attached")
        except Exception:
            logger.exception("funding_rate_scanner_hub_attach_failed")

    # --- BalanceReconcilerService (实时余额 + 持仓对账，单腿告警) ---
    balance_reconciler = None
    balance_reconciler_task = None
    if adapters:
        try:
            from app.services.balance_reconciler import BalanceReconcilerService  # noqa: PLC0415
            # 仅传已鉴权的 adapter（fetch_balance/positions 需要 API key）
            authed_adapters = {
                n: a for n, a in adapters.items()
                if getattr(a, "_api_key", "")
            }
            if authed_adapters:
                balance_reconciler = BalanceReconcilerService(
                    adapters=authed_adapters,
                    refresh_interval_s=30.0,
                    market_data_hub=market_data_hub,  # R7: mark-to-market PnL
                )
                balance_reconciler_task = asyncio.create_task(
                    balance_reconciler.run_forever(), name="balance_reconciler",
                )
                task_supervisor.register("balance_reconciler", balance_reconciler_task)
                logger.info("balance_reconciler_initialized",
                            authed_exchanges=list(authed_adapters.keys()))
            else:
                logger.warning("balance_reconciler_no_authed_adapters")
        except Exception:
            logger.exception("balance_reconciler_init_failed")
    app.state.balance_reconciler = balance_reconciler
    app.state.balance_reconciler_task = balance_reconciler_task

    # B3-1: 注入 app.state 给 risk_circuit_breaker，保证 daily_dd 分母用真实账户
    try:
        from app.services.risk_circuit_breaker import set_app_state  # noqa: PLC0415
        set_app_state(app.state)
        logger.info("risk_circuit_breaker_app_state_injected")
    except Exception:
        logger.exception("risk_circuit_breaker_inject_failed")

    # --- #02 perp-basis runner (Phase A: monitor only) ---
    perp_basis_runner = None
    perp_basis_task = None
    if market_data_hub is not None:
        try:
            from decimal import Decimal as _Decimal  # noqa: PLC0415
            from app.strategies.perp_basis.runner import PerpBasisRunner  # noqa: PLC0415
            from app.strategies.perp_basis.scanner import (  # noqa: PLC0415
                PerpBasisScanner,
                PerpBasisScannerConfig,
                all_pairs,
            )
            _pb_cfg_path = "config/strategies/perp_basis_main.yaml"
            try:
                with open(_pb_cfg_path) as _f:
                    _pb_yaml = yaml.safe_load(_f) or {}
            except FileNotFoundError:
                logger.warning("perp_basis_yaml_not_found", path=_pb_cfg_path)
                _pb_yaml = {}
            # 应用 runtime overrides（PATCH /perp-basis/config 持久化的热更新）
            try:
                from app.services.runtime_overrides import (  # noqa: PLC0415
                    load_overrides as _load_pb_overrides,
                    apply_to_perp_basis_cfg,
                )
                _pb_overrides = (_load_pb_overrides() or {}).get("perp_basis") or {}
                if isinstance(_pb_overrides, dict) and _pb_overrides:
                    _pb_yaml = apply_to_perp_basis_cfg(_pb_yaml, _pb_overrides)
                    logger.info(
                        "perp_basis_overrides_applied", keys=list(_pb_overrides.keys()),
                    )
            except Exception:  # noqa: BLE001
                logger.exception("perp_basis_overrides_apply_failed")
            _pb_entry = _pb_yaml.get("entry", {})
            _pb_pos = _pb_yaml.get("position", {})
            _pb_scan = _pb_yaml.get("scanning", {})
            _pb_exchanges = list(_pb_pos.get("exchanges", []) or list(adapters.keys()))
            # 仅保留 adapter 已就绪的交易所
            _pb_exchanges = [e for e in _pb_exchanges if e in adapters]
            _pb_pairs = all_pairs(_pb_exchanges)
            _pb_risk = _pb_yaml.get("risk", {}) or {}
            pb_scanner = PerpBasisScanner(
                hub=market_data_hub,
                config=PerpBasisScannerConfig(
                    candidate_symbols=list(_pb_pos.get("candidate_symbols", []) or []),
                    exchange_pairs=_pb_pairs,
                    min_diff_apr_pct=_Decimal(str(_pb_entry.get("min_diff_apr_pct", "3.0"))),
                    max_opportunities=int(_pb_entry.get("max_opportunities", 50)),
                    max_funding_age_seconds=float(_pb_scan.get("max_funding_age_seconds", 180)),
                    max_abs_apr_pct=_Decimal(str(_pb_risk.get("max_abs_apr_pct", "500.0"))),
                    health_safe_diff_apr_max=_Decimal(str(
                        _pb_risk.get("health_safe_diff_apr_max", "200.0")
                    )),
                    health_risky_diff_apr_max=_Decimal(str(
                        _pb_risk.get("health_risky_diff_apr_max", "500.0")
                    )),
                ),
            )
            perp_basis_runner = PerpBasisRunner(
                scanner=pb_scanner,
                scan_interval_seconds=float(_pb_scan.get("scan_interval_seconds", 30)),
            )
            perp_basis_task = asyncio.create_task(
                perp_basis_runner.run_forever(), name="perp_basis_runner",
            )
            task_supervisor.register("perp_basis_runner", perp_basis_task)
            logger.info(
                "perp_basis_runner_initialized",
                exchanges=len(_pb_exchanges),
                pairs=len(_pb_pairs),
                symbols=len(_pb_pos.get("candidate_symbols", []) or []),
            )
        except Exception:
            logger.exception("perp_basis_runner_init_failed")
    app.state.perp_basis_runner = perp_basis_runner
    app.state.perp_basis_task = perp_basis_task

    # --- #02 perp-basis Phase C paper trading (off by default) ---
    perp_basis_paper = None
    perp_basis_paper_task = None
    if perp_basis_runner is not None and adapters:
        try:
            _pb_paper_enabled = bool(
                (_pb_yaml.get("paper_trading", {}) or {}).get("enabled", False)
            )
            if _pb_paper_enabled:
                from app.strategies.perp_basis.session_factory import (  # noqa: PLC0415
                    build_perp_basis_paper_session,
                )
                # #02 paper trading 必须强制 paper 模式：跨所策略需 ≥2 broker，
                # 但只有 binance 配了 trading key → live=True 时只 1 broker 永远开不了仓
                # paper 模式下用 PaperBroker wrapper，所有 5 家 adapter 都参与
                _pb_yaml_pt = _pb_yaml.get("paper_trading", {}) or {}
                _pb_live_mode = bool(_pb_yaml_pt.get("live_mode", False))
                perp_basis_paper = build_perp_basis_paper_session(
                    cfg=_pb_yaml, adapters=adapters, scanner=pb_scanner,
                    market_data_hub=market_data_hub,
                    live_mode=_pb_live_mode,
                )
                logger.info(
                    "perp_basis_paper_mode",
                    live=_pb_live_mode,
                    broker_count=len(perp_basis_paper._brokers) if perp_basis_paper else 0,
                )
                if perp_basis_paper is not None:
                    await perp_basis_paper.restore()
                    perp_basis_paper_task = asyncio.create_task(
                        perp_basis_paper.run_forever(), name="perp_basis_paper",
                    )
                    task_supervisor.register("perp_basis_paper", perp_basis_paper_task)
                    logger.info("perp_basis_paper_initialized")
        except Exception:
            logger.exception("perp_basis_paper_init_failed")
    app.state.perp_basis_paper = perp_basis_paper
    app.state.perp_basis_paper_task = perp_basis_paper_task

    # --- Telegram 双向命令 bot（C 项）---
    telegram_bot = None
    if (
        settings.telegram_command_bot_enabled
        and settings.telegram_bot_token
    ):
        from app.notifications.telegram_bot import TelegramCommandBot  # noqa: PLC0415
        from app.notifications.telegram_bot_handlers import (  # noqa: PLC0415
            build_command_handlers,
        )

        # 白名单优先 telegram_allowed_chat_ids（逗号分隔）；否则退回 telegram_chat_id
        _raw = (settings.telegram_allowed_chat_ids
                or settings.telegram_chat_id or "")
        allowed = {x.strip() for x in _raw.split(",") if x.strip()}
        if allowed:
            try:
                telegram_bot = TelegramCommandBot(
                    token=settings.telegram_bot_token,
                    allowed_chat_ids=allowed,
                    handlers=build_command_handlers(app.state),
                    bot_username=None,  # 私聊场景不需要校验 @suffix
                )
                await telegram_bot.start()
                logger.info("telegram_command_bot_enabled",
                            allowed_chats=len(allowed))
            except Exception:
                logger.exception("telegram_command_bot_start_failed")
                telegram_bot = None
        else:
            logger.info("telegram_command_bot_disabled_no_allowlist")
    else:
        logger.info(
            "telegram_command_bot_disabled",
            reason=("flag_off" if not settings.telegram_command_bot_enabled
                    else "no_token"),
        )
    app.state.telegram_bot = telegram_bot

    yield  # ← application handles requests here

    # --- Graceful shutdown ---
    if perp_basis_runner is not None:
        perp_basis_runner.stop()
    if perp_basis_task is not None:
        perp_basis_task.cancel()
        try:
            await perp_basis_task
        except asyncio.CancelledError:
            pass
    if market_data_hub is not None:
        try:
            await market_data_hub.stop()
        except Exception:
            logger.exception("market_data_hub_stop_failed")
    if telegram_bot is not None:
        try:
            await telegram_bot.stop()
        except Exception:
            logger.exception("telegram_bot_stop_failed")
    if liquidation_watcher is not None:
        await liquidation_watcher.stop()
    for _w in polling_watchers:
        try:
            await _w.stop()
        except Exception:
            pass
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

    # P1-8: 统一异常处理器 — 500 不带 trace（防内部信息泄漏）
    from fastapi.responses import JSONResponse as _JSONResponse  # noqa: PLC0415
    from starlette.exceptions import HTTPException as _StarletteHTTPException  # noqa: PLC0415

    @_app.exception_handler(_StarletteHTTPException)
    async def _http_exception_handler(_req, exc: _StarletteHTTPException):
        """让 HTTPException 的 detail 透传，但 500 单独走 generic handler。"""
        return _JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @_app.exception_handler(Exception)
    async def _generic_exception_handler(req, exc: Exception):
        """未捕获异常 → 500 + 固定 message + log 真实异常（不泄漏 trace）。"""
        logger.exception(
            "unhandled_request_exception",
            path=str(getattr(req, "url", "?")),
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
        )
        return _JSONResponse(
            status_code=500,
            content={"detail": "internal server error"},
        )

    # 轻量级 metrics 中间件：纯 ASGI 形式（Starlette 文档推荐方式，绕过
    # BaseHTTPMiddleware/装饰器 在某些 lifespan 工厂场景的失效问题）
    from starlette.types import ASGIApp, Receive, Scope, Send  # noqa: PLC0415
    from time import perf_counter as _perf_counter  # noqa: PLC0415

    class _MetricsMiddleware:
        def __init__(self, app: ASGIApp) -> None:
            self._app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self._app(scope, receive, send)
                return
            from app.core.metrics import get_metrics  # noqa: PLC0415
            start = _perf_counter()
            status_code = 500

            async def _send(message):
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message.get("status", 500))
                await send(message)

            try:
                await self._app(scope, receive, _send)
            finally:
                elapsed_ms = (_perf_counter() - start) * 1000
                get_metrics().record_http(elapsed_ms, status_code)

    _app.add_middleware(_MetricsMiddleware)

    from app.api.v1 import router as v1_router  # noqa: PLC0415

    _app.include_router(v1_router, prefix="/api/v1")

    # Root-level health endpoint for Docker / load-balancer probes
    @_app.get("/health", tags=["health"])
    async def root_health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return _app


app = create_app()
