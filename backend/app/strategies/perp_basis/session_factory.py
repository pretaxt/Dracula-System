"""#02 perp_basis paper trading 工厂

从 yaml 配置 + 已就绪 adapter 字典 + scanner 构造完整 PerpBasisPaperSession。
统一所有策略的 session 构造模式（与 funding_rate / spot_perp_basis 对齐）。

Paper vs Live:
- paper 模式: 用 _PerpPaperBrokerWrapper 包 PaperBroker，所有 adapter 都参与
  （不依赖真实 trading API key，纯模拟 — 与 #01 paper trading 一致语义）
- live 模式: 用 LiveBroker，仅 _api_key 非空的 adapter 参与（不持 trading key
  的 read-only adapter 不能下单）
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.exchanges.models import InstrumentType
from app.execution.live_broker import LiveBroker
from app.execution.paper_broker import PaperBroker
from app.strategies.perp_basis.paper_trading import PerpBasisPaperSession


class _PerpPaperBrokerWrapper:
    """在 #02 paper_trading 里冒充 LiveBroker — 内部用 PaperBroker 不打交易所。

    paper_trading.py 直接读 broker._adapter._clients / broker._perp_leverage /
    broker._ensure_perp_margin / broker.execute；本 wrapper 提供这些接口的
    paper-safe 实现。
    """

    def __init__(
        self, exchange_name: str,
        fee_rate: Decimal = Decimal("0.0004"),
        perp_leverage: Decimal = Decimal("5"),
        slippage_bps: Decimal = Decimal("2"),
    ) -> None:
        self._paper = PaperBroker(slippage_bps=slippage_bps, fee_rate=fee_rate)
        self._perp_leverage = perp_leverage
        # paper_trading 里的 preflight 调 broker._adapter._clients[PERPETUAL].fetch_balance()
        # 返回虚拟足额余额让 preflight 通过
        async def _fake_fetch_balance() -> dict:
            return {
                "total": {"USDT": "1000000"},
                "free":  {"USDT": "1000000"},
            }

        perp_client_stub = SimpleNamespace(fetch_balance=_fake_fetch_balance)
        clients_stub: dict[Any, Any] = {InstrumentType.PERPETUAL: perp_client_stub}
        self._adapter = SimpleNamespace(
            exchange_id=exchange_name,
            exchange_name=exchange_name,
            _clients=clients_stub,
            _api_key="paper",   # 非空让上游 sanity 检查通过
        )

    async def _ensure_perp_margin(self, req: Any) -> None:
        """paper 模式不需要真实 ensure leverage / margin。"""
        return None

    async def execute(self, req: Any) -> Any:
        return await self._paper.execute(req)


def build_perp_basis_paper_session(
    cfg: dict,
    adapters: dict[str, Any],
    scanner: Any,
    fee_rate: Decimal = Decimal("0.0004"),
    perp_leverage: Decimal = Decimal("5"),
    market_data_hub: Any = None,
    live_mode: bool = False,
) -> PerpBasisPaperSession | None:
    """从 yaml dict + adapters + scanner 构造 perp_basis paper session。

    paper 模式下用 _PerpPaperBrokerWrapper 给所有 adapter 包装 PaperBroker；
    live 模式仅给已鉴权的 adapter 建 LiveBroker。
    """
    entry = cfg.get("entry", {}) or {}
    pos_cfg = cfg.get("position", {}) or {}
    exit_cfg = cfg.get("exit", {}) or {}
    scan_cfg = cfg.get("scanning", {}) or {}

    if live_mode:
        brokers: dict[str, Any] = {
            n: LiveBroker(adapter=a, fee_rate=fee_rate, perp_leverage=perp_leverage)
            for n, a in adapters.items()
            if getattr(a, "_api_key", "")
        }
    else:
        # paper 模式：每个 adapter 都包一份 PaperBroker，无需 trading key
        brokers = {
            n: _PerpPaperBrokerWrapper(
                exchange_name=n, fee_rate=fee_rate, perp_leverage=perp_leverage,
            )
            for n in adapters.keys()
        }

    if not brokers:
        return None

    risk = cfg.get("risk", {}) or {}
    return PerpBasisPaperSession(
        scanner=scanner,
        brokers=brokers,
        notional_per_position=Decimal(str(pos_cfg.get("notional_per_position", 50))),
        max_concurrent=int(pos_cfg.get("max_concurrent", 2)),
        min_diff_apr_pct=Decimal(str(entry.get("min_diff_apr_pct", "50.0"))),
        max_hold_hours=Decimal(str(exit_cfg.get("max_hold_hours", 48))),
        min_hold_hours=Decimal(str(exit_cfg.get("min_hold_hours", 4))),
        exit_diff_apr_pct=Decimal(str(exit_cfg.get("exit_diff_apr_pct", 5))),
        stop_price_divergence_pct=Decimal(str(
            risk.get("stop_price_divergence_pct", "5.0")
        )),
        scan_interval_seconds=float(scan_cfg.get("scan_interval_seconds", 60)),
        market_data_hub=market_data_hub,
    )
