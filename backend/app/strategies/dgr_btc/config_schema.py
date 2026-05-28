"""
dgr_btc/config_schema.py — Pydantic V2 schema for runtime config sanity (§6.2 #1)
==================================================================================

不替换 DgrBtcStrategyConfig dataclass (保持 ABI), 只在 yaml load + override 出口
跑一遍 model_validate, 阻挡明显的单位 / 范围错误.

历史教训:
  - max_price_deviation_pct 曾配 0.5 直接被读为 50% (实际想要 0.5%)
  - mart_factor 配 < 1 会让加仓越来越小 (martingale 反义)
  - layer_weights 不和为 1 会让 stake 分配漂移
  - leverage 写 0 / 负数会让 margin_health 计算除零

接口:
  validate_strategy_config(cfg) -> cfg     # 校验通过 returns cfg 原样
                                            # 失败 raise pydantic.ValidationError
                                            # → caller log + reject
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig


class DgrBtcConfigSchema(BaseModel):
    """Validate ranges & units of DgrBtcStrategyConfig fields.

    Fraction fields (0..1) get ge=0 le=1.
    Counts (int) get ge=1 where required.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        # 输入是 Decimal 时不强转, pydantic 直接接受
        # (DgrBtcStrategyConfig 所有 percent 字段都是 Decimal)
    )

    # ----- capital -----
    total_capital_usdt: Decimal = Field(gt=0)
    reserve_ratio: Decimal = Field(ge=0, le=1)

    # ----- position / leverage -----
    leverage: int = Field(ge=1, le=125)
    max_spot_btc: Decimal = Field(ge=0)
    max_short_btc: Decimal = Field(ge=0)

    # ----- martingale 内核 (核心 alpha 参数 sanity) -----
    mart_grid_step: Decimal = Field(gt=0, le=Decimal("0.5"))      # 0..50% 单格
    mart_factor: Decimal = Field(ge=1, le=10)                       # < 1 无意义
    mart_max_layers: int = Field(ge=1, le=20)
    mart_tp_pct: Decimal = Field(gt=0, le=1)                        # fraction
    mart_sl_pct: Decimal = Field(gt=0, le=1)                        # fraction
    mart_fee_pct: Decimal = Field(ge=0, le=Decimal("0.01"))         # 不超 1%
    mart_layer_weights: Sequence[Decimal]

    # ----- risk -----
    risk_max_daily_loss_pct: Decimal = Field(ge=0, le=1)
    risk_max_drawdown_pct: Decimal = Field(ge=0, le=1)
    risk_margin_ratio_min: Decimal = Field(ge=0, le=1)
    risk_liquidation_buffer: Decimal = Field(ge=0, le=1)

    # ----- pre-liq deleverage -----
    risk_pre_liq_enabled: bool
    risk_pre_liq_margin_ratio_threshold: Decimal = Field(ge=1, le=5)
    risk_pre_liq_deleverage_pct: Decimal = Field(gt=0, le=1)
    risk_pre_liq_cooldown_seconds: int = Field(ge=0)

    # ----- live safety (历史 bug 来源) -----
    live_safety_max_order_usd: Decimal = Field(ge=0)
    live_safety_max_daily_order_count: int = Field(ge=0)
    live_safety_max_daily_notional_usd: Decimal = Field(ge=0)
    # ⚠ 单位 fraction, 不是 %. 配 0.5 = 50% (旧 bug). 加 le=1 强制语义.
    live_safety_max_price_deviation_pct: Decimal = Field(ge=0, le=1)

    # ----- bounds (固定 fraction 区间) -----
    width_pct: Decimal = Field(gt=0, le=1)
    min_width_pct: Decimal = Field(gt=0, le=1)
    max_width_pct: Decimal = Field(gt=0, le=1)
    recenter_trigger_pct: Decimal = Field(gt=0, le=1)

    @field_validator("mart_layer_weights")
    @classmethod
    def _weights_positive_and_match_layers(
        cls, v: Sequence[Decimal],
    ) -> Sequence[Decimal]:
        if not v:
            raise ValueError("mart_layer_weights cannot be empty")
        for i, w in enumerate(v):
            if Decimal(str(w)) <= 0:
                raise ValueError(f"mart_layer_weights[{i}]={w} must be > 0")
        return v

    @model_validator(mode="after")
    def _weights_sum_close_to_one(self) -> "DgrBtcConfigSchema":
        total = sum((Decimal(str(w)) for w in self.mart_layer_weights), Decimal("0"))
        if not (Decimal("0.98") <= total <= Decimal("1.02")):
            raise ValueError(
                f"mart_layer_weights sum={total} must be ≈ 1.0 (got drift > 2%)",
            )
        return self

    @model_validator(mode="after")
    def _layer_weights_length_matches_max_layers(self) -> "DgrBtcConfigSchema":
        if len(self.mart_layer_weights) != self.mart_max_layers:
            raise ValueError(
                f"len(mart_layer_weights)={len(self.mart_layer_weights)} != "
                f"mart_max_layers={self.mart_max_layers}",
            )
        return self

    @model_validator(mode="after")
    def _min_max_width_consistency(self) -> "DgrBtcConfigSchema":
        if self.min_width_pct > self.max_width_pct:
            raise ValueError(
                f"min_width_pct={self.min_width_pct} > max_width_pct={self.max_width_pct}",
            )
        return self


def validate_strategy_config(cfg: "DgrBtcStrategyConfig") -> "DgrBtcStrategyConfig":
    """Run pydantic schema validation on a DgrBtcStrategyConfig instance.

    Returns the same cfg on success.
    Raises pydantic.ValidationError on failure — caller should log + reject
    config update (yaml load / PATCH override).
    """
    # 抽 schema 关心的字段 (避免 extra forbidden)
    payload = {
        name: getattr(cfg, name)
        for name in DgrBtcConfigSchema.model_fields.keys()
    }
    # convert tuple → list for pydantic Sequence accept
    if isinstance(payload.get("mart_layer_weights"), tuple):
        payload["mart_layer_weights"] = list(payload["mart_layer_weights"])
    DgrBtcConfigSchema.model_validate(payload)
    return cfg
