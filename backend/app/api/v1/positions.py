"""Positions 路由 — GET /positions, GET /positions/{uuid}, POST /positions/{uuid}/close。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser, DbSession
from app.api.v1.schemas.positions import (
    CloseRequest,
    CloseResponse,
    PositionListMeta,
    PositionListResponse,
    PositionOut,
)
from app.services.position_service import compute_days_held, get_position, list_positions

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=PositionListResponse)
async def get_positions(
    _: CurrentUser,
    db: DbSession,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> PositionListResponse:
    records, total = await list_positions(db, status=status, page=page, page_size=page_size)
    data = [PositionOut.from_record(r, compute_days_held(r)) for r in records]
    return PositionListResponse(
        data=data,
        meta=PositionListMeta(page=page, page_size=page_size, total=total),
    )


@router.get("/{uuid}", response_model=PositionOut)
async def get_position_detail(_: CurrentUser, db: DbSession, uuid: str) -> PositionOut:
    rec = await get_position(db, uuid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return PositionOut.from_record(rec, compute_days_held(rec))


@router.post("/{uuid}/close", response_model=CloseResponse)
async def close_position(
    _: CurrentUser,
    db: DbSession,
    request: Request,
    uuid: str,
    body: CloseRequest,
) -> CloseResponse:
    if not body.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true")

    rec = await get_position(db, uuid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if rec.status == "closed":
        raise HTTPException(status_code=409, detail="Position already closed")

    paper_session = getattr(request.app.state, "paper_session", None)
    if paper_session is not None and hasattr(paper_session, "close_position"):
        await paper_session.close_position(uuid, reason=body.reason)
        msg = "close request submitted"
    else:
        msg = "paper session not running; DB record unchanged"

    return CloseResponse(uuid=uuid, status="closing", message=msg)
