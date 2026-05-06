"""API v1 路由聚合 — 所有子路由在此注册。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.funding_rates import router as funding_rates_router
from app.api.v1.health import router as health_router
from app.api.v1.paper import router as paper_router
from app.api.v1.positions import router as positions_router
from app.api.v1.risk import router as risk_router
from app.api.v1.strategies import router as strategies_router

# 无需鉴权的路由
router = APIRouter()
router.include_router(health_router)
router.include_router(auth_router)

# 需要 JWT 的路由
_protected = APIRouter(dependencies=[Depends(get_current_user)])
_protected.include_router(paper_router)
_protected.include_router(positions_router)
_protected.include_router(funding_rates_router)
_protected.include_router(dashboard_router)
_protected.include_router(strategies_router)
_protected.include_router(risk_router)

router.include_router(_protected)
