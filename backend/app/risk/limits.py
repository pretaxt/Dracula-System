"""风险规则引擎

:class:`RiskLimits` — 风控参数配置（从 YAML 加载或使用默认值）。
:class:`RiskViolation` — 单条违规记录。
:class:`RiskGuard` — 在开仓/更新前校验规则，违规则抛出 :class:`RiskLimitError`。

用法::

    guard = RiskGuard(limits=RiskLimits.from_yaml(cfg))

    # 开仓前检查
    violations = guard.check_open(positions=manager.open_positions, new_notional=Decimal("500"))
    if violations:
        raise RiskLimitError(violations)

    # 持仓中持续检查
    violations = guard.check_position(position)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.risk.models import Position, PositionStatus


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class RiskLimitError(Exception):
    """违反风控规则时抛出。"""

    def __init__(self, violations: list[RiskViolation]) -> None:
        self.violations = violations
        names = ", ".join(v.rule for v in violations)
        super().__init__(f"风控规则触发: {names}")


# ---------------------------------------------------------------------------
# 违规记录
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskViolation:
    """单条风控违规记录。"""

    rule: str                    # 规则名，如 "max_positions"
    message: str                 # 可读描述
    current_value: Decimal       # 当前指标值
    limit_value: Decimal         # 规则阈值


# ---------------------------------------------------------------------------
# 风控参数
# ---------------------------------------------------------------------------


@dataclass
class RiskLimits:
    """全套风控参数，支持从 YAML 配置文件加载。"""

    # 组合级
    max_positions: int = 5
    max_total_notional_usd: Decimal = Decimal("10000")
    max_daily_drawdown_pct: Decimal = Decimal("3.0")   # 3%

    # 单仓级
    max_position_size_usd: Decimal = Decimal("2000")
    min_position_size_usd: Decimal = Decimal("50")
    stop_loss_pct: Decimal = Decimal("2.0")             # 相对名义价值的 2%
    max_hold_hours: Decimal = Decimal("168")            # 最长 7 天

    # 费率级
    min_apr_pct: Decimal = Decimal("10.0")

    @classmethod
    def from_yaml(cls, cfg: dict) -> "RiskLimits":
        """从策略 YAML 配置字典构建。

        期望结构（所有字段均可选）::

            risk:
              max_positions: 5
              max_total_notional_usd: 10000
              max_daily_drawdown_pct: 3.0
              max_position_size_usd: 2000
              min_position_size_usd: 50
              stop_loss_pct: 2.0
              max_hold_hours: 168
            entry:
              min_apr_pct: 10.0
        """
        risk = cfg.get("risk", {})
        entry = cfg.get("entry", {})

        def _d(key: str, default: Decimal, src: dict = risk) -> Decimal:
            val = src.get(key)
            return Decimal(str(val)) if val is not None else default

        return cls(
            max_positions=int(risk.get("max_positions", 5)),
            max_total_notional_usd=_d("max_total_notional_usd", Decimal("10000")),
            max_daily_drawdown_pct=_d("max_daily_drawdown_pct", Decimal("3.0")),
            max_position_size_usd=_d("max_position_size_usd", Decimal("2000")),
            min_position_size_usd=_d("min_position_size_usd", Decimal("50")),
            stop_loss_pct=_d("stop_loss_pct", Decimal("2.0")),
            max_hold_hours=_d("max_hold_hours", Decimal("168")),
            min_apr_pct=_d("min_apr_pct", Decimal("10.0"), src=entry),
        )


# ---------------------------------------------------------------------------
# 风控守卫
# ---------------------------------------------------------------------------


@dataclass
class RiskGuard:
    """在关键操作前/中校验风控规则。"""

    limits: RiskLimits = field(default_factory=RiskLimits)

    # ------------------------------------------------------------------
    # 开仓前检查（组合级）
    # ------------------------------------------------------------------

    def check_open(
        self,
        positions: list[Position],
        new_notional: Decimal,
        apr_pct: Decimal | None = None,
    ) -> list[RiskViolation]:
        """检查是否可以开一个新仓位。返回违规列表（空列表 = 可开仓）。"""
        open_positions = [p for p in positions if p.is_open]
        violations: list[RiskViolation] = []

        # 1. 最大仓位数
        current_count = Decimal(str(len(open_positions)))
        limit_count = Decimal(str(self.limits.max_positions))
        if current_count >= limit_count:
            violations.append(RiskViolation(
                rule="max_positions",
                message=f"已有 {len(open_positions)} 个开仓，上限 {self.limits.max_positions}",
                current_value=current_count,
                limit_value=limit_count,
            ))

        # 2. 单仓大小上限
        if new_notional > self.limits.max_position_size_usd:
            violations.append(RiskViolation(
                rule="max_position_size_usd",
                message=f"新仓位 {new_notional} USD 超过单仓上限 {self.limits.max_position_size_usd} USD",
                current_value=new_notional,
                limit_value=self.limits.max_position_size_usd,
            ))

        # 3. 单仓大小下限
        if new_notional < self.limits.min_position_size_usd:
            violations.append(RiskViolation(
                rule="min_position_size_usd",
                message=f"新仓位 {new_notional} USD 低于最小仓位 {self.limits.min_position_size_usd} USD",
                current_value=new_notional,
                limit_value=self.limits.min_position_size_usd,
            ))

        # 4. 组合总名义价值上限
        current_notional = sum(
            (p.notional_usd for p in open_positions), Decimal("0")
        )
        projected_notional = current_notional + new_notional
        if projected_notional > self.limits.max_total_notional_usd:
            violations.append(RiskViolation(
                rule="max_total_notional_usd",
                message=(
                    f"开仓后总名义价值 {projected_notional:.0f} USD "
                    f"超过上限 {self.limits.max_total_notional_usd:.0f} USD"
                ),
                current_value=projected_notional,
                limit_value=self.limits.max_total_notional_usd,
            ))

        # 5. APR 门槛
        if apr_pct is not None and apr_pct < self.limits.min_apr_pct:
            violations.append(RiskViolation(
                rule="min_apr_pct",
                message=f"APR {apr_pct:.2f}% 低于最低要求 {self.limits.min_apr_pct:.2f}%",
                current_value=apr_pct,
                limit_value=self.limits.min_apr_pct,
            ))

        return violations

    # ------------------------------------------------------------------
    # 持仓中检查（单仓级）
    # ------------------------------------------------------------------

    def check_position(
        self,
        position: Position,
        as_of: datetime | None = None,
    ) -> list[RiskViolation]:
        """检查单个持仓是否触发止损或超时。

        Parameters
        ----------
        position:
            要检查的仓位。
        as_of:
            计算持仓时长的参考时间（默认 datetime.now(UTC)）。
            回测时传入模拟周期时间戳，避免使用系统时钟。
        """
        if position.status != PositionStatus.OPEN:
            return []

        violations: list[RiskViolation] = []

        # 1. 止损：总盈亏为负且超过名义价值的 stop_loss_pct
        if position.notional_usd > 0:
            loss_pct = (-position.total_pnl / position.notional_usd) * Decimal("100")
            if loss_pct > self.limits.stop_loss_pct:
                violations.append(RiskViolation(
                    rule="stop_loss_pct",
                    message=(
                        f"仓位 {position.id[:8]} 亏损 {loss_pct:.2f}% "
                        f"超过止损线 {self.limits.stop_loss_pct:.2f}%"
                    ),
                    current_value=loss_pct,
                    limit_value=self.limits.stop_loss_pct,
                ))

        # 2. 最长持仓时间（支持传入模拟时间，用于回测）
        if position.opened_at is not None:
            ref_time = position.closed_at or as_of or datetime.now(UTC)
            holding_hours = Decimal(
                str((ref_time - position.opened_at).total_seconds() / 3600)
            )
            if holding_hours > self.limits.max_hold_hours:
                violations.append(RiskViolation(
                    rule="max_hold_hours",
                    message=(
                        f"仓位 {position.id[:8]} 已持有 {float(holding_hours):.1f}h "
                        f"超过上限 {self.limits.max_hold_hours:.0f}h"
                    ),
                    current_value=holding_hours,
                    limit_value=self.limits.max_hold_hours,
                ))

        return violations

    def assert_can_open(
        self,
        positions: list[Position],
        new_notional: Decimal,
        apr_pct: Decimal | None = None,
    ) -> None:
        """同 :meth:`check_open`，但违规时直接抛出 :class:`RiskLimitError`。"""
        violations = self.check_open(positions, new_notional, apr_pct)
        if violations:
            raise RiskLimitError(violations)
