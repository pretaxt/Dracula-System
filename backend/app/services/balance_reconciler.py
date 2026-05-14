"""BalanceReconcilerService — 后台余额 + 持仓对账（v0.4.7+）

设计目的（feedback_no_single_leg + 系统性设计）:
- 30s 后台 task 持续 fetch 各交易所 spot 余额 + perp 持仓
- 与 DB OPEN positions 对账，发现单腿暴露 / 残留持仓立即告警
- 提供实时 endpoint 直接读 cache（替代 lazy fetch）

三层不一致检测：
  1. **单腿暴露**：DB OPEN 但其 perp leg 在真实 fetch_positions 缺失（perp 已被强平/手动平）
  2. **残留持仓**：真实交易所有仓位但 DB 没有任一 OPEN 含此 symbol（孤儿）
  3. **数量漂移**：DB leg.size 与真实持仓数量差 >5%（部分平仓 / 滑点）

任何不一致写入 ``risk_events`` 表 + Telegram 告警 + 触发 auto-unwind。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_INTERVAL_SECONDS = 30.0
_QTY_DRIFT_TOLERANCE_PCT = Decimal("5.0")  # 5% 数量差异容忍（fee + slippage 缓冲）

# R2.b auto-unwind cap — 单笔残留持仓 ≤ 此名义价值才自动清，否则等人工
# P1-7: 静态 $200 cap 与策略 notional 脱节（单笔升到 $500 后所有 unwind 都 skip）。
# 实际 cap 改为动态：max(2 × max_strategy_notional, 200) — 在 _try_auto_* 里读 yaml/state
_AUTO_UNWIND_MAX_NOTIONAL_USD = Decimal("200")  # 兜底下限
_auto_unwind_attempted: set[tuple[str, str]] = set()  # (exchange, symbol) 防重复尝试


_unwind_cap_cache: dict[str, Any] = {"value": None, "ts": 0.0}
_UNWIND_CAP_TTL_S = 60.0  # W4 60s TTL：避免每次 unwind 三次同步 yaml IO


def _get_paper_only_instances_global() -> set[str]:
    """读取 yaml 判断哪些 strategy_instance 当前永远跑 paper 模式（live_mode=False）。

    paper 仓位仅在 DB 存在，交易所无真实持仓，reconciler 不应对账 leg。
    """
    out: set[str] = set()
    try:
        import yaml as _yaml  # noqa: PLC0415
        for path, instance in (
            ("config/strategies/perp_basis_main.yaml", "perp_basis_main"),
            ("config/strategies/funding_rate_main.yaml", "funding_rate_main"),
            ("config/strategies/spot_perp_main.yaml", "spot_perp_main"),
        ):
            try:
                with open(path) as f:
                    cfg = _yaml.safe_load(f) or {}
                pt = cfg.get("paper_trading", {}) or {}
                if pt.get("enabled") and not pt.get("live_mode", False):
                    out.add(instance)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _resolve_auto_unwind_cap() -> Decimal:
    """读取三策略当前 notional_per_position 的最大值 × 2，作为 unwind cap。

    W4: 加 60s TTL 缓存（之前每次 unwind 三次同步 open(yaml)，reconciler tick 主循环里
    无谓 IO 浪费）。PATCH 持久化的 override 在 60s 内生效，足够。
    fail-safe: yaml 读取失败时回退 _AUTO_UNWIND_MAX_NOTIONAL_USD ($200)。
    """
    import time as _time  # noqa: PLC0415
    now = _time.monotonic()
    cached_val = _unwind_cap_cache.get("value")
    cached_ts = _unwind_cap_cache.get("ts", 0.0) or 0.0
    if cached_val is not None and (now - cached_ts) < _UNWIND_CAP_TTL_S:
        return cached_val  # type: ignore[return-value]

    try:
        import yaml as _yaml  # noqa: PLC0415
        from app.services.runtime_overrides import load_overrides  # noqa: PLC0415

        notionals: list[Decimal] = []
        # #01 走 risk_limits.max_position_size_usd 兜底（global pool）
        try:
            with open("config/strategies/funding_rate_main.yaml") as f:
                fr_cfg = _yaml.safe_load(f) or {}
            v = (fr_cfg.get("position", {}) or {}).get("size_usd") \
                or (fr_cfg.get("risk", {}) or {}).get("max_position_size_usd")
            if v is not None:
                notionals.append(Decimal(str(v)))
        except Exception:
            pass

        # #02
        try:
            with open("config/strategies/perp_basis_main.yaml") as f:
                pb_cfg = _yaml.safe_load(f) or {}
            v = (pb_cfg.get("position", {}) or {}).get("notional_per_position")
            if v is not None:
                notionals.append(Decimal(str(v)))
        except Exception:
            pass

        # #04
        try:
            with open("config/strategies/spot_perp_main.yaml") as f:
                sp_cfg = _yaml.safe_load(f) or {}
            v = (sp_cfg.get("position", {}) or {}).get("size_usd") \
                or (sp_cfg.get("position", {}) or {}).get("notional_per_position")
            if v is not None:
                notionals.append(Decimal(str(v)))
        except Exception:
            pass

        # PATCH override（perp_basis）
        try:
            ov = load_overrides() or {}
            pb_ov = ov.get("perp_basis") or {}
            if isinstance(pb_ov, dict) and "notional_per_position" in pb_ov:
                notionals.append(Decimal(str(pb_ov["notional_per_position"])))
        except Exception:
            pass

        if notionals:
            cap = max(_AUTO_UNWIND_MAX_NOTIONAL_USD, max(notionals) * Decimal("2"))
            _unwind_cap_cache["value"] = cap
            _unwind_cap_cache["ts"] = now
            return cap
    except Exception:
        pass
    _unwind_cap_cache["value"] = _AUTO_UNWIND_MAX_NOTIONAL_USD
    _unwind_cap_cache["ts"] = now
    return _AUTO_UNWIND_MAX_NOTIONAL_USD

# T6/R12 残留挂单清理 — 超过此年龄（秒）仍 NEW/PARTIAL 的 limit 单视为残留，自动撤
_STALE_ORDER_MAX_AGE_S = 30 * 60  # 30 分钟（market 单不可能这么久仍 open）


@dataclass
class ReconcileAlert:
    """单笔对账告警事件，可序列化到 risk_events 表 + Telegram。"""

    type: str  # "single_leg_exposure" / "orphan_position" / "qty_drift"
    severity: str  # "critical" / "high" / "medium"
    exchange: str
    symbol: str
    detail: dict[str, Any] = field(default_factory=dict)
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BalanceReconcilerService:
    """后台对账服务 — 周期性 fetch + reconcile。

    Parameters
    ----------
    adapters:
        ``{exchange_name: ExchangeAdapter}`` — 仅含已鉴权可调 fetch_balance/positions 的
    refresh_interval_s:
        每轮 fetch+reconcile 间隔，默认 30s
    """

    def __init__(
        self,
        adapters: dict[str, Any],
        refresh_interval_s: float = _DEFAULT_INTERVAL_SECONDS,
        market_data_hub: Any = None,
        all_adapters_ref: dict[str, Any] | None = None,
    ) -> None:
        # 启动时的 authed snapshot（fallback）
        self._adapters = adapters
        # 持有 app.state.adapters 引用 — 每次 tick 重算 authed 子集
        # 这样 hot_reload 后新增的 CEX 会自动被纳入对账
        self._all_adapters_ref = all_adapters_ref
        self._interval = refresh_interval_s
        self._hub = market_data_hub  # R7: 拉 ticker 用，可选
        self._running = False
        # cache (供 endpoint 直读)
        self.balance_cache: dict[str, dict] = {}
        self.position_cache: dict[str, list] = {}
        self.live_pnl_cache: dict[str, dict] = {}  # R7: position_uuid → {unrealized_pnl, computed_at}
        self.last_refresh_at: datetime | None = None
        self.last_reconcile_at: datetime | None = None
        # 最近 100 条对账告警（FIFO，监控用）
        self.recent_alerts: list[ReconcileAlert] = []
        # 已告警过的不一致（uuid: leg_index）防止刷屏
        self._alerted: set[tuple[str, int]] = set()
        # 上一次 tick 看到的 OPEN positions（uuid 集合）— 用于检测平仓事件
        # 任意 uuid 从 _last_open_uuids 消失（不在当前 OPEN 列表）→ 触发 consolidate
        # 覆盖所有策略所有 CEX 所有 paper/live 模式所有平仓路径（统一 hook）
        self._last_open_uuids: set[str] = set()
        # consolidate 防抖：避免同一秒多笔平仓触发多次划转
        self._last_consolidate_at: float = 0.0
        self._consolidate_debounce_s: float = 30.0

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_authed_adapters(self) -> dict[str, Any]:
        """每次 tick 现算 authed 子集（hot_reload 后立即生效）。

        优先用 app.state.adapters 引用过滤；fallback 到启动时 snapshot。
        """
        ref = self._all_adapters_ref
        if ref is not None:
            current = {n: a for n, a in ref.items() if getattr(a, "_api_key", "")}
            # 同时刷新 self._adapters 让其他方法（_check_leg / _try_auto_unwind 等）一致
            self._adapters = current
            return current
        return self._adapters

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        self._running = True
        logger.info("balance_reconciler_started", interval_s=self._interval)
        while self._running:
            start = asyncio.get_event_loop().time()
            try:
                await self._tick()
            except Exception:
                logger.exception("balance_reconciler_tick_failed")
            elapsed = asyncio.get_event_loop().time() - start
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    async def stop(self) -> None:
        self._running = False
        logger.info("balance_reconciler_stop_requested")

    async def run_once(self) -> dict[str, Any]:
        """单次执行（测试 / 手动触发）。返回当前 snapshot。"""
        await self._tick()
        return self.snapshot()

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        await self._refresh_balances()
        await self._refresh_positions()
        self.last_refresh_at = datetime.now(UTC)
        await self._reconcile()
        await self._cancel_stale_orders()  # T6/R12
        self.last_reconcile_at = datetime.now(UTC)

    async def _refresh_balances(self) -> None:
        """并发拉取各交易所余额到 cache（spot + cross-margin + perp 全合并）。"""
        async def fetch_one(name: str, adapter: Any) -> tuple[str, dict | None]:
            try:
                # 1. spot fetch_balance
                bal = await adapter.fetch_balance()
                out = _balance_to_dict(bal)
                # 2. binance 特有：cross-margin + USDM perp USDT + SPOT 非 USDT 折算
                if name == "binance":
                    extra = await self._fetch_binance_extra_wallets(adapter)
                    if extra:
                        out.update(extra)
                # 3. HTX UTA：CCXT fetch_balance 在 swap client 报 4002，
                #    用 v3/unified_account_info 拿 USDT margin_balance
                elif name == "htx":
                    extra = await self._fetch_htx_extra_wallets(adapter)
                    if extra:
                        out.update(extra)
                return name, out
            except Exception as e:
                logger.warning("reconcile_fetch_balance_failed", exchange=name, error=str(e))
                return name, None

        # 每次 tick 现算 authed adapters（hot_reload 后新增的 CEX 自动纳入）
        active = self._get_authed_adapters()
        results = await asyncio.gather(
            *(fetch_one(n, a) for n, a in active.items()),
            return_exceptions=False,
        )
        for name, data in results:
            if data is not None:
                self.balance_cache[name] = data

    async def _binance_fold_assets_to_usdt(
        self, spot_client: Any, assets: set[str],
    ) -> dict[str, Decimal]:
        """非稳定币资产 → USDT 价格映射。
        优先 hub（避免重复 IO）；剩余资产用 ccxt 批量 fetch_tickers 拉一次。
        """
        from app.exchanges.models import InstrumentType, Symbol  # noqa: PLC0415
        prices: dict[str, Decimal] = {}
        missing: set[str] = set()
        for a in assets:
            if self._hub is not None:
                try:
                    tk = self._hub.get_ticker(
                        "binance", InstrumentType.SPOT, Symbol(a, "USDT"),
                    )
                    if tk is not None:
                        raw_t = getattr(tk, "raw", None)
                        if isinstance(raw_t, dict):
                            p = raw_t.get("last") or raw_t.get("close") or raw_t.get("bid")
                            if p is not None:
                                prices[a] = Decimal(str(p))
                                continue
                except Exception:
                    pass
            missing.add(a)
        if missing and spot_client is not None:
            # 过滤 Binance savings 等不可交易的 wrapper（LDxxx / LPxxx），
            # 以及未在 markets 中的符号，否则 ccxt 整批 reject。
            try:
                if not getattr(spot_client, "markets", None):
                    await spot_client.load_markets()
                markets = getattr(spot_client, "markets", {}) or {}
            except Exception:
                markets = {}
            valid: list[str] = []
            for a in missing:
                if a.startswith(("LD", "LP")):  # Binance Earn 标记
                    continue
                sym = f"{a}/USDT"
                if not markets or sym in markets:
                    valid.append(sym)
            if valid:
                try:
                    tks = await spot_client.fetch_tickers(valid)
                    for sym, tk in (tks or {}).items():
                        base = sym.split("/")[0]
                        p = tk.get("last") or tk.get("close")
                        if p is not None and base in missing:
                            try:
                                prices[base] = Decimal(str(p))
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug("binance_batch_fetch_tickers_failed", error=str(e))
        return prices

    async def _fetch_binance_extra_wallets(self, adapter: Any) -> dict:
        """Binance 全钱包采集 — 现货 + 杠杆(全仓+逐仓) + 合约(U本位+币本位) + 资金账户。

        cache 写入 pseudo-assets（每项独立可累加，dashboard 不重复）：

        现货账户 (SPOT)：
          USDT                  : SPOT USDT 由主 fetch_balance 写入
          USDT_SPOT_OTHERS      : SPOT 非稳定币资产 → USDT-equiv

        杠杆账户 (MARGIN)：
          USDT_MARGIN           : 全仓杠杆 USDT netAsset
          USDT_MARGIN_OTHERS    : 全仓杠杆非 USDT netAsset → USDT-equiv (BNB 抵押等)
          USDT_MARGIN_ISOLATED  : 逐仓杠杆所有交易对净资产 → USDT-equiv

        合约账户 (FUTURES)：
          USDT_PERP             : U 本位合约 totalMarginBalance
          USDT_PERP_COIN        : 币本位合约（如有）→ USDT-equiv

        资金账户 (FUNDING)：
          USDT_FUNDING          : 资金账户 USDT free
          USDT_FUNDING_OTHERS   : 资金账户非稳定币 → USDT-equiv
        """
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        _STABLES = {"USDT", "USDC", "BUSD", "FDUSD", "DAI", "TUSD"}
        out: dict = {}
        spot_client = adapter._clients.get(InstrumentType.SPOT)
        perp_client = adapter._clients.get(InstrumentType.PERPETUAL)
        if spot_client is None:
            return out

        # === Phase 1: 并发拉取所有钱包原始数据 ===
        async def _safe(awaitable):
            try:
                return await awaitable
            except Exception as e:
                return e

        async def _none():
            return None

        spot_bal_task = _safe(spot_client.fetch_balance())
        perp_bal_task = _safe(perp_client.fetch_balance()) if perp_client else _none()
        margin_acc_task = _safe(spot_client.sapi_get_margin_account())
        isolated_task = (
            _safe(spot_client.sapi_get_margin_isolated_account())
            if hasattr(spot_client, "sapi_get_margin_isolated_account") else _none()
        )
        funding_task = _safe(spot_client.sapi_post_asset_get_funding_asset({}))
        coin_perp_task = (
            _safe(spot_client.dapi_private_v2_get_account())
            if hasattr(spot_client, "dapi_private_v2_get_account") else _none()
        )

        spot_bal, perp_bal, margin_acc, iso_acc, funding_data, coin_acc = await asyncio.gather(
            spot_bal_task, perp_bal_task, margin_acc_task, isolated_task,
            funding_task, coin_perp_task,
        )

        # === Phase 2: 收集需要折算的非稳定币资产 ===
        needed: set[str] = set()

        def _collect(asset: str | None, amt: Any) -> Decimal:
            try:
                d = Decimal(str(amt or 0))
            except Exception:
                return Decimal("0")
            if asset and asset not in _STABLES and abs(d) > Decimal("0.0001"):
                needed.add(asset)
            return d

        if isinstance(spot_bal, dict):
            for asset, amt in (spot_bal.get("total") or {}).items():
                _collect(asset, amt)
        if isinstance(margin_acc, dict):
            for a in margin_acc.get("userAssets", []) or []:
                _collect(a.get("asset"), a.get("netAsset"))
        if isinstance(funding_data, list):
            for a in funding_data:
                _collect(a.get("asset"), a.get("free"))
        # 逐仓 + 币本位的折算 BTC 估值需要 BTC ticker
        if isinstance(iso_acc, dict) and Decimal(str(iso_acc.get("totalNetAssetOfBtc") or 0)) > 0:
            needed.add("BTC")
        if isinstance(coin_acc, dict):
            for a in coin_acc.get("assets", []) or []:
                _collect(a.get("asset"), a.get("walletBalance"))

        prices = await self._binance_fold_assets_to_usdt(spot_client, needed)

        # === Phase 3: SPOT 非 USDT ===
        if isinstance(spot_bal, dict):
            spot_others = Decimal("0")
            for asset, amt in (spot_bal.get("total") or {}).items():
                if asset in _STABLES:
                    continue
                try:
                    amt_d = Decimal(str(amt or 0))
                except Exception:
                    continue
                if amt_d <= Decimal("0.0001"):
                    continue
                px = prices.get(asset)
                if px and px > 0:
                    spot_others += amt_d * px
            if spot_others > Decimal("0.01"):
                out["USDT_SPOT_OTHERS"] = {
                    "free": str(spot_others), "total": str(spot_others), "used": "0",
                }

        # === Phase 4: 全仓杠杆 ===
        if isinstance(margin_acc, dict):
            margin_usdt = Decimal("0")
            margin_others = Decimal("0")
            for a in margin_acc.get("userAssets", []) or []:
                asset = a.get("asset")
                try:
                    net = Decimal(str(a.get("netAsset") or 0))
                except Exception:
                    continue
                if abs(net) < Decimal("0.0001"):
                    continue
                if asset == "USDT":
                    margin_usdt = net
                elif asset in _STABLES:
                    margin_others += net  # 其他稳定币按 1:1
                else:
                    px = prices.get(asset)
                    if px and px > 0:
                        margin_others += net * px
            out["USDT_MARGIN"] = {
                "free": str(margin_usdt), "total": str(margin_usdt), "used": "0",
            }
            if abs(margin_others) > Decimal("0.01"):
                out["USDT_MARGIN_OTHERS"] = {
                    "free": str(margin_others), "total": str(margin_others), "used": "0",
                }

        # === Phase 5: 逐仓杠杆 ===
        # Binance API: sapi_get_margin_isolated_account 返回 totalNetAssetOfBtc（已合计 BTC 估值）
        if isinstance(iso_acc, dict):
            try:
                btc_total = Decimal(str(iso_acc.get("totalNetAssetOfBtc") or 0))
            except Exception:
                btc_total = Decimal("0")
            if btc_total > Decimal("0.000001"):
                btc_px = prices.get("BTC")
                if btc_px and btc_px > 0:
                    iso_usdt = btc_total * btc_px
                    if iso_usdt > Decimal("0.01"):
                        out["USDT_MARGIN_ISOLATED"] = {
                            "free": str(iso_usdt), "total": str(iso_usdt), "used": "0",
                        }

        # === Phase 6: U 本位合约 (USDM) ===
        if isinstance(perp_bal, dict):
            info = perp_bal.get("info", {}) or {}
            try:
                # 优先 totalMarginBalance（含未实现盈亏），fallback totalWalletBalance
                perp_total = Decimal(str(
                    info.get("totalMarginBalance") or info.get("totalWalletBalance") or 0,
                ))
            except Exception:
                perp_total = Decimal("0")
            try:
                perp_free = Decimal(str((perp_bal.get("free") or {}).get("USDT") or 0))
            except Exception:
                perp_free = Decimal("0")
            if perp_total > Decimal("0.01") or perp_free > Decimal("0.01"):
                out["USDT_PERP"] = {
                    "free": str(perp_free), "total": str(perp_total),
                    "used": str(max(Decimal("0"), perp_total - perp_free)),
                }

        # === Phase 7: 币本位合约 (COIN-M) ===
        if isinstance(coin_acc, dict):
            coin_usdt = Decimal("0")
            for a in coin_acc.get("assets", []) or []:
                asset = a.get("asset")
                try:
                    wb = Decimal(str(a.get("walletBalance") or 0))
                except Exception:
                    continue
                if abs(wb) < Decimal("0.0001"):
                    continue
                if asset in _STABLES:
                    coin_usdt += wb
                else:
                    px = prices.get(asset)
                    if px and px > 0:
                        coin_usdt += wb * px
            if coin_usdt > Decimal("0.01"):
                out["USDT_PERP_COIN"] = {
                    "free": str(coin_usdt), "total": str(coin_usdt), "used": "0",
                }

        # === Phase 8: 资金账户 (FUNDING) ===
        if isinstance(funding_data, list):
            funding_usdt = Decimal("0")
            funding_others = Decimal("0")
            for a in funding_data:
                asset = a.get("asset")
                try:
                    free = Decimal(str(a.get("free") or 0))
                    locked = Decimal(str(a.get("locked") or 0))
                except Exception:
                    continue
                tot = free + locked
                if tot <= Decimal("0.0001"):
                    continue
                if asset == "USDT":
                    funding_usdt = tot
                elif asset in _STABLES:
                    funding_others += tot
                else:
                    px = prices.get(asset)
                    if px and px > 0:
                        funding_others += tot * px
            if funding_usdt > 0:
                out["USDT_FUNDING"] = {
                    "free": str(funding_usdt), "total": str(funding_usdt), "used": "0",
                }
            if funding_others > Decimal("0.01"):
                out["USDT_FUNDING_OTHERS"] = {
                    "free": str(funding_others), "total": str(funding_others), "used": "0",
                }

        # 失败的 task 留 debug log
        for label, val in (
            ("spot_bal", spot_bal), ("perp_bal", perp_bal),
            ("margin_acc", margin_acc), ("iso_acc", iso_acc),
            ("funding", funding_data), ("coin_acc", coin_acc),
        ):
            if isinstance(val, Exception):
                logger.debug("binance_wallet_fetch_failed", wallet=label, error=str(val))

        return out

    async def _fetch_htx_extra_wallets(self, adapter: Any) -> dict:
        """HTX UTA 模式：CCXT 默认 fetch_balance(swap) 报 4002，
        改用 /linear-swap-api/v3/unified_account_info 拿 swap USDT 余额。

        cache 增加 USDT_HTX_SWAP entry，dashboard 累加。
        """
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        out: dict = {}
        try:
            perp_client = adapter._clients.get(InstrumentType.PERPETUAL)
            if perp_client is None or not hasattr(
                perp_client, "contract_private_get_linear_swap_api_v3_unified_account_info",
            ):
                return out
            r = await perp_client.contract_private_get_linear_swap_api_v3_unified_account_info()
            data = r.get("data") if isinstance(r, dict) else None
            if not isinstance(data, list):
                return out
            for a in data:
                if a.get("margin_asset") != "USDT":
                    continue
                try:
                    mb = Decimal(str(a.get("margin_balance") or 0))
                    frozen = Decimal(str(a.get("margin_frozen") or 0))
                    withdraw = Decimal(str(a.get("withdraw_available") or 0))
                except Exception:
                    continue
                if mb > 0:
                    out["USDT_HTX_SWAP"] = {
                        "free": str(withdraw if withdraw > 0 else mb - frozen),
                        "total": str(mb),
                        "used": str(frozen),
                    }
                break
        except Exception as e:
            logger.debug("reconcile_htx_uta_fetch_failed", error=str(e))
        return out

    async def _refresh_positions(self) -> None:
        """并发拉取各交易所 perp 持仓到 cache（仅鉴权 adapter）。"""
        async def fetch_one(name: str, adapter: Any) -> tuple[str, list | None]:
            try:
                # ccxt fetch_positions 直读 — 避免 adapter.fetch_positions raise NotImplementedError
                from app.exchanges.models import InstrumentType  # noqa: PLC0415
                clients = getattr(adapter, "_clients", {})
                client = clients.get(InstrumentType.PERPETUAL)
                if client is None:
                    return name, []
                raw = await client.fetch_positions()
                positions = []
                for p in raw or []:
                    contracts = float(p.get("contracts") or 0)
                    if abs(contracts) > 0:
                        positions.append({
                            "symbol": p.get("symbol"),
                            "side": p.get("side"),
                            "contracts": contracts,
                            # contractSize = 每张合约代表的 base 数量（如 HTX TIA = 0.1）
                            "contractSize": float(p.get("contractSize") or 1.0),
                            "entry_price": float(p.get("entryPrice") or 0),
                            "unrealized_pnl": float(p.get("unrealizedPnl") or 0),
                        })
                return name, positions
            except Exception as e:
                logger.warning("reconcile_fetch_positions_failed", exchange=name, error=str(e))
                return name, None

        active = self._get_authed_adapters()
        results = await asyncio.gather(
            *(fetch_one(n, a) for n, a in active.items()),
            return_exceptions=False,
        )
        for name, data in results:
            if data is not None:
                self.position_cache[name] = data

    # ------------------------------------------------------------------
    # 对账逻辑
    # ------------------------------------------------------------------

    async def _reconcile(self) -> None:
        """对 DB OPEN positions 与真实余额/持仓对账。"""
        from sqlalchemy import select  # noqa: PLC0415

        from app.core.database import get_session  # noqa: PLC0415
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        from app.models.position import PositionLegRecord, PositionRecord  # noqa: PLC0415

        async with get_session() as session:
            res = await session.execute(
                select(PositionRecord).where(PositionRecord.status == "open")
            )
            open_records: list[PositionRecord] = list(res.scalars().all())
            pos_ids = [r.id for r in open_records]
            legs_by_pos: dict[int, list[PositionLegRecord]] = {}
            if pos_ids:
                leg_res = await session.execute(
                    select(PositionLegRecord).where(
                        PositionLegRecord.position_id.in_(pos_ids)
                    )
                )
                for lr in leg_res.scalars().all():
                    legs_by_pos.setdefault(lr.position_id, []).append(lr)

        # T8: GC 已不再 OPEN 的 alert key — 让真正的下次 inconsistency 能重发
        current_open_uuids = {rec.uuid for rec in open_records}
        stale_keys = [
            k for k in self._alerted
            if isinstance(k, tuple) and len(k) == 2
            and isinstance(k[0], str)
            and not k[0].startswith("orphan:")
            and k[0] not in current_open_uuids
        ]
        for k in stale_keys:
            self._alerted.discard(k)

        # 平仓事件追踪（不再触发自动归集 — 多策略并发场景下不安全）
        # 改为正确计算多钱包余额（USDT_SPOT_OTHERS + USDT_MARGIN + USDT_PERP +
        # USDT_FUNDING + USDT），dashboard 已含全部资产折算
        # 归集仍可通过 POST /system/consolidate 手动触发（用户判断时机）
        self._last_open_uuids = current_open_uuids

        new_alerts: list[ReconcileAlert] = []
        # 缓存 alert → (rec, leg_idx) 用于 #02 跨所单腿失踪后定位幸存腿
        alert_to_pos: dict[int, tuple[Any, int]] = {}

        # paper 模式仓位：DB 有但交易所无真实持仓 — 跳过 leg 对账避免误报
        # #02 perp_basis 当前永远 paper（yaml.paper_trading.live_mode=false）
        paper_only_instances = _get_paper_only_instances_global()

        # 预先聚合：同一 (exchange, symbol) 下所有 OPEN perp leg 的期望总量
        # 交易所只有一个合并仓位，而 DB 可能有多条平行仓位（如两个 #04 position 都开了 TIA）
        from app.exchanges.models import InstrumentType as _IT  # noqa: PLC0415
        _perp_expected: dict[tuple[str, str], Decimal] = {}
        for _rec in open_records:
            if _rec.strategy_instance in paper_only_instances:
                continue
            for _leg in legs_by_pos.get(_rec.id, []):
                if _leg.instrument_type == _IT.PERPETUAL.value:
                    _k = (_leg.exchange, _leg.symbol)
                    _perp_expected[_k] = _perp_expected.get(_k, Decimal("0")) + _leg.size
        self._perp_expected = _perp_expected

        for rec in open_records:
            if rec.strategy_instance in paper_only_instances:
                continue  # 跳过 paper 仓位 leg 对账
            legs = legs_by_pos.get(rec.id, [])
            for idx, leg in enumerate(legs):
                alert = self._check_leg(rec, leg, idx)
                if alert is not None:
                    new_alerts.append(alert)
                    alert_to_pos[id(alert)] = (rec, idx)

        # 残留持仓检测：真实交易所 perp 持仓 vs DB OPEN
        new_alerts.extend(self._detect_orphan_positions(open_records, legs_by_pos))

        # R7: mark-to-market PnL（用 MarketDataHub ticker）
        self._compute_live_pnl(open_records, legs_by_pos)

        if new_alerts:
            for alert in new_alerts:
                self.recent_alerts.append(alert)
                if len(self.recent_alerts) > 100:
                    self.recent_alerts.pop(0)
                logger.error(
                    "reconcile_alert",
                    type=alert.type,
                    severity=alert.severity,
                    exchange=alert.exchange,
                    symbol=alert.symbol,
                    detail=alert.detail,
                )
                # 自动修复路径：
                # - orphan_position（DB CLOSED 但有真实持仓）→ 反向平掉
                # - single_leg_exposure on #02 perp_basis（一腿被强平）→ 平另一腿
                action_taken = None
                if alert.type == "orphan_position":
                    unwound = await self._try_auto_unwind_orphan(alert)
                    action_taken = "auto_unwound" if unwound else "alert_only_above_cap"
                elif alert.type == "single_leg_exposure":
                    rec_idx = alert_to_pos.get(id(alert))
                    if rec_idx is not None:
                        rec, lost_idx = rec_idx
                        if (rec.strategy_instance or "") == "perp_basis_main":
                            closed = await self._try_auto_close_remaining_leg(
                                rec, lost_idx, legs_by_pos.get(rec.id, []),
                            )
                            action_taken = (
                                "auto_closed_remaining_leg" if closed
                                else "alert_only_remaining_leg_skip"
                            )
                await self._persist_alert(alert, action_taken=action_taken)
                self._notify_alert(alert)

    def _notify_alert(self, alert: ReconcileAlert) -> None:
        """R10: 推送 Telegram。失败不影响主循环。"""
        try:
            from app.notifications import notify_reconcile_alert  # noqa: PLC0415
            notify_reconcile_alert(
                alert_type=alert.type,
                severity=alert.severity,
                exchange=alert.exchange,
                symbol=alert.symbol,
                explanation=alert.detail.get("explanation", ""),
            )
        except Exception:
            logger.warning("reconcile_telegram_notify_failed", type=alert.type)

    def _check_leg(self, rec: Any, leg: Any, idx: int) -> ReconcileAlert | None:
        """检查单条 leg 与真实持仓是否匹配。返回告警 or None。"""
        from app.exchanges.models import InstrumentType  # noqa: PLC0415

        key = (rec.uuid, idx)
        # 已经告警过且仍未解决就不重复告警
        if key in self._alerted:
            return None

        # PERPETUAL leg → 期望在 perp position_cache 中找到
        if leg.instrument_type == InstrumentType.PERPETUAL.value:
            # GRACE_PERIOD: 跳过最近 _RECONCILE_OPEN_GRACE_SECONDS 秒内开的仓
            # 防止 binance API 同步延迟导致的误报（fetch_positions 滞后 N 秒）
            opened_at = getattr(rec, "opened_at", None)
            if opened_at is not None:
                try:
                    from datetime import datetime, timezone  # noqa: PLC0415
                    age_s = (datetime.now(timezone.utc) - opened_at).total_seconds()
                    if age_s < _RECONCILE_OPEN_GRACE_SECONDS:
                        return None
                except Exception:
                    pass
            cached = self.position_cache.get(leg.exchange, [])
            match = next(
                (p for p in cached if _normalize_symbol(p.get("symbol", "")) == leg.symbol),
                None,
            )
            if match is None:
                self._alerted.add(key)
                return ReconcileAlert(
                    type="single_leg_exposure",
                    severity="critical",
                    exchange=leg.exchange,
                    symbol=leg.symbol,
                    detail={
                        "position_uuid": rec.uuid,
                        "leg_idx": idx,
                        "expected_size": str(leg.size),
                        "actual": "no_perp_position",
                        "explanation": "DB OPEN 但 perp 真实持仓缺失（已强平/被手动平）",
                    },
                )
            # contracts → 真实 token 数量（合约张数 × 每张 base 数，HTX TIA = 0.1/张）
            _contract_size = Decimal(str(match.get("contractSize") or 1.0))
            actual = abs(Decimal(str(match["contracts"])) * _contract_size)
            # 用该 (exchange, symbol) 下所有 OPEN leg 的期望总量做对账
            # 避免"两个 position 各期望 112 TIA，但交易所只有 224 TIA 合并仓位"的误报
            expected_total = getattr(self, "_perp_expected", {}).get(
                (leg.exchange, leg.symbol), leg.size
            )
            drift = abs(actual - expected_total)
            tolerance = expected_total * _QTY_DRIFT_TOLERANCE_PCT / Decimal("100")
            if drift > tolerance:
                self._alerted.add(key)
                return ReconcileAlert(
                    type="qty_drift",
                    severity="high",
                    exchange=leg.exchange,
                    symbol=leg.symbol,
                    detail={
                        "position_uuid": rec.uuid,
                        "leg_idx": idx,
                        "expected_total": str(expected_total),
                        "expected_this_leg": str(leg.size),
                        "actual_qty": str(actual),
                        "actual_contracts": str(match["contracts"]),
                        "contract_size": str(_contract_size),
                        "drift_pct": str(drift / expected_total * 100),
                    },
                )

        # SPOT leg → 期望在 balance_cache 中 base 余额 >= leg.size
        elif leg.instrument_type == InstrumentType.SPOT.value:
            bal = self.balance_cache.get(leg.exchange, {})
            base = leg.symbol.split("/")[0] if "/" in leg.symbol else None
            if base is None:
                return None
            free = Decimal(str((bal.get(base) or {}).get("free") or 0))
            tolerance = leg.size * _QTY_DRIFT_TOLERANCE_PCT / Decimal("100")
            if free + tolerance < leg.size:
                self._alerted.add(key)
                return ReconcileAlert(
                    type="single_leg_exposure",
                    severity="critical",
                    exchange=leg.exchange,
                    symbol=leg.symbol,
                    detail={
                        "position_uuid": rec.uuid,
                        "leg_idx": idx,
                        "expected_size": str(leg.size),
                        "actual_free": str(free),
                        "explanation": "DB OPEN 但 spot 真实余额不足（被卖出/转走）",
                    },
                )

        return None

    def _detect_orphan_positions(
        self,
        open_records: list,
        legs_by_pos: dict,
    ) -> list[ReconcileAlert]:
        """真实 perp 持仓但 DB 找不到对应 OPEN — 残留单腿。"""
        from app.exchanges.models import InstrumentType  # noqa: PLC0415

        # 构建 DB 期望的 (exchange, symbol) 集合
        db_expected: set[tuple[str, str]] = set()
        for rec in open_records:
            for leg in legs_by_pos.get(rec.id, []):
                if leg.instrument_type == InstrumentType.PERPETUAL.value:
                    db_expected.add((leg.exchange, leg.symbol))

        alerts: list[ReconcileAlert] = []
        for ex_name, positions in self.position_cache.items():
            for p in positions:
                sym = _normalize_symbol(p.get("symbol", ""))
                if (ex_name, sym) not in db_expected:
                    key = (f"orphan:{ex_name}:{sym}", 0)
                    if key in self._alerted:
                        continue
                    self._alerted.add(key)
                    alerts.append(ReconcileAlert(
                        type="orphan_position",
                        severity="high",
                        exchange=ex_name,
                        symbol=sym,
                        detail={
                            "contracts": p.get("contracts"),
                            "side": p.get("side"),
                            "entry_price": p.get("entry_price") or 0,
                            "explanation": "真实 perp 持仓但 DB 无对应 OPEN 仓位",
                        },
                    ))
        return alerts

    async def _persist_alert(
        self, alert: ReconcileAlert, action_taken: str | None = None,
    ) -> None:
        """写入 risk_events 表（best-effort，DB 失败不影响主循环）。"""
        from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
        await write_risk_event(
            event_type=alert.type,
            severity=alert.severity,
            description=f"[{alert.exchange}/{alert.symbol}] {alert.detail.get('explanation', '')}",
            action_taken=action_taken,
            extra=alert.detail,
        )

    async def _cancel_stale_orders(self) -> None:
        """T6/R12: 撤掉超过 _STALE_ORDER_MAX_AGE_S 仍未成交的挂单。

        Why: market 单应即时成交；limit 单超时未填可能是网络问题或价格远离市场，
        长期占用资金 / 配额。撤掉 + 写 risk_event。

        P1-7: 各 exchange 串行 → 并发，5 家 ~1.5s → ~300ms。
        """
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        max_age_ms = _STALE_ORDER_MAX_AGE_S * 1000

        async def _scan_one_exchange(ex_name: str, adapter: Any) -> None:
            try:
                if not hasattr(adapter, "fetch_open_orders"):
                    return
                orders = await adapter.fetch_open_orders()
                stale_orders = []
                for o in (orders or []):
                    ts = getattr(o, "timestamp", 0) or 0
                    if ts > 0 and (now_ms - ts) > max_age_ms:
                        stale_orders.append(o)
                if not stale_orders:
                    return
                for o in stale_orders:
                    try:
                        cancelled = await adapter.cancel_order(
                            o.order_id, o.symbol,
                        )
                        age_min = (now_ms - (o.timestamp or now_ms)) / 60_000
                        logger.warning(
                            "stale_order_cancelled",
                            exchange=ex_name,
                            symbol=str(o.symbol),
                            order_id=o.order_id,
                            age_minutes=round(age_min, 1),
                            cancelled=cancelled,
                        )
                        from app.services.risk_event_service import write_risk_event  # noqa: PLC0415
                        await write_risk_event(
                            event_type="stale_order_cancelled",
                            severity="medium",
                            description=(
                                f"[{ex_name}/{o.symbol}] order {o.order_id[:12]} "
                                f"挂单 {age_min:.1f}min 未成交自动撤"
                            ),
                            action_taken="cancel_order" if cancelled else "cancel_failed",
                            extra={
                                "order_id": o.order_id,
                                "side": o.side.value if hasattr(o.side, "value") else str(o.side),
                                "instrument": o.instrument.value if hasattr(o.instrument, "value") else str(o.instrument),
                                "age_minutes": age_min,
                            },
                        )
                    except Exception as e:
                        logger.warning(
                            "stale_order_cancel_failed",
                            exchange=ex_name, order_id=o.order_id, error=str(e),
                        )
            except Exception as e:
                logger.debug("stale_order_scan_failed", exchange=ex_name, error=str(e))

        # 并发跑所有 exchange
        await asyncio.gather(
            *(_scan_one_exchange(ex_name, adapter) for ex_name, adapter in self._adapters.items()),
            return_exceptions=True,
        )

    def _compute_live_pnl(self, open_records: list, legs_by_pos: dict) -> None:
        """R7: 用 MarketDataHub ticker 计算每个 OPEN position 的 mark-to-market PnL。

        无 hub / 无 ticker 时静默跳过该 position。
        """
        from app.exchanges.models import InstrumentType  # noqa: PLC0415
        if self._hub is None:
            return
        new_pnl: dict[str, dict] = {}
        for rec in open_records:
            legs = legs_by_pos.get(rec.id, [])
            unrealized = Decimal("0")
            has_data = False
            for leg in legs:
                try:
                    instrument = (
                        InstrumentType.PERPETUAL
                        if leg.instrument_type == "perpetual"
                        else InstrumentType.SPOT
                    )
                    entry_obj = self._hub.get_ticker(
                        leg.exchange, instrument,
                        _parse_symbol_for_hub(leg.symbol),
                    )
                    if entry_obj is None:
                        continue
                    current = entry_obj.last or entry_obj.bid
                    if current is None or current <= 0:
                        continue
                    has_data = True
                    if leg.side == "buy":
                        unrealized += (Decimal(str(current)) - leg.entry_price) * leg.size
                    else:  # sell
                        unrealized += (leg.entry_price - Decimal(str(current))) * leg.size
                except Exception:
                    continue
            if has_data:
                new_pnl[rec.uuid] = {
                    "unrealized_pnl": str(unrealized.quantize(Decimal("0.0001"))),
                    "computed_at": datetime.now(UTC).isoformat(),
                }
        self.live_pnl_cache = new_pnl

    async def _try_auto_unwind_orphan(self, alert: ReconcileAlert) -> bool:
        """R2.b: 真实交易所有残留 perp 持仓但 DB 无 OPEN → 自动反向平掉。

        保护：
          - 仅 binance（hedge mode positionSide）
          - 单笔残留名义价值 ≤ _AUTO_UNWIND_MAX_NOTIONAL_USD ($200)
          - 同 (exchange, symbol) 仅尝试一次（防失败循环）

        Returns
        -------
        bool: 是否成功平仓
        """
        key = (alert.exchange, alert.symbol)
        if key in _auto_unwind_attempted:
            return False
        _auto_unwind_attempted.add(key)

        if alert.exchange != "binance":
            return False
        adapter = self._adapters.get(alert.exchange)
        if adapter is None:
            return False
        try:
            from app.exchanges.models import InstrumentType  # noqa: PLC0415

            contracts = abs(Decimal(str(alert.detail.get("contracts") or 0)))
            side = alert.detail.get("side", "")
            entry = Decimal(str(alert.detail.get("entry_price") or 0))
            notional = contracts * entry
            cap = _resolve_auto_unwind_cap()
            if notional > cap:
                logger.warning(
                    "auto_unwind_skip_above_cap",
                    symbol=alert.symbol,
                    notional=str(notional),
                    cap=str(cap),
                )
                return False
            perp_client = adapter._clients.get(InstrumentType.PERPETUAL)
            if perp_client is None:
                return False
            close_side = "buy" if side == "short" else "sell"
            params = {"positionSide": "SHORT" if side == "short" else "LONG"}
            if close_side == "buy":
                r = await perp_client.create_market_buy_order(
                    alert.symbol, float(contracts), params=params,
                )
            else:
                r = await perp_client.create_market_sell_order(
                    alert.symbol, float(contracts), params=params,
                )
            logger.warning(
                "auto_unwind_orphan_executed",
                symbol=alert.symbol,
                contracts=str(contracts),
                side=close_side,
                notional=str(notional),
                order_id=r.get("id"),
            )
            return True
        except Exception as e:
            logger.error(
                "auto_unwind_failed",
                symbol=alert.symbol,
                error=str(e),
            )
            return False

    async def _post_close_consolidate(self, closed_uuids: list[str]) -> None:
        """平仓后归集：把多钱包 USDT 划转到 spot，简化资金管理。

        防抖：30s 内多笔平仓只触发一次（避免连环划转）。
        覆盖所有策略所有 CEX 所有 paper/live 模式（reconciler 自身判断 OPEN→CLOSED）。
        """
        now = asyncio.get_event_loop().time()
        if (now - self._last_consolidate_at) < self._consolidate_debounce_s:
            return
        self._last_consolidate_at = now
        try:
            from app.services.balance_consolidator import consolidate_to_spot  # noqa: PLC0415
            adapters = self._get_authed_adapters()
            result = await consolidate_to_spot(adapters)
            transfer_count = sum(len(r.get("transfers", [])) for r in result.values())
            if transfer_count > 0:
                # 清 cache 让下次 tick 立即重读
                self.balance_cache.clear()
            logger.info(
                "consolidate_after_close",
                closed_count=len(closed_uuids),
                transfer_count=transfer_count,
                exchanges=list(result.keys()),
            )
        except Exception:
            logger.exception("consolidate_after_close_failed")

    async def _try_auto_close_remaining_leg(
        self,
        rec: Any,
        lost_idx: int,
        legs: list[Any],
    ) -> bool:
        """#02 perp_basis 一腿被强平 → 自动平掉幸存腿恢复 delta-neutral。

        触发条件：
          - alert.type == "single_leg_exposure"
          - rec.strategy_instance == "perp_basis_main"

        保护：
          - 单笔幸存腿名义价值 ≤ _AUTO_UNWIND_MAX_NOTIONAL_USD ($200)
          - 同 (position_uuid) 仅尝试一次（防失败循环 reuse _auto_unwind_attempted set）

        Returns
        -------
        bool: 是否成功平掉幸存腿
        """
        from app.exchanges.models import InstrumentType  # noqa: PLC0415

        attempt_key = (f"close_remaining:{rec.uuid}", 0)
        if attempt_key in _auto_unwind_attempted:
            return False
        _auto_unwind_attempted.add(attempt_key)

        # 找幸存腿（idx != lost_idx 的 perp leg）
        surviving = [
            (i, l) for i, l in enumerate(legs)
            if i != lost_idx and l.instrument_type == InstrumentType.PERPETUAL.value
        ]
        if not surviving:
            return False
        s_idx, s_leg = surviving[0]

        adapter = self._adapters.get(s_leg.exchange)
        if adapter is None:
            logger.warning(
                "auto_close_remaining_no_adapter",
                position_uuid=rec.uuid,
                exchange=s_leg.exchange,
            )
            return False

        # 估算名义价值
        try:
            entry = Decimal(str(s_leg.entry_price or 0))
            size = Decimal(str(s_leg.size))
            notional = abs(entry * size)
        except Exception:
            notional = Decimal("0")
        cap = _resolve_auto_unwind_cap()
        if notional > cap:
            logger.warning(
                "auto_close_remaining_skip_above_cap",
                position_uuid=rec.uuid,
                symbol=s_leg.symbol,
                notional=str(notional),
                cap=str(cap),
            )
            return False

        try:
            perp_client = adapter._clients.get(InstrumentType.PERPETUAL)
            if perp_client is None:
                return False
            # 平仓方向：long 腿 → sell，short 腿 → buy
            leg_side = (s_leg.side or "").lower()
            close_side = "sell" if leg_side == "long" else "buy"
            # CRITICAL: reduceOnly + CCXT unified swap symbol
            # 否则 HTX 会把 close 当作"新开 long 仓"，引用 spot trade 余额报错
            # （5/13 STABLE 误报触发的 account-frozen-balance-insufficient-error: left 41 根因）
            params: dict[str, Any] = {"reduceOnly": True}
            if s_leg.exchange == "binance":
                # binance hedge mode 需要 positionSide
                params["positionSide"] = "LONG" if leg_side == "long" else "SHORT"
            # CCXT swap symbol 必须带 :QUOTE 后缀（HTX/OKX/Bybit linear perp 都需要）
            close_symbol = s_leg.symbol
            if ":" not in close_symbol:
                quote = close_symbol.split("/")[1] if "/" in close_symbol else "USDT"
                close_symbol = f"{close_symbol}:{quote}"
            if close_side == "sell":
                r = await perp_client.create_market_sell_order(
                    close_symbol, float(size), params=params,
                )
            else:
                r = await perp_client.create_market_buy_order(
                    close_symbol, float(size), params=params,
                )
            logger.warning(
                "auto_close_remaining_leg_executed",
                position_uuid=rec.uuid,
                symbol=s_leg.symbol,
                exchange=s_leg.exchange,
                side=close_side,
                size=str(size),
                notional=str(notional),
                order_id=(r or {}).get("id") if isinstance(r, dict) else None,
            )
            return True
        except Exception as e:
            logger.error(
                "auto_close_remaining_failed",
                position_uuid=rec.uuid,
                symbol=s_leg.symbol,
                error=str(e),
            )
            return False

    # ------------------------------------------------------------------
    # 状态 snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """供 endpoint 直读的 snapshot。"""
        return {
            "running": self._running,
            "interval_s": self._interval,
            "last_refresh_at": self.last_refresh_at.isoformat() if self.last_refresh_at else None,
            "last_reconcile_at": self.last_reconcile_at.isoformat() if self.last_reconcile_at else None,
            "balance_cache": self.balance_cache,
            "position_cache": self.position_cache,
            "live_pnl": self.live_pnl_cache,
            "alerts_total": len(self.recent_alerts),
            "recent_alerts": [
                {
                    "type": a.type,
                    "severity": a.severity,
                    "exchange": a.exchange,
                    "symbol": a.symbol,
                    "detail": a.detail,
                    "detected_at": a.detected_at.isoformat(),
                }
                for a in self.recent_alerts[-20:]
            ],
        }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _balance_to_dict(bal: Any) -> dict:
    """把 Balance / ccxt dict 转换成扁平 dict[asset → {free, total, used}]。"""
    if isinstance(bal, dict):
        out: dict[str, dict] = {}
        for k, v in bal.items():
            if isinstance(v, dict) and ("free" in v or "total" in v):
                out[k] = {
                    "free": str(v.get("free") or 0),
                    "total": str(v.get("total") or 0),
                    "used": str(v.get("used") or 0),
                }
        return out
    # app.exchanges.models.Balance(entries=[BalanceEntry(asset, free, locked)])
    if hasattr(bal, "entries"):
        return {
            e.asset: {
                "free": str(e.free),
                "total": str(e.total),
                "used": str(e.locked),
            }
            for e in (bal.entries or [])
        }
    return {}


def _parse_symbol_for_hub(s: str) -> Any:
    """'FIL/USDT' → Symbol('FIL','USDT')，供 MarketDataHub.get_ticker 使用。"""
    from app.exchanges.models import Symbol  # noqa: PLC0415
    if "/" in s:
        base, _, quote = s.partition("/")
        return Symbol(base, quote)
    return Symbol(s, "USDT")


def _normalize_symbol(s: str) -> str:
    """ccxt perp symbol 'FIL/USDT:USDT' → 'FIL/USDT'。"""
    if ":" in s:
        return s.split(":")[0]
    return s
