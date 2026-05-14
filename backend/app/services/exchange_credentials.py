"""交易所 API 凭据持久化服务

设计：
- env 变量是开发默认（如 BINANCE_API_KEY 在 .env）
- /app/state/exchange_credentials.json 是运行时凭据（UI POST 写入）
- main.py lifespan 加载文件优先于 env，主动覆盖 settings
- API 永不回传明文 secret（仅 masked 前 6 + 后 4 位）

文件结构::
    {
      "binance": {
        "api_key": "...",
        "api_secret": "...",
        "updated_at": "2026-05-09T08:30:00+00:00"
      },
      "okx": {
        "api_key": "...", "api_secret": "...", "passphrase": "...",
        "updated_at": "..."
      }
    }

权限：写入时 chmod 600（仅 owner 读，避免容器内进程外泄露）。
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_CREDENTIALS_PATH = Path(
    os.environ.get("EXCHANGE_CREDENTIALS_PATH", "/app/state/exchange_credentials.json")
)

# 字段白名单 — POST body 只允许这些 key 写入
_ALLOWED_FIELDS = {"api_key", "api_secret", "passphrase"}


def load_credentials() -> dict[str, dict[str, Any]]:
    """读凭据文件，返回 {exchange_name: {api_key, api_secret, passphrase, updated_at}}。
    文件不存在或损坏时返回空 dict。"""
    if not _CREDENTIALS_PATH.exists():
        return {}
    try:
        with _CREDENTIALS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("credentials_file_not_dict", path=str(_CREDENTIALS_PATH))
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("credentials_load_failed", path=str(_CREDENTIALS_PATH),
                       error=str(exc)[:120])
        return {}


def save_credentials(exchange: str, patch: dict[str, str]) -> dict[str, Any]:
    """合并 patch 到指定交易所，atomic 写入。返回该交易所最终条目（含 updated_at，仍是明文）。

    仅白名单字段被持久化；空字符串被忽略（不覆盖现有非空值）。"""
    filtered = {
        k: v for k, v in patch.items()
        if k in _ALLOWED_FIELDS and isinstance(v, str) and v.strip()
    }
    if not filtered:
        raise ValueError("No valid fields to save (need api_key / api_secret / passphrase)")

    existing = load_credentials()
    entry = existing.get(exchange, {})
    entry.update(filtered)
    entry["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing[exchange] = entry

    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".credentials_", suffix=".json.tmp",
        dir=str(_CREDENTIALS_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        # 写入前先 .bak 备份当前文件（防 mount 重置 / 误删 / 写失败回滚）
        if _CREDENTIALS_PATH.exists():
            try:
                bak_path = _CREDENTIALS_PATH.with_suffix(".json.bak")
                # 用 copy 而非 rename，原文件继续存在；不影响 atomic replace
                import shutil  # noqa: PLC0415
                shutil.copy2(str(_CREDENTIALS_PATH), str(bak_path))
                os.chmod(bak_path, stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                # 备份失败不阻塞主写
                logger.debug("credentials_backup_failed")
        os.replace(tmp_path, _CREDENTIALS_PATH)
        # chmod 600 — 仅文件所有者可读
        os.chmod(_CREDENTIALS_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("credentials_saved", exchange=exchange, fields=list(filtered.keys()))
    return entry


def mask_key(key: str) -> str:
    """API key 显示用：`abc123...wxyz`。<10 字符显示 `***`。"""
    if not key:
        return ""
    if len(key) < 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def list_credentials_metadata() -> list[dict[str, Any]]:
    """返回每个交易所的 metadata（带 masked key，永不回明文 secret）。

    用于 GET /api/v1/system/exchange-credentials。"""
    creds = load_credentials()
    out: list[dict[str, Any]] = []
    for exchange in ("binance", "okx", "bitget", "bybit", "htx", "hyperliquid"):
        entry = creds.get(exchange, {})
        configured = bool(entry.get("api_key") and entry.get("api_secret"))
        out.append({
            "exchange": exchange,
            "configured": configured,
            "api_key_preview": mask_key(entry.get("api_key", "")),
            "has_passphrase": bool(entry.get("passphrase")),
            "updated_at": entry.get("updated_at"),
        })
    return out


def get_exchange_credentials(exchange: str) -> dict[str, str]:
    """供 main.py lifespan 调用 — 返回明文凭据（不存在时空 dict）。"""
    creds = load_credentials()
    entry = creds.get(exchange, {})
    return {
        "api_key": entry.get("api_key", ""),
        "api_secret": entry.get("api_secret", ""),
        "passphrase": entry.get("passphrase", ""),
    }
