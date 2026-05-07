"""健康检查端点 — GET /api/v1/health"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    startup_time: datetime | None = None


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """返回服务存活状态、版本和 uptime,供 Docker / k8s 探针 + 前端 SideNav 使用。"""
    startup_time: datetime | None = getattr(request.app.state, "startup_time", None)
    if startup_time is None:
        uptime_seconds = 0.0
    else:
        uptime_seconds = (datetime.now(timezone.utc) - startup_time).total_seconds()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=round(uptime_seconds, 1),
        startup_time=startup_time,
    )
