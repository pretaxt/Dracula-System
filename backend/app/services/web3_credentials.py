"""Web3 凭据持久化服务 — 存储 CEX-DEX 套利所需的钱包私钥和 RPC URL。

文件位置: /app/state/web3_credentials.json (volume mount，持久化)
权限: chmod 600，私钥不回传前端。
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
    os.environ.get("WEB3_CREDENTIALS_PATH", "/app/state/web3_credentials.json")
)


def load_web3_credentials() -> dict[str, Any]:
    """返回 {private_key, rpc_url, updated_at}，文件不存在时返回空 dict。"""
    if not _CREDENTIALS_PATH.exists():
        return {}
    try:
        with _CREDENTIALS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("web3_credentials_load_failed", error=str(exc)[:120])
        return {}


def save_web3_credentials(private_key: str, rpc_url: str) -> None:
    """将凭据原子写入 state 文件，chmod 600。"""
    data = {
        "private_key": private_key,
        "rpc_url": rpc_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_CREDENTIALS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp_path, _CREDENTIALS_PATH)
        logger.info("web3_credentials_saved")
    except Exception:
        os.unlink(tmp_path)
        raise


def get_web3_credentials_meta() -> dict[str, Any]:
    """返回脱敏元数据（不含私钥明文）。"""
    data = load_web3_credentials()
    if not data:
        return {"configured": False, "wallet_address": None, "rpc_url_preview": None, "updated_at": None}

    private_key = data.get("private_key", "")
    rpc_url = data.get("rpc_url", "")

    # 从私钥派生地址
    wallet_address: str | None = None
    if private_key:
        try:
            from eth_account import Account  # noqa: PLC0415
            wallet_address = Account.from_key(private_key).address
        except Exception:
            wallet_address = None

    # RPC URL 脱敏：只显示 key 前 6 后 4
    rpc_preview = _mask_rpc_url(rpc_url)

    return {
        "configured": bool(private_key and rpc_url),
        "wallet_address": wallet_address,
        "rpc_url_preview": rpc_preview,
        "updated_at": data.get("updated_at"),
    }


def _mask_rpc_url(url: str) -> str:
    if not url:
        return ""
    # 找 /v2/ 之后的 key 部分
    import re  # noqa: PLC0415
    m = re.match(r"(https://[^/]+/v2/)(.{6})(.*)(.{4})$", url)
    if m:
        return f"{m.group(1)}{m.group(2)}...{m.group(4)}"
    return url[:30] + "..." if len(url) > 30 else url
