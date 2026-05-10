"""把 Dracula app_state 包装成 TelegramCommandBot 所需的 CommandHandlers。

每个 handler 在被调用时**实时读** app_state（不缓存），这样 pause/resume 后
后续 /status 能反映最新状态。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.notifications.telegram_bot import CommandHandlers
from app.services import balance_service, strategy_control

logger = get_logger(__name__)


_HELP_TEXT = (
    "<b>Dracula Bot 命令</b>\n"
    "/balance  /余额      —  各交易所余额\n"
    "/positions  /持仓    —  当前持仓\n"
    "/pause  /暂停        —  暂停纸交易\n"
    "/resume  /恢复       —  恢复纸交易\n"
    "/status  /状态       —  汇总状态\n"
    "/help  /帮助         —  本帮助"
)


def _fmt_usd(v: Decimal | float | int | None) -> str:
    if v is None:
        return "—"
    try:
        return f"${Decimal(str(v)):,.2f}"
    except Exception:
        return str(v)


def _is_paper_running(app_state) -> bool:
    sess = getattr(app_state, "paper_session", None)
    task = getattr(app_state, "paper_task", None)
    return sess is not None and task is not None and not task.done()


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _balance_handler(app_state) -> str:
    adapters: dict[str, Any] = getattr(app_state, "adapters", {}) or {}
    if not adapters:
        return "⚠️ 尚未初始化任何交易所适配器。"
    per_ex = await balance_service.get_per_exchange_equity(adapters)
    total = sum(per_ex.values(), Decimal("0"))
    lines = ["<b>💰 账户余额</b>"]
    for name, val in sorted(per_ex.items()):
        lines.append(f"  {name.upper():<8}{_fmt_usd(val)}")
    lines.append(f"  <b>合计   {_fmt_usd(total)}</b>")
    return "\n".join(lines)


def _format_position_line(pos) -> str:
    """单个仓位的紧凑展示。Position 来自 risk.models。"""
    sym = str(pos.symbol)
    upnl = pos.unrealized_pnl
    fund = pos.funding_received
    hours = pos.holding_hours
    return (
        f"  <code>{sym:<14}</code> "
        f"${pos.notional_usd:.0f}N  uPnL{upnl:+.2f}  "
        f"funding{fund:+.2f}  hold{hours}h"
    )


async def _positions_handler(app_state) -> str:
    """合并展示 #01（资金费率）+ #02（跨所基差）+ #04（期现）。"""
    fr_lines: list[str] = []
    fr_count = 0
    sess = getattr(app_state, "paper_session", None)
    if sess is not None:
        try:
            fr_open = list(sess._manager.open_positions)
            fr_count = len(fr_open)
            for pos in fr_open:
                fr_lines.append(_format_position_line(pos))
        except Exception:
            logger.exception("positions_handler_funding_rate_read_failed")
            fr_lines.append("  ⚠️ 资金费率持仓读取失败")

    sp_lines: list[str] = []
    sp_count = 0
    try:
        sp_count, sp_lines = await _read_spot_perp_open_rows()
    except Exception:
        logger.exception("positions_handler_spot_perp_read_failed")
        sp_lines.append("  ⚠️ 期现持仓读取失败")

    pb_lines: list[str] = []
    pb_count = 0
    try:
        pb_count, pb_lines = await _read_perp_basis_open_rows()
    except Exception:
        logger.exception("positions_handler_perp_basis_read_failed")
        pb_lines.append("  ⚠️ 跨所基差持仓读取失败")

    total = fr_count + sp_count + pb_count
    if total == 0:
        return "📭 当前无持仓。"

    out = [f"<b>📊 当前持仓 ({total})</b>"]
    if fr_count:
        out.append(f"<b>—— #01 资金费率 ({fr_count}) ——</b>")
        out.extend(fr_lines)
    if pb_count:
        out.append(f"<b>—— #02 跨所基差 ({pb_count}) ——</b>")
        out.extend(pb_lines)
    if sp_count:
        out.append(f"<b>—— #04 期现套利 ({sp_count}) ——</b>")
        out.extend(sp_lines)
    return "\n".join(out)


async def _read_spot_perp_open_rows() -> tuple[int, list[str]]:
    """从 DB 读 spot_perp_main 的 open 行并格式化为可读行。"""
    from app.core.database import get_session  # noqa: PLC0415
    from app.models.position import PositionRecord  # noqa: PLC0415
    from app.strategies.spot_perp_basis.paper_trading import _decode_notes  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    out: list[str] = []
    async with get_session() as session:
        stmt = (
            select(PositionRecord)
            .where(PositionRecord.strategy_instance == "spot_perp_main")
            .where(PositionRecord.status == "open")
        )
        rows = (await session.execute(stmt)).scalars().all()
        for r in rows:
            sym, _meta = _decode_notes(r.notes or "")
            entry_basis = r.target_apr_pct or Decimal("0")
            upnl = r.unrealized_pnl or Decimal("0")
            notional = r.notional_usd or Decimal("0")
            out.append(
                f"  <code>{sym:<14}</code> "
                f"${notional:.0f}N  entry{entry_basis:+.3f}%  uPnL{upnl:+.3f}"
            )
        return len(rows), out


