"""健康检查端点 — GET /api/v1/health"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    startup_time: datetime | None = None
    # P0-β task health：task crash 时反映到 /health → docker restart 触发
    all_tasks_healthy: bool = True
    task_count: int = 0
    failed_tasks: list[dict[str, Any]] = []
    # B4 修复 (round 2 audit): dgr_btc 启动不变量
    # paper_session 注册 + pnl_writer 心跳 < 5min, 任一失败 → 503 + 触发 docker restart
    dgr_btc_invariants_ok: bool = True
    dgr_btc_invariants: dict[str, Any] = {}


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    """返回服务存活状态、版本、uptime + 后台 task 健康度。

    P0-β: docker-compose healthcheck 之前永远 200 即使所有 task crash —
    现在 task 健康度真实反映：任一 task 失败返回 503，触发 docker restart。
    """
    startup_time: datetime | None = getattr(request.app.state, "startup_time", None)
    if startup_time is None:
        uptime_seconds = 0.0
    else:
        uptime_seconds = (datetime.now(timezone.utc) - startup_time).total_seconds()

    supervisor = getattr(request.app.state, "task_supervisor", None)
    if supervisor is not None:
        snap = supervisor.health_snapshot()
        all_healthy = bool(snap.get("all_healthy", True))
        task_count = int(snap.get("task_count", 0))
        failed = [t for t in snap.get("tasks", []) if t.get("failed")]
    else:
        all_healthy = True
        task_count = 0
        failed = []

    # B4 dgr_btc 启动不变量 — 防 chip 误删 main.py 启动段类事故重演
    dgr_invariants_ok, dgr_invariants = _check_dgr_btc_invariants(request)

    if not all_healthy or not dgr_invariants_ok:
        # 任一关键失败 → 503，docker-compose healthcheck 触发 restart
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if (all_healthy and dgr_invariants_ok) else "degraded",
        version="0.1.0",
        uptime_seconds=round(uptime_seconds, 1),
        startup_time=startup_time,
        all_tasks_healthy=all_healthy,
        task_count=task_count,
        failed_tasks=failed,
        dgr_btc_invariants_ok=dgr_invariants_ok,
        dgr_btc_invariants=dgr_invariants,
    )


def _check_dgr_btc_invariants(request: Request) -> tuple[bool, dict[str, Any]]:
    """B4 (round 2 audit): 校验 dgr_btc 关键运行时不变量.

    检查项:
      1. paper_session 已注册 (app.state.dgr_btc_paper 不为 None)
      2. pnl_writer task 存在于 supervisor (避免之前 chip 误删事故重演)
      3. paper_session 最近 tick < 5 min (心跳新鲜)

    若 dgr_btc 已禁用 (override_enabled=False), 跳过这些 invariant.
    """
    invariants: dict[str, Any] = {}

    # 1. paper_session 注册
    sess = getattr(request.app.state, "dgr_btc_paper", None)
    if sess is None:
        # 可能是 disabled 状态, 看 override
        try:
            from app.services.runtime_overrides import load_dgr_btc_overrides  # noqa: PLC0415
            override = load_dgr_btc_overrides()
            if not bool(override.get("enabled", False)):
                # disabled → 不算 invariant violation
                invariants["paper_session_registered"] = "skipped (disabled)"
                return True, invariants
        except Exception:
            pass
        invariants["paper_session_registered"] = False
        return False, invariants
    invariants["paper_session_registered"] = True

    # 2. pnl_writer task 注册
    supervisor = getattr(request.app.state, "task_supervisor", None)
    pnl_writer_registered = False
    if supervisor is not None:
        try:
            snap = supervisor.health_snapshot()
            tasks = snap.get("tasks", [])
            pnl_writer_registered = any(
                t.get("name") == "dgr_btc_pnl_writer" for t in tasks
            )
        except Exception:
            pass
    invariants["pnl_writer_registered"] = pnl_writer_registered

    # 3. tick 新鲜度 — 最近 5 min 内
    last_tick = getattr(sess, "_last_tick_at", None)
    tick_fresh = False
    if last_tick is not None:
        age_seconds = (datetime.now(timezone.utc) - last_tick).total_seconds()
        tick_fresh = age_seconds < 300  # 5 min
        invariants["last_tick_age_seconds"] = round(age_seconds, 1)
    invariants["tick_fresh"] = tick_fresh

    all_ok = (
        invariants["paper_session_registered"] is True
        and pnl_writer_registered
        and tick_fresh
    )
    return all_ok, invariants
