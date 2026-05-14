"""通知渠道凭据与开关的持久化服务。

凭据存储到 /app/state/notifications.json（chmod 600）。
运行时热读取 — 无需重启容器。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_STATE_DIR = Path(os.getenv("DRACULA_STATE_DIR", "/app/state"))
_NOTIF_FILE = _STATE_DIR / "notifications.json"

_DEFAULTS: dict = {
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "email_enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from_email": "",
    "smtp_to_email": "",
    "updated_at": None,
}


def _read() -> dict:
    if _NOTIF_FILE.exists():
        try:
            raw = json.loads(_NOTIF_FILE.read_text())
            return {**_DEFAULTS, **raw}
        except Exception:
            pass
    return dict(_DEFAULTS)


def _write(data: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _NOTIF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.rename(_NOTIF_FILE)


def get_notification_config() -> dict:
    """返回完整通知配置（含敏感字段，内部使用）。"""
    return _read()


def get_notification_meta() -> dict:
    """返回脱敏配置，适合 API 暴露。"""
    cfg = _read()
    token = cfg.get("telegram_bot_token", "")
    return {
        "telegram_enabled": cfg["telegram_enabled"],
        "telegram_bot_token_preview": (token[:8] + "…") if token else "",
        "telegram_chat_id": cfg["telegram_chat_id"],
        "email_enabled": cfg["email_enabled"],
        "smtp_host": cfg["smtp_host"],
        "smtp_port": cfg["smtp_port"],
        "smtp_user": cfg["smtp_user"],
        "smtp_password_set": bool(cfg["smtp_password"]),
        "smtp_from_email": cfg["smtp_from_email"],
        "smtp_to_email": cfg["smtp_to_email"],
        "updated_at": cfg["updated_at"],
    }


def save_notification_config(patch: dict) -> None:
    """合并更新通知配置，未传字段不覆盖。空字符串 '' 视为清空。"""
    cfg = _read()
    allowed = set(_DEFAULTS.keys()) - {"updated_at"}
    for k, v in patch.items():
        if k in allowed:
            cfg[k] = v
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write(cfg)