async def _read_perp_basis_open_rows() -> tuple[int, list[str]]:
    """从 DB 读 perp_basis_main 的 open 行并格式化为跨所双腿可读行。"""
    from app.core.database import get_session  # noqa: PLC0415
    from app.models.position import PositionRecord, PositionLegRecord  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    out: list[str] = []
    async with get_session() as session:
        stmt = (
            select(PositionRecord)
            .where(PositionRecord.strategy_instance == "perp_basis_main")
            .where(PositionRecord.status == "open")
        )
        rows = (await session.execute(stmt)).scalars().all()
        for r in rows:
            # 读 legs 拼出 long/short 交易所
            leg_stmt = select(PositionLegRecord).where(
                PositionLegRecord.position_id == r.id,
            )
            legs = (await session.execute(leg_stmt)).scalars().all()
            long_ex = next((l.exchange for l in legs if (l.side or "").lower() == "long"), "?")
            short_ex = next((l.exchange for l in legs if (l.side or "").lower() == "short"), "?")
            sym = str(r.symbol)
            entry_diff = r.target_apr_pct or Decimal("0")
            upnl = r.unrealized_pnl or Decimal("0")
            funding = r.funding_received or Decimal("0")
            notional = r.notional_usd or Decimal("0")
            out.append(
                f"  <code>{sym:<10}</code> "
                f"{long_ex}/{short_ex} ${notional:.0f}N "
                f"diff{entry_diff:+.1f}%  uPnL{upnl:+.2f}  fund{funding:+.2f}"
            )
        return len(rows), out


def _is_perp_basis_running(app_state) -> bool:
    sess = getattr(app_state, "perp_basis_paper", None)
    task = getattr(app_state, "perp_basis_paper_task", None)
    return sess is not None and task is not None and not task.done()


async def _pause_handler(app_state) -> str:
    """暂停所有纸交易 session（#01 + #02 + #04）。"""
    actions: list[str] = []
    if _is_paper_running(app_state):
        if await strategy_control.stop_paper(app_state):
            actions.append("#01")
    if _is_perp_basis_running(app_state):
        if await strategy_control.stop_perp_basis_paper(app_state):
            actions.append("#02")
    # #04 spot_perp 暂停由独立 toggle 控制（live_mode 不通过 stop session 体现）
    if not actions:
        return "ℹ️ 策略已处于暂停状态。"
    return f"⏸ 已暂停 {' + '.join(actions)} 纸交易 session。"


async def _resume_handler(app_state) -> str:
    """恢复所有可恢复的纸交易 session。"""
    actions: list[str] = []
    if not _is_paper_running(app_state):
        if await strategy_control.start_paper(app_state):
            actions.append("#01")
    if not _is_perp_basis_running(app_state):
        if await strategy_control.start_perp_basis_paper(app_state):
            actions.append("#02")
    if not actions:
        return "ℹ️ 策略已在运行中（幂等）。"
    return f"▶️ 已恢复 {' + '.join(actions)} 纸交易 session。"


async def _status_handler(app_state) -> str:
    from app.core.config import get_settings  # noqa: PLC0415
    settings = get_settings()
    mode = settings.trading_mode.lower()
    fr_running = _is_paper_running(app_state)

    sess = getattr(app_state, "paper_session", None)
    fr_count = 0
    if sess is not None:
        try:
            fr_count = len(sess._manager.open_positions)
        except Exception:
            fr_count = -1

    # #04 spot-perp session 状态（独立于 #01）
    sp_session = getattr(app_state, "spot_perp_paper", None)
    sp_task = getattr(app_state, "spot_perp_task", None)
    sp_running = (
        sp_session is not None and sp_task is not None and not sp_task.done()
    )
    sp_count = 0
    try:
        sp_count, _ = await _read_spot_perp_open_rows()
    except Exception:
        sp_count = -1

    # #02 perp-basis session 状态（跨所 perp+perp）
    pb_running = _is_perp_basis_running(app_state)
    pb_count = 0
    try:
        pb_count, _ = await _read_perp_basis_open_rows()
    except Exception:
        pb_count = -1

    adapters = getattr(app_state, "adapters", {}) or {}
    per_ex: dict[str, Decimal] = {}
    if adapters:
        try:
            per_ex = await balance_service.get_per_exchange_equity(adapters)
        except Exception:
            logger.exception("status_balance_fetch_failed")
    total = sum(per_ex.values(), Decimal("0")) if per_ex else None

    mode_emoji = "🔴 LIVE" if mode == "live" else "🟡 PAPER"
    fr_emoji = "✅ 运行中" if fr_running else "⏸ 已暂停"
    sp_emoji = "✅ 运行中" if sp_running else "⏸ 已暂停"
    pb_emoji = "✅ 运行中" if pb_running else "⏸ 已暂停"

    lines = [
        "<b>📡 Dracula 状态</b>",
        f"  模式:    {mode_emoji}",
        f"  #01 策略:  {fr_emoji}",
        f"  #01 持仓:  {fr_count if fr_count >= 0 else '—'}",
        f"  #02 策略:  {pb_emoji}",
        f"  #02 持仓:  {pb_count if pb_count >= 0 else '—'}",
        f"  #04 策略:  {sp_emoji}",
        f"  #04 持仓:  {sp_count if sp_count >= 0 else '—'}",
        f"  总资产:    {_fmt_usd(total)}",
    ]
    return "\n".join(lines)


async def _help_handler(_app_state) -> str:
    return _HELP_TEXT


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_command_handlers(app_state) -> CommandHandlers:
    """绑定 app_state 生成 CommandHandlers — 闭包捕获，运行时再读最新状态。"""
    return CommandHandlers(
        balance=lambda: _balance_handler(app_state),
        positions=lambda: _positions_handler(app_state),
        pause=lambda: _pause_handler(app_state),
        resume=lambda: _resume_handler(app_state),
        status=lambda: _status_handler(app_state),
        help=lambda: _help_handler(app_state),
    )
