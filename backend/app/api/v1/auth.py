"""Auth 路由 — POST /auth/login，GET /auth/me。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, oauth2_scheme
from app.api.v1.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.core.security import create_access_token, decode_token, verify_password
from jose import JWTError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """用户名固定为 admin，密码为 .env 中的 API_TOKEN。"""
    if body.username != "admin" or not verify_password(body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    token, expires_at = create_access_token()
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser) -> MeResponse:
    return MeResponse(username=user.sub, expires_at=user.exp)
