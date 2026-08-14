"""空口令 / CHANGE_ME 必须 fail-closed，禁止启动。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.security import (
    AuthNotConfiguredError,
    require_auth_configured,
    verify_password,
)


def _settings(token: str, secret: str) -> SimpleNamespace:
    return SimpleNamespace(api_token=token, api_secret_key=secret)


def test_require_auth_rejects_empty_token() -> None:
    with pytest.raises(AuthNotConfiguredError, match="Refusing to start"):
        require_auth_configured(_settings("", "a-real-secret-key"))


def test_require_auth_rejects_empty_secret() -> None:
    with pytest.raises(AuthNotConfiguredError, match="Refusing to start"):
        require_auth_configured(_settings("a-real-token", ""))


def test_require_auth_rejects_change_me() -> None:
    with pytest.raises(AuthNotConfiguredError, match="Refusing to start"):
        require_auth_configured(_settings("CHANGE_ME", "CHANGE_ME"))


def test_require_auth_accepts_real_secrets() -> None:
    require_auth_configured(_settings("not-a-placeholder", "another-real-secret"))


def test_verify_password_fails_when_token_unconfigured() -> None:
    fake = _settings("", "a-real-secret-key")
    with patch("app.core.security.get_settings", return_value=fake):
        assert verify_password("") is False
        assert verify_password("anything") is False
