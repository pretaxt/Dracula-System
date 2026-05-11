"""纸交易会话工厂

从策略 YAML 配置字典 + 交易所适配器字典 构建一个完整的
:class:`~app.strategies.funding_rate.paper_trading.PaperTradingSession`，
调用方只需传入已加载的 cfg 和已初始化的 adapters，无需关心内部组件顺序。

用法（来自 lifespan）::

    with open("config/strategies/funding_rate_main.yaml") as f:
        cfg = yaml.safe_load(f)

    session = build_paper_session(cfg, adapters={"binance": adapter}, symbols=symbols)
    asyncio.create_task(session.run_forever())
"""
from __future__ import annotations

from decimal import Decimal

from app.exchanges.base import ExchangeAdapter
from app.exchanges.models import Symbol
from app.execution.live_broker import LiveBroker
from app.execution.order_executor import OrderExecutor
from app.execution.paper_broker import PaperBroker
from app.risk.limits import RiskGuard, RiskLimits
from app.risk.position_manager import PositionManager
from app.strategies.funding_rate.paper_trading import PaperTradingSession
from app.strategies.funding_rate.scanner import FundingRateScanner, ScannerConfig


def build_paper_session(
    cfg: dict,
    adapters: dict[str, ExchangeAdapter],
    symbols: list[Symbol],
    scan_interval_seconds: float = 60.0,
    live_mode: bool = False,
    market_data_hub: object | None = None,
) -> PaperTradingSession:
    """从策略配置字典构建 PaperTradingSession。

    Parameters
    ----------
    cfg:
        funding_rate_main.yaml 对应的已解析字典。
    adapters:
        exchange_name → ExchangeAdapter 映射，例如 ``{"binance": BinanceAdapter(...)}``。
    symbols:
        需要扫描的交易对列表。
    scan_interval_seconds:
        每次 tick 之间的间隔（秒），默认 60。

    Returns
    -------
    PaperTradingSession
        完全初始化、可直接调用 ``run_forever()`` 的纸交易会话。
    """
    position_cfg = cfg.get("position", {})
    execution_cfg = cfg.get("execution", {})
    risk_cfg = cfg.get("risk", {})
    entry_cfg = cfg.get("entry", {})
    exit_cfg = cfg.get("exit", {})

    # ------------------------------------------------------------------
    # 执行层参数
    # ------------------------------------------------------------------
    slippage_bps = Decimal(str(execution_cfg.get("slippage_bps", "2")))
    fee_rate = Decimal(str(execution_cfg.get("fee_rate", "0.0004")))
    size_usd = Decimal(str(position_cfg.get("size_usd", "500")))
    max_positions = int(position_cfg.get("max_positions", 3))

    # ------------------------------------------------------------------
    # 风控参数（position / exit / risk / entry 四节合并）
    # ------------------------------------------------------------------
    risk_limits = RiskLimits(
        max_positions=max_positions,
        max_total_notional_usd=Decimal(
            str(risk_cfg.get("max_total_notional_usd", "10000"))
        ),
        max_position_size_usd=Decimal(
            str(risk_cfg.get("max_position_size_usd", "2000"))
        ),
        min_position_size_usd=Decimal(
            str(risk_cfg.get("min_position_size_usd", "50"))
        ),
        stop_loss_pct=Decimal(str(risk_cfg.get("stop_loss_pct", "2.0"))),
        max_hold_hours=Decimal(
            str(exit_cfg.get("max_hold_hours", risk_cfg.get("max_hold_hours", "720")))
        ),
        min_apr_pct=Decimal(str(entry_cfg.get("min_apr_pct", "10.0"))),
    )

    # ------------------------------------------------------------------
    # 组件装配
    # ------------------------------------------------------------------
    leverage_cfg = cfg.get("leverage", {}) or {}
    perp_leverage = Decimal(str(leverage_cfg.get("default", "1")))

    if live_mode:
        # 多交易所路由：仅给已鉴权的 adapter 建 LiveBroker
        # OrderExecutor 按 opportunity.exchange 选 broker；公开行情 only 的 adapter 不会用于实盘下单
        broker = {
            ex_name: LiveBroker(
                adapter=ad,
                fee_rate=fee_rate,
                perp_leverage=perp_leverage,
            )
            for ex_name, ad in adapters.items()
            if getattr(ad, "_api_key", "")  # 跳过无 API key 的适配器
        }
        if not broker:
            raise RuntimeError(
                "live_mode=True but no authenticated adapter available "
                "(check BINANCE_API_KEY / OKX_API_KEY in .env)"
            )
    else:
        broker = PaperBroker(slippage_bps=slippage_bps, fee_rate=fee_rate)
    manager = PositionManager()
    guard = RiskGuard(limits=risk_limits)
    executor = OrderExecutor(
        broker=broker,
        manager=manager,
        guard=guard,
        strategy_instance=cfg.get("instance_name", "funding_rate_main"),
        perp_leverage=perp_leverage,
    )
    scanner = FundingRateScanner(
        adapters=adapters,
        symbols=symbols,
        config=ScannerConfig.from_yaml(cfg),
        market_data_hub=market_data_hub,  # hub 注入：跳过未知 symbol → 消除 timeout spam
    )

    pre_funding_window_min = float(entry_cfg.get("pre_funding_window_minutes", 15.0))
    min_apr_for_hold = Decimal(str(exit_cfg.get("min_apr_for_hold_pct", "0")))
    profit_target = Decimal(str(exit_cfg.get("profit_target_pct", "0")))
    trailing_drawdown = Decimal(str(exit_cfg.get("trailing_drawdown_pct", "1.0")))
    perp_margin_loss_threshold = Decimal(str(risk_cfg.get("perp_margin_loss_threshold_pct", "0")))

    return PaperTradingSession(
        scanner=scanner,
        executor=executor,
        manager=manager,
        size_per_trade_usd=size_usd,
        scan_interval_seconds=scan_interval_seconds,
        pre_funding_window_minutes=pre_funding_window_min,
        min_apr_for_hold_pct=min_apr_for_hold,
        profit_target_pct=profit_target,
        trailing_drawdown_pct=trailing_drawdown,
        perp_margin_loss_threshold_pct=perp_margin_loss_threshold,
    )
