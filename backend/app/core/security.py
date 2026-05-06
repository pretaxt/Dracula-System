"""JWT 鉴权工具 — 编码 / 解码 / 验证

单租户管理员模型：无用户表，API_TOKEN 即管理员密码。
"""
from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt  # noqa: F401  (JWTError re-exported for callers)
from pydantic import BaseModel

from app.core.config import get_settings

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 24


class TokenData(BaseModel):
    sub: str
    exp: datetime


def create_access_token(sub: str = "admin") -> tuple[str, datetime]:
    """生成 JWT，返回 (token, expires_at)。"""
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_TOKEN_EXPIRE_HOURS)
    payload: dict[str, Any] = {"sub": sub, "exp": expires_at}
    token = jwt.encode(payload, settings.api_secret_key, algorithm=_ALGORITHM)
    return token, expires_at


def decode_token(token: str) -> TokenData:
    """解码并验证 JWT，失败时抛出 JWTError。"""
    settings = get_settings()
    payload = jwt.decode(token, settings.api_secret_key, algorithms=[_ALGORITHM])
    return TokenData(sub=payload["sub"], exp=payload["exp"])


def verify_password(plain: str) -> bool:
    """常量时间比较，防止时序攻击。"""
    settings = get_settings()
    expected = settings.api_token or ""
    return hmac.compare_digest(plain.encode(), expected.encode())
