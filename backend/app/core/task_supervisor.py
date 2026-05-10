"""TaskSupervisor — 监督 long-running asyncio task，故障时告警 + 自动重启。

之前所有 main.py lifespan 创建的 task 都是裸 `asyncio.create_task(...)`：
- 任何未捕获异常 → task done，container 不重启（docker `restart: unless-stopped`
  看的是进程不是 task）
- /health 永远 200，监控完全失效
- 日志一行 traceback 后悄无声息

本模块解决：
1. 包装 `_supervised_loop` — 把 task 主体放在 while True + try/except 里
   重启策略：指数 backoff (5/30/300s)
2. 每个 task 注册到 `app.state.supervised_tasks`，/health endpoint 可遍历
3. 异常 → Telegram critical 告警

用法:
    supervisor = TaskSupervisor(app)
    supervisor.spawn("funding_rate_runner", runner.run_forever)
    # /health 自动检测 task 健康度
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SupervisedTask:
    """单个被监督的 task 状态。"""
    name: str
    task: asyncio.Task
    restart_count: int = 0
    last_exception: str | None = None
    last_exception_at: datetime | None = None
    crash_history: list[dict] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return not self.task.done()

    @property
    def is_failed(self) -> bool:
        """task done 且最后一次抛了异常（非主动 cancel）"""
        if not self.task.done():
            return False
        try:
            exc = self.task.exception()
            return exc is not None and not isinstance(exc, asyncio.CancelledError)
        except asyncio.CancelledError:
            return False
        except asyncio.InvalidStateError:
            return False


class TaskSupervisor:
    """监督 long-running asyncio task，故障时告警 + 自动重启。"""

    # 重启 backoff 序列（秒）— 渐进退避
    _BACKOFF_SCHEDULE = [5, 30, 300]
    _MAX_RESTARTS = 10  # 超过此值停止重启（防 crash loop）

    def __init__(self) -> None:
        self._tasks: dict[str, SupervisedTask] = {}

    def spawn(
        self,
        name: str,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        auto_restart: bool = True,
    ) -> SupervisedTask:
        """启动一个被监督的 task。

        Parameters
        ----------
        name: task 唯一名（lifespan 重启时区分）
        coro_factory: 无参数返回 coroutine 的 callable（每次重启都重新调用）
        auto_restart: True = crash 后自动重启；False = 仅告警不重启
        """
        async def _supervised_wrapper() -> None:
            attempt = 0
            while attempt < self._MAX_RESTARTS:
                try:
                    await coro_factory()
                    # 正常返回（如 task 主体自己 return） — 不重启
                    logger.info("supervised_task_completed", name=name)
                    return
                except asyncio.CancelledError:
                    logger.info("supervised_task_cancelled", name=name)
                    raise
                except Exception as exc:
                    attempt += 1
                    rec = self._tasks.get(name)
                    if rec is not None:
                        rec.restart_count = attempt
                        rec.last_exception = f"{type(exc).__name__}: {str(exc)[:200]}"
                        rec.last_exception_at = datetime.now(timezone.utc)
                        rec.crash_history.append({
                            "ts": rec.last_exception_at.isoformat(),
                            "error": rec.last_exception,
                        })
                        # 仅保留最近 10 条
                        if len(rec.crash_history) > 10:
                            rec.crash_history = rec.crash_history[-10:]
                    logger.exception(
                        "supervised_task_crashed",
                        name=name, restart_count=attempt,
                    )
                    self._notify_critical(name, exc, attempt)
                    if not auto_restart or attempt >= self._MAX_RESTARTS:
                        logger.error(
                            "supervised_task_giving_up",
                            name=name, restart_count=attempt,
                            auto_restart=auto_restart,
                        )
                        return
                    backoff = self._BACKOFF_SCHEDULE[
                        min(attempt - 1, len(self._BACKOFF_SCHEDULE) - 1)
                    ]
                    logger.warning(
                        "supervised_task_restarting",
                        name=name, attempt=attempt + 1, backoff_s=backoff,
                    )
                    await asyncio.sleep(backoff)

        task = asyncio.create_task(_supervised_wrapper(), name=name)
        rec = SupervisedTask(name=name, task=task)
        self._tasks[name] = rec
        logger.info("supervised_task_spawned", name=name)
        return rec

    def register(self, name: str, task: asyncio.Task) -> SupervisedTask:
        """注册已存在的 task 仅做监控（不接管重启）。

        最小侵入模式：保留 lifespan 现有 create_task 调用，加 done_callback
        监测 + Telegram 告警 + /health 反映。完整 auto-restart 用 spawn()。
        """
        rec = SupervisedTask(name=name, task=task)
        self._tasks[name] = rec

        def _done_callback(t: asyncio.Task) -> None:
            try:
                exc = t.exception()
            except asyncio.CancelledError:
                logger.info("registered_task_cancelled", name=name)
                return
            except asyncio.InvalidStateError:
                return
            if exc is not None and not isinstance(exc, asyncio.CancelledError):
                rec.last_exception = f"{type(exc).__name__}: {str(exc)[:200]}"
                rec.last_exception_at = datetime.now(timezone.utc)
                rec.crash_history.append({
                    "ts": rec.last_exception_at.isoformat(),
                    "error": rec.last_exception,
                })
                logger.error(
                    "registered_task_died",
                    name=name, error=rec.last_exception,
                )
                self._notify_critical(name, exc, attempt=0)

        task.add_done_callback(_done_callback)
        logger.info("supervised_task_registered", name=name)
        return rec

    def get(self, name: str) -> SupervisedTask | None:
        return self._tasks.get(name)

    def all_tasks(self) -> list[SupervisedTask]:
        return list(self._tasks.values())

    def health_snapshot(self) -> dict[str, Any]:
        """供 /health endpoint 直读的快照。"""
        tasks_info = []
        any_failed = False
        for rec in self._tasks.values():
            failed = rec.is_failed
            any_failed = any_failed or failed
            tasks_info.append({
                "name": rec.name,
                "running": rec.is_running,
                "failed": failed,
                "restart_count": rec.restart_count,
                "last_exception": rec.last_exception,
                "last_exception_at": (
                    rec.last_exception_at.isoformat() if rec.last_exception_at else None
                ),
            })
        return {
            "all_healthy": not any_failed,
            "task_count": len(self._tasks),
            "tasks": tasks_info,
        }

    async def stop_all(self) -> None:
        """优雅关闭所有 task。"""
        for rec in self._tasks.values():
            if not rec.task.done():
                rec.task.cancel()
        for rec in self._tasks.values():
            try:
                await rec.task
            except (asyncio.CancelledError, Exception):
                pass

    def _notify_critical(self, name: str, exc: Exception, attempt: int) -> None:
        """告警到 Telegram（best-effort）。"""
        try:
            from app.notifications import notify_reconcile_alert  # noqa: PLC0415
            notify_reconcile_alert(
                alert_type="supervised_task_crashed",
                severity="critical",
                exchange="*",
                symbol=name,
                explanation=(
                    f"task '{name}' 第 {attempt} 次崩溃: "
                    f"{type(exc).__name__}: {str(exc)[:120]}"
                ),
            )
        except Exception:
            logger.debug("supervised_task_telegram_failed", name=name)
