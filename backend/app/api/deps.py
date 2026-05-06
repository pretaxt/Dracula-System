"""FastAPI 依赖注入 — 鉴权、DB session、app.state 访问。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import TokenData, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> TokenData:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        return decode_token(token)
    except JWTError:
        raise credentials_exception


async def get_db(request: Request) -> AsyncSession:  # type: ignore[misc]
    async with get_session() as session:
        yield session


def get_app_state(request: Request):
    return request.app.state


CurrentUser = Annotated[TokenData, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
