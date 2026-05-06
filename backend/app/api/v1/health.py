"""健康检查端点 — GET /api/v1/health"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """返回服务存活状态，供 Docker / k8s 探针使用。"""
    return HealthResponse(status="ok", version="0.1.0")
