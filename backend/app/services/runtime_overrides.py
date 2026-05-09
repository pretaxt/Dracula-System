"""运行时配置覆盖 — 把 UI 改动持久化到 JSON 文件，重启不丢。

设计：
- YAML 是声明式默认值（开发时编辑、git 跟踪）
- /app/state/overrides.json 是运行时状态（API 写入、容器重启加载）
- lifespan 启动时：加载 YAML → 应用 overrides → 传给 session_factory + scanner
- /risk/limits PATCH：更新内存（立即生效）+ 持久化 JSON（重启可恢复）

文件路径：
- 容器内 /app/state/overrides.json
- 宿主机 /opt/dracula/state/overrides.json（docker volume RW mount）
- 不存在或损坏时返回 {}（应用 YAML 原值）
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 容器内挂载点（docker-compose: ./state:/app/state）
_OVERRIDES_PATH = Path(os.environ.get("RUNTIME_OVERRIDES_PATH", "/app/state/overrides.json"))


def load_overrides() -> dict[str, Any]:
    """读 overrides 文件，返回 dict。文件不存在或损坏时返回空 dict。"""
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        with _OVERRIDES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("overrides_file_not_dict", path=str(_OVERRIDES_PATH))
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("overrides_load_failed", path=str(_OVERRIDES_PATH), error=str(exc)[:120])
        return {}


def save_overrides(patch: dict[str, Any]) -> None:
    """合并 patch 到现有 overrides 并持久化（atomic write via tmpfile + rename）。

    仅持久化白名单字段，避免误存敏感数据。"""
    allowed = {"min_apr_pct", "max_positions", "max_total_notional_usd",
               "stop_loss_pct", "max_hold_hours"}
    filtered = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if not filtered:
        return

    existing = load_overrides()
    merged = {**existing, **filtered}

    try:
        _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 写到同目录 tmp 文件后 rename，保证原子性
        fd, tmp_path = tempfile.mkstemp(
            prefix=".overrides_", suffix=".json.tmp",
            dir=str(_OVERRIDES_PATH.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, _OVERRIDES_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info("overrides_saved", path=str(_OVERRIDES_PATH), keys=list(filtered.keys()))
    except Exception as exc:
        logger.exception("overrides_save_failed", error=str(exc))


# ---------------------------------------------------------------------------
# spot-perp 子节（D.1.5）
# ---------------------------------------------------------------------------


_SPOT_PERP_ALLOWED = {
    "entry_pct", "exit_pct", "max_hold_hours", "min_hold_minutes",
    "max_concurrent", "notional_per_position", "direction_filter",
    "scan_threshold_pct", "candidate_symbols", "exchanges",
}


def save_spot_perp_overrides(patch: dict[str, Any]) -> None:
    """合并 spot-perp patch 到 overrides.json 的 ``spot_perp`` 子节。

    与 save_overrides 共用同一文件，原子写入。"""
    filtered = {
        k: v for k, v in patch.items()
        if k in _SPOT_PERP_ALLOWED and v is not None
    }
    if not filtered:
        return

    existing = load_overrides()
    sp_existing = existing.get("spot_perp") or {}
    if not isinstance(sp_existing, dict):
        sp_existing = {}
    merged_sp = {**sp_existing, **filtered}
    merged = {**existing, "spot_perp": merged_sp}

    try:
        _OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".overrides_", suffix=".json.tmp",
            dir=str(_OVERRIDES_PATH.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, _OVERRIDES_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info(
            "spot_perp_overrides_saved",
            path=str(_OVERRIDES_PATH),
            keys=list(filtered.keys()),
        )
    except Exception as exc:
        logger.exception("spot_perp_overrides_save_failed", error=str(exc))


def apply_to_strategy_cfg(cfg: dict, overrides: dict[str, Any]) -> dict:
    """把 overrides 合并到 strategy_cfg dict（YAML 加载结果）。返回合并后的 cfg。

    映射规则：
      min_apr_pct          → cfg['entry']['min_apr_pct']
      max_positions        → cfg['position']['max_positions']
      max_total_notional_usd → cfg['risk']['max_total_notional_usd']
      stop_loss_pct        → cfg['risk']['stop_loss_pct']
      max_hold_hours       → cfg['exit']['max_hold_hours']
    """
    if not overrides:
        return cfg
    if "min_apr_pct" in overrides:
        cfg.setdefault("entry", {})["min_apr_pct"] = overrides["min_apr_pct"]
    if "max_positions" in overrides:
        cfg.setdefault("position", {})["max_positions"] = int(overrides["max_positions"])
    if "max_total_notional_usd" in overrides:
        cfg.setdefault("risk", {})["max_total_notional_usd"] = overrides["max_total_notional_usd"]
    if "stop_loss_pct" in overrides:
        cfg.setdefault("risk", {})["stop_loss_pct"] = overrides["stop_loss_pct"]
    if "max_hold_hours" in overrides:
        cfg.setdefault("exit", {})["max_hold_hours"] = overrides["max_hold_hours"]
    return cfg
