"""API v1 路由聚合

所有 v1 子路由在此注册后由 app/main.py 挂载到 /api/v1 前缀。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.health import router as health_router

router = APIRouter()
router.include_router(health_router)
