"""统一风控事件写入器 — R9 (Wave 2)。

所有异常 / 单腿告警 / 部分平仓 / 自动操作都通过此 helper 写入 risk_events 表，
让 UI / Telegram / Postmortem 都有 SSOT。

Schema 映射（见 schema.sql / public.risk_events）::

  severity   varchar(20)   "critical" | "high" | "medium" | "low"
  tier       int           1=immediate halt / 2=warn / 3=info（按 docs/05_risk_engine.md）
  event_type varchar(50)   类型：partial_close / single_leg_exposure / auto_rebalance_failed ...
  description text         人类可读说明（必填）
  action_taken text        系统采取的动作（auto_unwound / alert_only / paused_strategy ...）
  position_id bigint       关联仓位 id（无对应时 NULL）
  metric_*    数值          数量漂移百分比 / 资金不足金额等
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Severity → tier 默认映射
_TIER_BY_SEVERITY: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 2,
    "low": 3,
}


async def write_risk_event(
    event_type: str,
    severity: str,
    description: str,
    *,
    strategy_instance: str | None = None,
    position_id: int | None = None,
    action_taken: str | None = None,
    metric_name: str | None = None,
    metric_value: Decimal | float | str | None = None,
    threshold: Decimal | float | str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    """写入一条风控事件到 DB（best-effort，DB 失败不影响主流程）。

    Returns
    -------
    bool: True 写入成功，False 失败（已 log warning）
    """
    try:
        from sqlalchemy import text  # noqa: PLC0415

        from app.core.database import get_session  # noqa: PLC0415

        # 把 extra 序列化追加到 description 末尾，方便 UI 直接 grep
        full_desc = description
        if extra:
            try:
                full_desc = f"{description} | {json.dumps(extra, default=str)}"
            except Exception:
                pass

        tier = _TIER_BY_SEVERITY.get(severity.lower(), 3)
        async with get_session() as session:
            await session.execute(
                text(
                    "INSERT INTO risk_events "
                    "(event_type, severity, tier, description, action_taken, "
                    "strategy_instance, position_id, metric_name, metric_value, threshold, created_at) "
                    "VALUES (:t, :s, :tier, :d, :a, :si, :pid, :mn, :mv, :th, NOW())"
                ),
                {
                    "t": event_type,
                    "s": severity.lower(),
                    "tier": tier,
                    "d": full_desc,
                    "a": action_taken,
                    "si": strategy_instance,
                    "pid": position_id,
                    "mn": metric_name,
                    "mv": str(metric_value) if metric_value is not None else None,
                    "th": str(threshold) if threshold is not None else None,
                },
            )
        return True
    except Exception as e:
        logger.warning(
            "risk_event_write_failed",
            event_type=event_type,
            severity=severity,
            error=str(e),
        )
        return False
