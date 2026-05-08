"""邮件报警推送服务。

使用内置 smtplib + asyncio.to_thread，无需额外依赖。
fire-and-forget 设计：发送失败不影响主流程。
"""
from __future__ import annotations

import asyncio
import smtplib
from decimal import Decimal
from email.mime.text import MIMEText

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _send_sync(subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[Dracula] {subject}"
    msg["From"] = settings.smtp_from_email or settings.smtp_user
    msg["To"] = settings.smtp_to_email or settings.smtp_user
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(msg["From"], [msg["To"]], msg.as_string())
    except Exception:
        logger.warning("email_send_error", exc_info=True)


def _fire(subject: str, body: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(asyncio.to_thread(_send_sync, subject, body))
    except RuntimeError:
        pass


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def notify_position_opened(
    strategy: str,
    symbol: str,
    basis_pct: Decimal | str,
    notional_usd: Decimal | str,
) -> None:
    subject = f"开仓 · {strategy} · {symbol}"
    body = (
        f"策略: {strategy}\n"
        f"标的: {symbol}\n"
        f"基差/费率: {basis_pct}%\n"
        f"名义价值: ${notional_usd}\n"
    )
    _fire(subject, body)


def notify_position_closed(
    strategy: str,
    symbol: str,
    realized_pnl: Decimal | str,
    exit_reason: str,
) -> None:
    pnl = Decimal(str(realized_pnl))
    prefix = "盈利平仓" if pnl >= 0 else "亏损平仓"
    subject = f"{prefix} · {strategy} · {symbol}"
    body = (
        f"策略: {strategy}\n"
        f"标的: {symbol}\n"
        f"已实现盈亏: ${pnl:+.4f}\n"
        f"平仓原因: {exit_reason}\n"
    )
    _fire(subject, body)


def notify_risk_violation(rule: str, message: str) -> None:
    subject = f"风控触发 · {rule}"
    body = f"规则: {rule}\n详情: {message}\n"
    _fire(subject, body)


def notify_system(message: str) -> None:
    _fire("系统通知", message)
