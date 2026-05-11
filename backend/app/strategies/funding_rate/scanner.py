"""FundingRateScanner — 资金费率机会扫描器

职责:
  1. 对配置的交易所和币种并发拉取当期资金费率
  2. 过滤掉不满足进场条件的机会
  3. 对通过过滤的机会执行历史稳定性检查
  4. 返回排序后的机会列表 (APR 从高到低)

不负责:
  - 实际下单 (Week 5 由 FundingRateStrategy 负责)
  - 持仓监控
  - 风控判断

进场过滤条件 (来自 docs/02_strategy_funding_rate.md §3 + config):
  ✓ APR > min_apr_pct
  ✓ 资金费率为正
  ✓ 订单簿深度 > min_orderbook_depth_usd (现货 + 永续分别检查)
  ✓ 买卖价差 < max_spread_bps
  ✓ 历史稳定性: 过去 lookback_periods 期中至少 min_positive_periods 期为正
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.exchanges.base import ExchangeAdapter
from app.exchanges.errors import ExchangeError
from app.exchanges.models import FundingRate, InstrumentType, OrderBook, Symbol
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 默认过滤参数 (可通过 ScannerConfig 覆盖)
# ---------------------------------------------------------------------------
_DEFAULT_MIN_APR_PCT = Decimal("10.0")
_DEFAULT_MIN_DEPTH_USD = Decimal("10000")
_DEFAULT_MAX_SPREAD_BPS = Decimal("10")
_DEFAULT_LOOKBACK_PERIODS = 9
_DEFAULT_MIN_POSITIVE_PERIODS = 7
_DEFAULT_MAX_OPPORTUNITIES = 20
_DEFAULT_MIN_VOLUME_24H_USD = Decimal("0")  # 0 = 不过滤；YAML 中可设置如 50000000


@dataclass
class ScannerConfig:
    """扫描器配置,从 funding_rate_main.yaml 加载"""

    min_apr_pct: Decimal = _DEFAULT_MIN_APR_PCT
    # 候选展示门槛：APR ≥ scan_threshold_apr_pct 的标的会进 opps_list 供 UI 显示，
    # 但只有 APR ≥ min_apr_pct 才会真正开仓（passes_entry=True）。
    # 0 = 回退用 min_apr_pct（旧行为，scan == entry）
    scan_threshold_apr_pct: Decimal = Decimal("0")
    min_orderbook_depth_usd: Decimal = _DEFAULT_MIN_DEPTH_USD
    max_spread_bps: Decimal = _DEFAULT_MAX_SPREAD_BPS
    lookback_periods: int = _DEFAULT_LOOKBACK_PERIODS
    min_positive_periods: int = _DEFAULT_MIN_POSITIVE_PERIODS
    max_opportunities: int = _DEFAULT_MAX_OPPORTUNITIES  # 按 APR 降序后保留前 N 名
    min_volume_24h_usd: Decimal = _DEFAULT_MIN_VOLUME_24H_USD  # 24h quote-volume 下限

    @property
    def effective_scan_threshold(self) -> Decimal:
        """实际使用的展示门槛：>0 时用 scan_threshold_apr_pct，否则回退 min_apr_pct。"""
        return self.scan_threshold_apr_pct if self.scan_threshold_apr_pct > 0 else self.min_apr_pct

    @classmethod
    def from_yaml(cls, cfg: dict) -> "ScannerConfig":
        """从 YAML 配置字典构建 ScannerConfig

        cfg 对应 funding_rate_main.yaml 结构:
          entry:
            min_apr_pct: 10.0
            scan_threshold_apr_pct: 5.0   # 候选展示门槛（可选）
            min_orderbook_depth_usd: 10000
          scanning:
            max_spread_bps: 10
            funding_history_check:
              lookback_periods: 9
              min_positive_periods: 7
        """
        entry = cfg.get("entry", {})
        scanning = cfg.get("scanning", {})
        history = scanning.get("funding_history_check", {})
        return cls(
            min_apr_pct=Decimal(str(entry.get("min_apr_pct", _DEFAULT_MIN_APR_PCT))),
            scan_threshold_apr_pct=Decimal(
                str(entry.get("scan_threshold_apr_pct", "0") or "0"),
            ),
            min_orderbook_depth_usd=Decimal(
                str(entry.get("min_orderbook_depth_usd", _DEFAULT_MIN_DEPTH_USD))
            ),
            max_spread_bps=Decimal(
                str(scanning.get("max_spread_bps", _DEFAULT_MAX_SPREAD_BPS))
            ),
            lookback_periods=int(
                history.get("lookback_periods", _DEFAULT_LOOKBACK_PERIODS)
            ),
            min_positive_periods=int(
                history.get("min_positive_periods", _DEFAULT_MIN_POSITIVE_PERIODS)
            ),
            max_opportunities=int(
                entry.get("max_opportunities", _DEFAULT_MAX_OPPORTUNITIES)
            ),
            min_volume_24h_usd=Decimal(
                str(entry.get("min_volume_24h_usd", _DEFAULT_MIN_VOLUME_24H_USD))
            ),
        )


@dataclass
class FundingRateOpportunity:
    """通过所有过滤条件的资金费率套利机会

    表示可以在 `exchange` 上针对 `symbol` 建立
    Delta-中性仓位(现货多 + 永续空)的机会。

    "是否到入场阈值"由 ``apr_pct >= cfg.min_apr_pct`` 在调用方
    （paper_trading._open_positions / API endpoint）现算，
    避免 PATCH min_apr_pct 后已缓存对象内存值过期。
    """

    symbol: Symbol
    exchange: str
    funding_rate: FundingRate
    spot_orderbook: OrderBook
    perp_orderbook: OrderBook
    history_positive_count: int = 0
    history_total_count: int = 0
    # P2-16 一阶差分过滤：近 3 期 APR 最大值，paper_trading 判定 current ≥ 0.7 × 该值
    recent_max_apr_pct: Decimal = Decimal("0")

    @property
    def apr_pct(self) -> Decimal:
        """年化收益率百分比,如 10.95"""
        return self.funding_rate.apr * Decimal("100")

    @property
    def spot_depth_usd(self) -> Decimal:
        _, ask_d = self.spot_orderbook.depth_usd(5)
        return ask_d

    @property
    def perp_depth_usd(self) -> Decimal:
        _, ask_d = self.perp_orderbook.depth_usd(5)
        return ask_d

    def __repr__(self) -> str:
        return (
            f"Opportunity({self.exchange} {self.symbol} "
            f"APR={self.apr_pct:.2f}% "
            f"spot_depth=${self.spot_depth_usd:,.0f} "
            f"perp_depth=${self.perp_depth_usd:,.0f})"
        )


class FundingRateScanner:
    """资金费率机会扫描器

    用法:
        scanner = FundingRateScanner(
            adapters={"binance": binance_adapter},
            symbols=[Symbol("BTC", "USDT"), Symbol("ETH", "USDT")],
            config=ScannerConfig(min_apr_pct=Decimal("10")),
        )
        opportunities = await scanner.scan()
        # 已按 APR 降序排列,均通过全部过滤条件
    """

    def __init__(
        self,
        adapters: Dict[str, ExchangeAdapter],
        symbols: List[Symbol],
        config: Optional[ScannerConfig] = None,
        market_data_hub: Any = None,
    ) -> None:
        self._adapters = adapters
        self._symbols = symbols
        self._config = config or ScannerConfig()
        # P0-α 复用 hub funding cache：避免 3565 次 per-symbol fetch_funding_rate
        # （hub 已 60s bulk fetch 一次全量，scanner 直接 hit cache 节省 ~6 分钟扫描时间）
        self._hub = market_data_hub
        # P0-α per-exchange 并发限制：防止 3565 任务同时打 limiter 雪崩
        self._exchange_sems: Dict[str, asyncio.Semaphore] = {
            ex: asyncio.Semaphore(50) for ex in adapters.keys()
        }

    async def current_rate(
        self, exchange: str, symbol: Symbol
    ) -> Optional[FundingRate]:
        """获取指定交易所/币种的当前资金费率（不做任何过滤）。

        供 funding flip 退出检查使用：scanner.scan() 只返回通过 min_apr_pct
        过滤的机会，无法用来判断已开仓位的费率是否翻负。
        """
        adapter = self._adapters.get(exchange)
        if adapter is None:
            return None
        try:
            return await adapter.fetch_funding_rate(symbol)
        except Exception:
            logger.warning("current_rate_fetch_failed", exchange=exchange, symbol=str(symbol))
            return None

    async def current_perp_price(
        self, exchange: str, symbol: Symbol
    ) -> Optional[Decimal]:
        """获取指定交易所/币种的永续合约最新价。

        供 perp 单腿保证金亏损监测使用：当价格接近 short 腿清算线时，
        策略需要主动平仓以避免被交易所强平后丢失对冲。
        """
        adapter = self._adapters.get(exchange)
        if adapter is None:
            return None
        try:
            ticker = await adapter.fetch_ticker(symbol, InstrumentType.PERPETUAL)
            return ticker.last
        except Exception:
            logger.warning("current_perp_price_fetch_failed", exchange=exchange, symbol=str(symbol))
            return None

    async def scan(self) -> List[FundingRateOpportunity]:
        """并发扫描所有交易所 × 所有币种,返回通过过滤的机会列表

        P0-α: 每任务 30s timeout（防单慢请求拖死整个 gather）
        + per-exchange Semaphore(50) 限并发（防 3565 任务挤爆单家 limiter）
        + 复用 MarketDataHub funding cache（节省 60-70% HTTP）
        """
        async def _scan_with_timeout(ex: str, ad: ExchangeAdapter, sym: Symbol) -> Optional[FundingRateOpportunity]:
            try:
                return await asyncio.wait_for(
                    self._scan_one(ex, ad, sym), timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.debug("scan_one_timeout", exchange=ex, symbol=str(sym))
                return None
            except Exception as e:
                logger.debug("scan_one_failed", exchange=ex, symbol=str(sym), error=str(e)[:100])
                return None

        tasks = [
            _scan_with_timeout(exchange_name, adapter, symbol)
            for exchange_name, adapter in self._adapters.items()
            for symbol in self._symbols
            if InstrumentType.PERPETUAL in adapter.supported_instruments
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        opportunities: List[FundingRateOpportunity] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("scan_task_error", error=str(res))
                continue
            if res is not None:
                opportunities.append(res)

        opportunities.sort(key=lambda o: o.apr_pct, reverse=True)

        # 取按 APR 降序排列的前 N 名
        total_passed = len(opportunities)
        if self._config.max_opportunities and total_passed > self._config.max_opportunities:
            opportunities = opportunities[: self._config.max_opportunities]

        logger.info(
            "scan_complete",
            total_checked=len(tasks),
            opportunities_passed_filters=total_passed,
            opportunities_returned=len(opportunities),
            top_n_cap=self._config.max_opportunities,
        )
        return opportunities

    async def _scan_one(
        self,
        exchange_name: str,
        adapter: ExchangeAdapter,
        symbol: Symbol,
    ) -> Optional[FundingRateOpportunity]:
        """扫描单个 (交易所, 币种) 组合,返回机会或 None

        P0-α 优化路径：先查 MarketDataHub funding cache（hit 则免 HTTP），
        miss 才走 adapter.fetch_funding_rate 兜底。
        """
        log = logger.bind(exchange=exchange_name, symbol=str(symbol))
        sem = self._exchange_sems.get(exchange_name)

        # 早退 1：hub 资金费率缓存过滤（HTX 等 bulk API 支持，已知 symbol 集合准确）
        if self._hub is not None and hasattr(self._hub, "known_funding_symbols"):
            _known = self._hub.known_funding_symbols(exchange_name)
            if _known and str(symbol) not in _known:
                return None

        # 早退 2：hub ticker 缓存过滤（对 bulk 资金费率 API 不支持的交易所如 Bybit 尤为关键）
        # 若 ticker hub 已填充但 symbol 无 perp ticker，说明该 symbol 在此交易所不存在
        if self._hub is not None and hasattr(self._hub, "known_perp_symbols"):
            _perp_known = self._hub.known_perp_symbols(exchange_name)
            if _perp_known and str(symbol) not in _perp_known:
                return None

        try:
            # Step 1: 拉取资金费率（hub-first，避免 3565 次重复 HTTP）
            funding: Optional[FundingRate] = None
            if self._hub is not None:
                try:
                    fr_entry = self._hub.get_funding_rate(exchange_name, symbol)
                    if fr_entry is not None:
                        funding = fr_entry.rate
                except Exception:
                    pass
            if funding is None:
                # cache miss → fallback HTTP，受 per-exchange Semaphore 限并发
                if sem is not None:
                    async with sem:
                        funding = await adapter.fetch_funding_rate(symbol)
                else:
                    funding = await adapter.fetch_funding_rate(symbol)

            if not funding.is_positive:
                log.debug("funding_rate_not_positive", rate=float(funding.rate))
                return None

            apr_pct = funding.apr * Decimal("100")
            # 用展示门槛早退（更宽松），实盘开仓门槛 min_apr_pct 在 opportunity 上标记
            scan_threshold = self._config.effective_scan_threshold
            if apr_pct < scan_threshold:
                log.debug(
                    "apr_below_threshold",
                    apr_pct=float(apr_pct),
                    threshold=float(scan_threshold),
                )
                return None

            # Step 1.5: 24h 交易量过滤（防止流动性陷阱）
            if self._config.min_volume_24h_usd > 0:
                try:
                    volume_24h: Optional[Decimal] = None
                    hub_hit = False  # hub 是否命中（命中则不走 HTTP）
                    # hub-first：优先从 ticker cache 读取，避免 HTTP
                    # （OKX 等 perp ticker quoteVolume=None，改用 baseVolume×last 估算；
                    #   无论如何只要 hub 命中就不回落 HTTP 避免 fetch_ticker 挂起）
                    if self._hub is not None:
                        try:
                            te = self._hub.get_ticker(exchange_name, InstrumentType.PERPETUAL, symbol)
                            if te is not None:
                                hub_hit = True
                                raw_vol = te.raw.get("quoteVolume")
                                if raw_vol is not None:
                                    volume_24h = Decimal(str(raw_vol))
                                else:
                                    # OKX swap: quoteVolume=None, baseVolume in base asset
                                    base_vol = te.raw.get("baseVolume")
                                    last = te.raw.get("last") or te.raw.get("close")
                                    if base_vol is not None and last is not None:
                                        volume_24h = Decimal(str(base_vol)) * Decimal(str(last))
                        except Exception:
                            pass
                    if not hub_hit:
                        ticker = await adapter.fetch_ticker(symbol, InstrumentType.PERPETUAL)
                        volume_24h = ticker.volume_24h
                    if volume_24h is None:
                        # hub 命中但缺量数据：跳过此过滤（宁可放行，不挂 HTTP）
                        pass
                    elif volume_24h < self._config.min_volume_24h_usd:
                        log.debug(
                            "volume_too_low",
                            volume_24h_usd=float(volume_24h),
                            required=float(self._config.min_volume_24h_usd),
                        )
                        return None
                except Exception:
                    log.debug("volume_fetch_failed")
                    return None

            # Step 2: 并发拉取现货 + 永续订单簿（受 per-exchange Semaphore 限并发）
            if sem is not None:
                async with sem:
                    spot_ob, perp_ob = await asyncio.gather(
                        adapter.fetch_orderbook(symbol, InstrumentType.SPOT, depth=10),
                        adapter.fetch_orderbook(symbol, InstrumentType.PERPETUAL, depth=10),
                    )
            else:
                spot_ob, perp_ob = await asyncio.gather(
                    adapter.fetch_orderbook(symbol, InstrumentType.SPOT, depth=10),
                    adapter.fetch_orderbook(symbol, InstrumentType.PERPETUAL, depth=10),
                )

            # 检查价差
            spot_spread = spot_ob.spread_bps()
            if spot_spread > self._config.max_spread_bps:
                log.debug(
                    "spot_spread_too_wide",
                    spread_bps=float(spot_spread),
                    max=float(self._config.max_spread_bps),
                )
                return None

            perp_spread = perp_ob.spread_bps()
            if perp_spread > self._config.max_spread_bps:
                log.debug(
                    "perp_spread_too_wide",
                    spread_bps=float(perp_spread),
                    max=float(self._config.max_spread_bps),
                )
                return None

            # 检查订单簿深度 (现货卖方深度 + 永续卖方深度,取最小)
            _, spot_ask_depth = spot_ob.depth_usd(5)
            _, perp_ask_depth = perp_ob.depth_usd(5)
            min_depth = min(spot_ask_depth, perp_ask_depth)
            if min_depth < self._config.min_orderbook_depth_usd:
                log.debug(
                    "insufficient_depth",
                    depth_usd=float(min_depth),
                    required=float(self._config.min_orderbook_depth_usd),
                )
                return None

            # Step 3: 历史资金费率稳定性检查
            positive_count, total_count, recent_max_apr = await self._check_history(
                adapter, symbol, exchange_name
            )
            if positive_count < self._config.min_positive_periods:
                log.debug(
                    "history_unstable",
                    positive=positive_count,
                    required=self._config.min_positive_periods,
                    total=total_count,
                )
                return None

            opp = FundingRateOpportunity(
                symbol=symbol,
                exchange=exchange_name,
                funding_rate=funding,
                spot_orderbook=spot_ob,
                perp_orderbook=perp_ob,
                history_positive_count=positive_count,
                history_total_count=total_count,
                recent_max_apr_pct=recent_max_apr,
            )
            log.info(
                "opportunity_found",
                apr_pct=float(apr_pct),
                min_depth_usd=float(min_depth),
                history_positive=positive_count,
            )
            return opp

        except ExchangeError as e:
            log.warning("exchange_error_during_scan", error=str(e))
            return None

    async def _check_history(
        self,
        adapter: ExchangeAdapter,
        symbol: Symbol,
        exchange_name: str,
    ) -> Tuple[int, int, Decimal]:
        """检查历史资金费率稳定性

        返回 (positive_count, total_count, recent_max_apr_pct)。
        recent_max_apr_pct 是近 3 期最大 APR — paper_trading 用作一阶差分过滤
        （拒绝当前 APR << 近期峰值的衰减信号）。
        若适配器不支持历史查询，返回 (lookback, lookback, 0) 不阻止开仓。
        """
        lookback = self._config.lookback_periods
        fetch_history = getattr(adapter, "fetch_funding_rate_history", None)

        if fetch_history is None:
            return lookback, lookback, Decimal("0")

        try:
            history: List[FundingRate] = await fetch_history(
                symbol, limit=lookback
            )
            if not history:
                return lookback, lookback, Decimal("0")
            positive = sum(1 for fr in history if fr.is_positive)
            # P2-16 近 3 期 APR 最大值（含当前）— APR = rate × (24/interval) × 365 × 100
            recent = history[:3]
            recent_max_apr = max(
                (fr.rate * Decimal(str(24 / max(fr.funding_interval_hours or 8, 1)))
                 * Decimal("365") * Decimal("100"))
                for fr in recent
            ) if recent else Decimal("0")
            return positive, len(history), recent_max_apr
        except ExchangeError as e:
            logger.warning(
                "funding_history_fetch_error",
                exchange=exchange_name,
                symbol=str(symbol),
                error=str(e),
            )
            return lookback, lookback, Decimal("0")
