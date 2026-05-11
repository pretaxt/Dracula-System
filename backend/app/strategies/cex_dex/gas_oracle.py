"""Gas 价格检查 — 超过上限时阻断执行。"""
from __future__ import annotations

from decimal import Decimal

from app.core.logging import get_logger

logger = get_logger(__name__)


async def check_gas_ok(w3: object, max_gwei: Decimal) -> tuple[bool, Decimal]:
    """返回 (is_ok, current_gwei)。gas 超限返回 False。"""
    try:
        gas_price_wei = await w3.eth.gas_price  # type: ignore[attr-defined]
        current_gwei = Decimal(str(gas_price_wei)) / Decimal("1e9")
        ok = current_gwei <= max_gwei
        if not ok:
            logger.warning("gas_too_high", current_gwei=float(current_gwei), max_gwei=float(max_gwei))
        return ok, current_gwei
    except Exception:
        logger.warning("gas_check_failed", exc_info=True)
        return True, Decimal("0")  # 查询失败时不阻断，由后续执行层兜底
