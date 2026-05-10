"""Reconciliation 路由 — GET /reconciliation/status, POST /reconciliation/run-once。

实时余额 + 持仓对账状态查询。前端 Dashboard 可直读 cache 替代 lazy fetch。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import CurrentUser

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/status")
async def get_status(_: CurrentUser, request: Request) -> dict:
    """返回 BalanceReconcilerService 当前 snapshot：余额 cache、持仓 cache、最近告警。"""
    svc = getattr(request.app.state, "balance_reconciler", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="reconciler not initialized")
    return svc.snapshot()


@router.post("/run-once")
async def run_once(_: CurrentUser, request: Request) -> dict:
    """手动触发一次对账（不等下个周期）。用于调试 / 紧急刷新。"""
    svc = getattr(request.app.state, "balance_reconciler", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="reconciler not initialized")
    return await svc.run_once()
