"""Telegram 双向命令 bot — long-poll updates 并路由到处理器。

设计：
- HTTPx long-poll Telegram Bot API `/getUpdates`，timeout 25s。
- 仅响应 allowlisted chat_id（默认 = settings.telegram_chat_id）。
- 命令处理器通过依赖注入（CommandHandlers），便于单测。
- 任意一处异常不能炸死 poll loop（指数退避后重试）。
- 中英文命令并存：/balance 与 /余额 等价。

公开类：
    CommandHandlers      — 业务回调容器
    TelegramCommandBot   — 长轮询 + 命令路由

公开函数：
    parse_command(text)  — 单测友好的纯函数：拆出命令名
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


_API_BASE = "https://api.telegram.org/bot{token}"
_DEFAULT_POLL_TIMEOUT_S = 25
_BACKOFF_INITIAL_S = 2.0
_BACKOFF_MAX_S = 30.0


# ---------------------------------------------------------------------------
# 命令解析
# ---------------------------------------------------------------------------


# 中文同义命令 → 标准英文命令
_ZH_ALIASES: dict[str, str] = {
    "余额": "balance",
    "持仓": "positions",
    "暂停": "pause",
    "恢复": "resume",
    "状态": "status",
    "帮助": "help",
    "开始": "start",
}


def parse_command(text: str | None, bot_username: str | None = None) -> str | None:
    """从消息正文里提取标准化命令名（去 `/`、去 `@bot`、中文映射、小写）。

    返回 None 表示该消息不是命令（不以 `/` 起头）。
    """
    if not text:
        return None
    s = text.strip()
    if not s.startswith("/"):
        return None
    head = s.split()[0][1:]  # 去掉前导 `/`
    if "@" in head:
        cmd, _, suffix = head.partition("@")
        if bot_username and suffix.lower() != bot_username.lower():
            return None
        head = cmd
    if not head:
        return None
    if head in _ZH_ALIASES:
        return _ZH_ALIASES[head]
    return head.lower()


# ---------------------------------------------------------------------------
# 命令处理器容器
# ---------------------------------------------------------------------------


Handler = Callable[[], Awaitable[str]]


@dataclass
class CommandHandlers:
    """业务命令处理器集合 — 每个返回 Telegram 要发的纯文本字符串。"""

    balance: Handler
    positions: Handler
    pause: Handler
    resume: Handler
    status: Handler
    help: Handler


# ---------------------------------------------------------------------------
# Bot 主类
# ---------------------------------------------------------------------------


class TelegramCommandBot:
    """长轮询 Telegram Bot API，路由命令到 CommandHandlers。"""

    def __init__(
        self,
        token: str,
        allowed_chat_ids: set[str],
        handlers: CommandHandlers,
        bot_username: str | None = None,
        poll_timeout_seconds: int = _DEFAULT_POLL_TIMEOUT_S,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ValueError("token is required")
        if not allowed_chat_ids:
            raise ValueError("allowed_chat_ids must not be empty")
        self._token = token
        self._allowed = {str(x) for x in allowed_chat_ids}
        self._handlers = handlers
        self._bot_username = bot_username
        self._poll_timeout = poll_timeout_seconds
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            timeout=poll_timeout_seconds + 10,
        )
        self._offset = 0  # next update_id to fetch
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """启动后台 poll 任务（幂等）。"""
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._poll_forever(), name="telegram_command_bot"
        )
        logger.info("telegram_bot_started", allowed=len(self._allowed))

    async def stop(self) -> None:
        """优雅停止 poll 循环并关闭 HTTP client。"""
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._owns_client:
            try:
                await self._client.aclose()
            except Exception:
                pass
        logger.info("telegram_bot_stopped")

    # -- poll loop ---------------------------------------------------------

    async def _poll_forever(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        while not self._stopping:
            try:
                updates = await self._fetch_updates()
                backoff = _BACKOFF_INITIAL_S
                for upd in updates:
                    try:
                        await self._handle_update(upd)
                    except Exception:
                        logger.exception("telegram_update_handle_failed")
                    finally:
                        uid = upd.get("update_id")
                        if isinstance(uid, int) and uid >= self._offset:
                            self._offset = uid + 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "telegram_poll_failed_backoff", backoff_s=backoff, exc_info=True,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

    async def _fetch_updates(self) -> list[dict]:
        url = _API_BASE.format(token=self._token) + "/getUpdates"
        params = {
            "timeout": self._poll_timeout,
            "offset": self._offset,
            "allowed_updates": '["message"]',
        }
        r = await self._client.get(url, params=params)
        if r.status_code != 200:
            raise RuntimeError(
                f"getUpdates HTTP {r.status_code}: {r.text[:200]}"
            )
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"getUpdates not ok: {str(body)[:200]}")
        return body.get("result") or []

    # -- handler dispatch --------------------------------------------------

    async def _handle_update(self, update: dict) -> None:
        msg = update.get("message") or update.get("edited_message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        chat_id_s = str(chat_id)
        text = msg.get("text") or ""

        cmd = parse_command(text, bot_username=self._bot_username)
        if cmd is None:
            return

        if chat_id_s not in self._allowed:
            logger.warning(
                "telegram_unauthorized_chat",
                chat_id=chat_id_s, command=cmd,
            )
            await self._send(chat_id_s, "⛔ 此聊天未授权使用本 bot。")
            return

        handler = self._resolve(cmd)
        if handler is None:
            await self._send(
                chat_id_s,
                f"未知命令：/{cmd}\n发送 /help 查看可用命令。",
            )
            return

        logger.info("telegram_command_dispatch", chat_id=chat_id_s, command=cmd)
        try:
            reply = await handler()
        except Exception:
            logger.exception("telegram_handler_error", command=cmd)
            reply = f"⚠️ 处理 /{cmd} 失败，请查看后端日志。"
        await self._send(chat_id_s, reply or "(no output)")

    def _resolve(self, cmd: str) -> Handler | None:
        if cmd in ("start", "help"):
            return self._handlers.help
        return getattr(self._handlers, cmd, None)

    # -- send --------------------------------------------------------------

    async def _send(self, chat_id: str, text: str) -> None:
        url = _API_BASE.format(token=self._token) + "/sendMessage"
        try:
            r = await self._client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if r.status_code != 200:
                logger.warning(
                    "telegram_reply_failed",
                    status=r.status_code, body=r.text[:200],
                )
        except Exception:
            logger.warning("telegram_reply_error", exc_info=True)
