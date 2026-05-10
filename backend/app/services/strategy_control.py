"""策略运行时控制 — start / stop / config patch。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

_lock = asyncio.Lock()


async def start_paper(app_state) -> bool:
    """启动纸交易 session（若已运行则幂等）。"""
    async with _lock:
        if getattr(app_state, "paper_session", None) is not None:
            task = getattr(app_state, "paper_task", None)
            if task and not task.done():
                return False  # already running

        from app.strategies.funding_rate.session_factory import build_paper_session  # noqa

        cfg = getattr(app_state, "strategy_cfg", {})
        adapters = getattr(app_state, "adapters", {})
        symbols = getattr(app_state, "symbols", [])

        session = build_paper_session(cfg, adapters, symbols)
        task = asyncio.create_task(session.run_forever(), name="paper_trading_session")
        app_state.paper_session = session
        app_state.paper_task = task
        return True


async def stop_paper(app_state) -> bool:
    """优雅停止纸交易 session。"""
    async with _lock:
        session = getattr(app_state, "paper_session", None)
        if session is None:
            return False
        await session.stop()
        task = getattr(app_state, "paper_task", None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        app_state.paper_session = None
        app_state.paper_task = None
        return True


def patch_strategy_config(app_state, patch: dict) -> dict:
    """热更新策略配置（下一 tick 生效）。"""
    cfg: dict = getattr(app_state, "strategy_cfg", {})
    for k, v in patch.items():
        if v is not None:
            cfg[k] = v
    app_state.strategy_cfg = cfg
    return cfg


# ---------------------------------------------------------------------------
# spot-perp 启停（D.1.5+）
# ---------------------------------------------------------------------------


_SP_CFG_PATH = "config/strategies/spot_perp_main.yaml"


def is_spot_perp_running(app_state) -> bool:
    """spot-perp session 是否在跑。"""
    sess = getattr(app_state, "spot_perp_paper", None)
    task = getattr(app_state, "spot_perp_paper_task", None)
    return sess is not None and task is not None and not task.done()


async def start_spot_perp(app_state) -> bool:
    """启动 spot-perp paper session（幂等）。返回 True 表示新启动，False 已在运行/缺资源。"""
    async with _lock:
        if is_spot_perp_running(app_state):
            return False

        from decimal import Decimal as _Dec  # noqa: PLC0415

        import yaml as _yaml  # noqa: PLC0415

        from app.core.config import get_settings  # noqa: PLC0415
        from app.execution.live_broker import LiveBroker  # noqa: PLC0415
        from app.services.runtime_overrides import load_overrides  # noqa: PLC0415
        from app.strategies.spot_perp_basis.paper_trading import (  # noqa: PLC0415
            SpotPerpPaperSession,
            SpotPerpStrategyConfig,
        )

        settings = get_settings()
        adapters = getattr(app_state, "adapters", {}) or {}
        runner = getattr(app_state, "spot_perp_runner", None)
        if runner is None or not adapters:
            return False

        # yaml + overrides
        try:
            with open(_SP_CFG_PATH) as f:
                yaml_data = _yaml.safe_load(f) or {}
        except FileNotFoundError:
            yaml_data = {}
        cfg = SpotPerpStrategyConfig.from_yaml(yaml_data)
        sp_overrides = (load_overrides() or {}).get("spot_perp") or {}
        if sp_overrides:
            cfg = cfg.apply_overrides(sp_overrides)
        if settings.spot_perp_notional_usd:
            cfg = cfg.apply_overrides(
                {"notional_per_position": settings.spot_perp_notional_usd}
            )

        # broker 路由
        live_mode = settings.trading_mode.lower() == "live"
        brokers = None
        if live_mode:
            brokers = {
                ex: LiveBroker(adapter=ad, fee_rate=_Dec("0.0004"),
                               perp_leverage=_Dec("3"))
                for ex, ad in adapters.items()
                if getattr(ad, "_api_key", "")
            }
            if not brokers:
                live_mode = False
                brokers = None

        session = SpotPerpPaperSession(
            runner=runner,
            tick_interval_seconds=60.0,
            live_mode=live_mode,
            brokers=brokers,
            strategy_config=cfg,
        )
        task = asyncio.create_task(
            session.run_forever(), name="spot_perp_paper_session",
        )
        app_state.spot_perp_paper = session
        app_state.spot_perp_paper_task = task
        return True


async def stop_spot_perp(app_state) -> bool:
    """优雅停止 spot-perp session（持仓不自动平仓）。"""
    async with _lock:
        session = getattr(app_state, "spot_perp_paper", None)
        if session is None:
            return False
        try:
            session.stop()  # sync
        except Exception:
            pass
        task = getattr(app_state, "spot_perp_paper_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        app_state.spot_perp_paper = None
        app_state.spot_perp_paper_task = None
        return True


# ---------------------------------------------------------------------------
# #02 perp-basis paper trading 启停（Phase C）
# ---------------------------------------------------------------------------


def is_perp_basis_paper_running(app_state) -> bool:
    sess = getattr(app_state, "perp_basis_paper", None)
    task = getattr(app_state, "perp_basis_paper_task", None)
    return sess is not None and task is not None and not task.done()


async def start_perp_basis_paper(app_state) -> bool:
    """启动 #02 perp-basis paper trading（已运行则幂等返回 False）。"""
    async with _lock:
        if is_perp_basis_paper_running(app_state):
            return False
        from app.strategies.perp_basis.session_factory import (  # noqa: PLC0415
            build_perp_basis_paper_session,
        )
        import yaml as _yaml  # noqa: PLC0415

        adapters = getattr(app_state, "adapters", {})
        scanner = getattr(getattr(app_state, "perp_basis_runner", None), "_scanner", None)
        if scanner is None:
            return False
        try:
            with open("config/strategies/perp_basis_main.yaml") as f:
                cfg = _yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}
        session = build_perp_basis_paper_session(
            cfg=cfg, adapters=adapters, scanner=scanner,
            market_data_hub=getattr(app_state, "market_data_hub", None),
        )
        if session is None:
            return False
        await session.restore()
        task = asyncio.create_task(session.run_forever(), name="perp_basis_paper")
        app_state.perp_basis_paper = session
        app_state.perp_basis_paper_task = task
        return True


async def stop_perp_basis_paper(app_state) -> bool:
    async with _lock:
        sess = getattr(app_state, "perp_basis_paper", None)
        if sess is None:
            return False
        try:
            await sess.stop()
        except Exception:
            pass
        task = getattr(app_state, "perp_basis_paper_task", None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        app_state.perp_basis_paper = None
        app_state.perp_basis_paper_task = None
        return True
