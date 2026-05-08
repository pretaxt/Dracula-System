"""Telegram 报警推送服务。

fire-and-forget 设计：通知失败不影响主流程。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"


async def _send(text: str) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return
    url = _API_BASE.format(token=token)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
            if r.status_code != 200:
                logger.warning("telegram_send_failed", status=r.status_code, body=r.text[:200])
    except Exception:
        logger.warning("telegram_send_error", exc_info=True)


def _fire(text: str) -> None:
    """非阻塞发送：在当前事件循环中创建 task，不等待结果。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send(text))
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
    text = (
        f"🟢 <b>开仓</b>\n"
        f"策略: {strategy}\n"
        f"标的: {symbol}\n"
        f"基差: {basis_pct}%\n"
        f"名义价值: ${notional_usd}"
    )
    _fire(text)


def notify_position_closed(
    strategy: str,
    symbol: str,
    realized_pnl: Decimal | str,
    exit_reason: str,
) -> None:
    pnl = Decimal(str(realized_pnl))
    emoji = "✅" if pnl >= 0 else "🔴"
    text = (
        f"{emoji} <b>平仓</b>\n"
        f"策略: {strategy}\n"
        f"标的: {symbol}\n"
        f"已实现盈亏: ${pnl:+.4f}\n"
        f"原因: {exit_reason}"
    )
    _fire(text)


def notify_perp_liquidated(
    symbol: str,
    side: str,
    quantity: Decimal | str,
    avg_price: Decimal | str,
) -> None:
    """永续单腿被交易所强平 — 紧急告警（系统已自动平 spot 解除裸多）。"""
    text = (
        f"🚨 <b>永续强平</b>\n"
        f"标的: {symbol}\n"
        f"方向: {side}\n"
        f"数量: {quantity}\n"
        f"成交均价: {avg_price}\n"
        f"⚠️ 现货裸多敞口已自动平仓"
    )
    _fire(text)


def notify_risk_violation(rule: str, message: str) -> None:
    text = (
        f"⚠️ <b>风控触发</b>\n"
        f"规则: {rule}\n"
        f"详情: {message}"
    )
    _fire(text)


def notify_system(message: str) -> None:
    text = f"ℹ️ <b>系统通知</b>\n{message}"
    _fire(text)
