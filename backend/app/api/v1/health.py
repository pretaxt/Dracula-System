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

    if not all_healthy:
        # 任一 task 失败 → 503，docker-compose healthcheck 触发 restart
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ok" if all_healthy else "degraded",
        version="0.1.0",
        uptime_seconds=round(uptime_seconds, 1),
        startup_time=startup_time,
        all_tasks_healthy=all_healthy,
        task_count=task_count,
        failed_tasks=failed,
    )
