"""dgr_btc 每日 mirror divergence 检查 — in-process scheduler
================================================================
把 scripts/dgr_btc_mirror_check.py 的 main() 在 dracula-api 进程内
按 cron-like 节奏触发, 不依赖 host crontab / systemd timer.

调度:
  每天 UTC 00:00 trigger 一次, 跑完后 sleep 到下个 0 点.

监督:
  挂在 TaskSupervisor 下 (main.py lifespan), 异常自动 backoff 重启 +
  Telegram critical 告警; /health 接口可见任务状态.

为何不用 APScheduler:
  TaskSupervisor 已经提供 backoff + 告警 + /health, 加一个调度库属于过度工程.
  一个 while True + sleep_until_midnight 协程就够.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

from app.core.logging import get_logger

logger = get_logger(__name__)


_SCRIPT_PATH = Path("/app/scripts/dgr_btc_mirror_check.py")
_MODULE_NAME = "_dgr_btc_mirror_check_loaded"


def _load_mirror_check_module() -> ModuleType:
    """Dynamic import — scripts/ 不在 app package 路径下, 用 importlib."""
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 mirror_check module: {_SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    # 必须先注册到 sys.modules 再 exec — 否则 module 内部的 @dataclass 装饰器
    # 会在 sys.modules.get(cls.__module__) 拿到 None 然后崩 (Python 3.12+ 行为).
    sys.modules[_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def _seconds_until_next_utc_midnight() -> float:
    """到下一个 UTC 0 点剩多少秒 (>0)."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return (tomorrow - now).total_seconds()


# 连续失败 N 次后才抛给 supervisor (避免单次 API 抖动反复重启,
# 又防止"永远静默失败"盲区 — codex review follow-up #1, 2026-05-28)
_MAX_CONSECUTIVE_FAILURES = 3


async def run_forever() -> None:
    """每日 UTC 00:00 跑一次 mirror_check.main().

    被 TaskSupervisor 监督; 单次跑失败 swallow 后等下一天, 但连续
    _MAX_CONSECUTIVE_FAILURES 次失败必须 raise → supervisor 接管告警 + backoff,
    避免 P11 mirror 检查永远静默失败的可观测性盲区.
    若整个 module load 失败则抛 → supervisor 重启.
    """
    mod = _load_mirror_check_module()
    logger.info("mirror_scheduler_started", script_path=str(_SCRIPT_PATH))

    consecutive_failures = 0
    while True:
        sleep_s = _seconds_until_next_utc_midnight()
        next_run = datetime.now(timezone.utc) + timedelta(seconds=sleep_s)
        logger.info(
            "mirror_scheduler_sleeping",
            sleep_s=int(sleep_s),
            next_run_utc=next_run.isoformat(),
        )
        await asyncio.sleep(sleep_s)

        logger.info("mirror_scheduler_triggering")
        try:
            rc = await mod.main()
            logger.info("mirror_scheduler_completed", rc=rc)
            consecutive_failures = 0  # 成功重置
        except Exception:
            consecutive_failures += 1
            logger.exception(
                "mirror_scheduler_run_failed",
                consecutive_failures=consecutive_failures,
                max_consecutive=_MAX_CONSECUTIVE_FAILURES,
            )
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                # 连续失败超阈值: 抛给 supervisor 触发 Telegram critical 告警 + backoff
                logger.error(
                    "mirror_scheduler_raising_to_supervisor",
                    consecutive_failures=consecutive_failures,
                )
                raise
            # 单次/少数失败: swallow 等明天 (避免 API 抖动反复重启)
