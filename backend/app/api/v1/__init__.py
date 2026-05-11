"""API v1 路由聚合 — 所有子路由在此注册。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.v1.account import router as account_router
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.funding_rates import router as funding_rates_router
from app.api.v1.health import router as health_router
from app.api.v1.orders import router as orders_router
from app.api.v1.paper import router as paper_router
from app.api.v1.positions import router as positions_router
from app.api.v1.risk import router as risk_router
from app.api.v1.strategies import router as strategies_router
from app.api.v1.system import router as system_router
from app.api.v1.market import router as market_router
from app.api.v1.backtest import router as backtest_router
from app.api.v1.reconciliation import router as reconciliation_router
from app.api.v1.cex_dex import router as cex_dex_router

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
_protected.include_router(orders_router)
_protected.include_router(account_router)
_protected.include_router(system_router)
_protected.include_router(backtest_router)
_protected.include_router(reconciliation_router)
_protected.include_router(cex_dex_router)

router.include_router(_protected)

# market_router 独立挂载 — 避免 router-level OAuth2PasswordBearer
# 在 WebSocket 路由上抛 TypeError(missing 'request' arg)。
# HTTP endpoints 自身已用 `_: CurrentUser` 做鉴权；WS 路由用 ?token= query 参数自验。
router.include_router(market_router)
