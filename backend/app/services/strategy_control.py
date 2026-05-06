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
