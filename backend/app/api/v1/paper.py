"""纸交易监控端点

GET  /api/v1/paper/status  — 返回当前纸交易会话的实时快照
POST /api/v1/paper/stop    — 请求停止纸交易循环（优雅退出）
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/paper", tags=["paper-trading"])


# ---------------------------------------------------------------------------
# 响应模型（Decimal → str 序列化，避免 JSON 精度损失）
# ---------------------------------------------------------------------------


class PaperStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    timestamp: datetime
    open_positions: int
    total_notional_usd: str
    unrealized_pnl_usd: str
    total_funding_usd: str
    total_fees_usd: str
    total_trades: int
    net_pnl_usd: str
    running: bool


class PaperStopResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.get("/status", response_model=PaperStatusResponse)
async def get_paper_status(request: Request) -> PaperStatusResponse:
    """返回当前纸交易会话快照。

    若会话未启动（适配器初始化失败），返回 503。
    """
    session = getattr(request.app.state, "paper_session", None)
    if session is None:
        raise HTTPException(
            status_code=503,
            detail="纸交易会话未运行（交易所适配器未就绪或已被禁用）",
        )

    snap = session.status()
    return PaperStatusResponse(
        timestamp=snap.timestamp,
        open_positions=snap.open_positions,
        total_notional_usd=str(snap.total_notional_usd),
        unrealized_pnl_usd=str(snap.unrealized_pnl_usd),
        total_funding_usd=str(snap.total_funding_usd),
        total_fees_usd=str(snap.total_fees_usd),
        total_trades=snap.total_trades,
        net_pnl_usd=str(snap.net_pnl_usd),
        running=getattr(session, "_running", False),
    )


@router.post("/stop", response_model=PaperStopResponse)
async def stop_paper_session(request: Request) -> PaperStopResponse:
    """请求纸交易循环优雅停止。"""
    session = getattr(request.app.state, "paper_session", None)
    if session is None:
        raise HTTPException(status_code=503, detail="纸交易会话未运行")

    await session.stop()
    return PaperStopResponse(message="停止信号已发送，当前 tick 完成后退出")
