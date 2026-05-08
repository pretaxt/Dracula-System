"""统一通知派发器 — 同时推送 Telegram + 邮件。

调用方只需 import 此包，无需关心具体渠道。
"""
from __future__ import annotations

from decimal import Decimal

from app.notifications import email as _email
from app.notifications import telegram as _tg


def notify_position_opened(
    strategy: str,
    symbol: str,
    basis_pct: Decimal | str,
    notional_usd: Decimal | str,
) -> None:
    _tg.notify_position_opened(strategy, symbol, basis_pct, notional_usd)
    _email.notify_position_opened(strategy, symbol, basis_pct, notional_usd)


def notify_position_closed(
    strategy: str,
    symbol: str,
    realized_pnl: Decimal | str,
    exit_reason: str,
) -> None:
    _tg.notify_position_closed(strategy, symbol, realized_pnl, exit_reason)
    _email.notify_position_closed(strategy, symbol, realized_pnl, exit_reason)


def notify_perp_liquidated(
    symbol: str,
    side: str,
    quantity: Decimal | str,
    avg_price: Decimal | str,
) -> None:
    """永续单腿被强平 — 仅 Telegram（紧急告警，跳过邮件以加快推送）。"""
    _tg.notify_perp_liquidated(symbol, side, quantity, avg_price)


def notify_risk_violation(rule: str, message: str) -> None:
    _tg.notify_risk_violation(rule, message)
    _email.notify_risk_violation(rule, message)


def notify_system(message: str) -> None:
    _tg.notify_system(message)
    _email.notify_system(message)
