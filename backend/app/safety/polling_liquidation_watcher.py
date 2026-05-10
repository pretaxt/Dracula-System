"""轮询式强平检测 — 跨交易所通用，与 Binance WS-based watcher 互补。

设计：
- 每 30s 调 adapter.fetch_positions() 拿到交易所 perp 仓位真实状态
- 与策略期望开仓集（来自 PositionManager）比对
- 期望开但交易所显示 size=0 → 视为强平 → 触发回调（典型动作：紧急关 spot）

适用：
- OKX（无原生强平 WS 数据流）
- 任何不便接 WS 私有数据流的交易所

不替代 strategy 自己的 _maybe_close_perp_margin_risk（80% margin 主动关），
也不替代 Binance WS-based watcher（毫秒级实时）—— 这是兜底网。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

from app.core.logging import get_logger
from app.exchanges.models import Symbol

logger = get_logger(__name__)

# (symbol_str, side_str) 期望集合，比如 {("BTC/USDT", "sell")}
ExpectedPositions = set[tuple[str, str]]
LiquidationCallback = Callable[[Symbol, dict], Awaitable[None]]
ExpectedGetter = Callable[[], ExpectedPositions]

_DEFAULT_INTERVAL_S = 30.0
_BACKOFF_S = 5.0


class PollingLiquidationWatcher:
    """周期 fetch_positions，检测应开未开的 perp 腿（视为强平）。

    Parameters
    ----------
    adapter:
        已认证的 ExchangeAdapter（需实现 fetch_positions）。
    exchange_name:
        ``"okx"`` / ``"binance"`` 等。仅用于日志和 callback 标识。
    on_liquidation:
        ``async (symbol: Symbol, raw_event: dict) -> None``。
        典型：emergency close spot leg + Telegram 通知。
    get_expected_positions:
        无参函数，返回当前应在该交易所开着的 perp 腿集合
        ``{(symbol_str, side_str), ...}``。每轮询前调用，反映当时仓位。
    interval_seconds:
        轮询间隔，默认 30s。
    """

    def __init__(
        self,
        adapter,
        exchange_name: str,
        on_liquidation: LiquidationCallback,
        get_expected_positions: ExpectedGetter,
        interval_seconds: float = _DEFAULT_INTERVAL_S,
    ) -> None:
        self._adapter = adapter
        self._exchange_name = exchange_name
        self._callback = on_liquidation
        self._get_expected = get_expected_positions
        self._interval = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # 仅追踪 "上次 poll 时确实存在的 expected 集"，避免开仓 race（
        # 仓位刚开但交易所推送有延迟 → 误判强平）
        self._previously_seen: ExpectedPositions = set()

    async def start(self) -> None:
        """非阻塞启动轮询任务。重复调用幂等。"""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name=f"polling_liquidation_watcher_{self._exchange_name}",
        )
        logger.info("polling_liquidation_watcher_started", exchange=self._exchange_name,
                    interval_s=self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("polling_liquidation_watcher_stopped", exchange=self._exchange_name)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("polling_liquidation_tick_failed",
                                 exchange=self._exchange_name)
            try:
                await asyncio.sleep(self._interval if self._running else _BACKOFF_S)
            except asyncio.CancelledError:
                raise

    async def _tick(self) -> None:
        """一次轮询：拉取交易所 perp 仓位 → 与期望集比对 → 缺失即触发回调。"""
        # W5: getter 可能是 async（DB 查询），兼容 sync + async 两种返回
        raw = self._get_expected()
        if asyncio.iscoroutine(raw):
            raw = await raw
        expected: ExpectedPositions = set(raw)

        # 仅"上次 poll 已经看到 + 当前期望仍包含"的腿才算稳定追踪
        # 新建仓的腿可能交易所 fetch 还没反映 → 等下轮再算
        actively_tracked = expected & self._previously_seen

        if not expected:
            self._previously_seen = expected
            return

        try:
            exchange_positions = await self._adapter.fetch_positions()
        except Exception as exc:
            logger.warning("polling_fetch_positions_failed",
                           exchange=self._exchange_name, error=str(exc)[:200])
            return  # 不更新 _previously_seen，下轮再试

        on_exchange: ExpectedPositions = set()
        for p in exchange_positions:
            try:
                size = getattr(p, "size", 0) or 0
                if size > 0:
                    on_exchange.add((str(p.symbol), p.side.value))
            except Exception:
                continue

        # 失踪 = 之前期望且活跃 - 现在交易所还在
        missing = actively_tracked - on_exchange

        for symbol_str, side_str in missing:
            logger.warning(
                "polling_perp_position_lost_likely_liquidation",
                exchange=self._exchange_name,
                symbol=symbol_str, side=side_str,
            )
            try:
                if "/" in symbol_str:
                    base, quote = symbol_str.split("/", 1)
                    sym = Symbol(base.strip(), quote.strip())
                else:
                    sym = Symbol(symbol_str, "USDT")
                await self._callback(sym, {
                    "source": "polling_watcher",
                    "exchange": self._exchange_name,
                    "side": side_str,
                    "reason": "position_missing_on_exchange",
                })
            except Exception:
                logger.exception("polling_liquidation_callback_failed",
                                 exchange=self._exchange_name, symbol=symbol_str)

        # 更新基线 — 当前确实在交易所的 expected 子集
        self._previously_seen = expected & on_exchange
