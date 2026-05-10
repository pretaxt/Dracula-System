"""Binance USDM 强平监听器 — 实盘双腿同步保护。

当永续 SHORT 腿被交易所强制清算时（即便价格瞬间穿越我们的 -80% 保证金止损），
现货 LONG 腿仍滞留在另一账户，瞬间变成裸多敞口。本模块订阅 Binance USDM
用户数据流，检测 ORDER_TRADE_UPDATE.o.ot == "LIQUIDATION" 事件，立即触发
回调（典型回调：在现货端市价 SELL 解除裸多 + 发送 Telegram 告警）。

设计要点:
  - listenKey 从 POST /fapi/v1/listenKey 获取，每 30 分钟 PUT 续期（官方 60 分钟过期）
  - WS 断线自动重连（5 秒退避）
  - paper 模式下无真实 perp 仓位，watcher 不会触发任何事件，但仍可启动用于实盘演练
"""
from __future__ import annotations

import asyncio
import json
from typing import Awaitable, Callable, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger
from app.exchanges.models import Symbol

logger = get_logger(__name__)

LiquidationCallback = Callable[[Symbol, dict], Awaitable[None]]

_BINANCE_USDM_REST = "https://fapi.binance.com"
_BINANCE_USDM_WS_BASE = "wss://fstream.binance.com/ws"
_KEEPALIVE_INTERVAL_MIN = 30     # 留 30 分钟缓冲（官方 60 分钟过期）
_RECONNECT_BACKOFF_S = 5.0


class LiquidationWatcher:
    """监听 Binance USDM 用户数据流，对永续单腿强平事件触发回调。

    Parameters
    ----------
    api_key, api_secret:
        Binance USDM 凭据。仅 api_key 用于 listenKey/WS；api_secret 保留供日后扩展。
    on_liquidation:
        强平回调。签名 `async (symbol: Symbol, raw_event: dict) -> None`。
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        on_liquidation: LiquidationCallback,
        on_critical_failure: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret  # noqa: F841 — 保留供以后签名校验扩展
        self._callback = on_liquidation
        self._on_critical_failure = on_critical_failure
        self._listen_key: Optional[str] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # W6 listenKey 健康度跟踪
        self._consecutive_keepalive_failures = 0

    async def start(self) -> None:
        """非阻塞启动：在后台跑 WS 主循环 + listenKey 续期循环。"""
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="liquidation_watcher")
        logger.info("liquidation_watcher_started")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("liquidation_watcher_stopped")

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        try:
            await asyncio.gather(self._ws_loop(), self._keepalive_loop())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("liquidation_watcher_unexpected_error")

    async def _ws_loop(self) -> None:
        while self._running:
            try:
                self._listen_key = await self._get_listen_key()
                logger.info(
                    "liquidation_watcher_listen_key_acquired",
                    key_prefix=self._listen_key[:8] if self._listen_key else "",
                )
                url = f"{_BINANCE_USDM_WS_BASE}/{self._listen_key}"
                async with websockets.connect(url, ping_interval=180, ping_timeout=10) as ws:
                    logger.info("liquidation_watcher_ws_connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            await self._handle_event(json.loads(raw))
                        except Exception:
                            logger.exception("liquidation_watcher_event_handler_failed")
            except ConnectionClosed as e:
                if not self._running:
                    break
                logger.warning("liquidation_watcher_ws_closed", code=e.code, reason=str(e.reason)[:80])
            except Exception as e:
                if not self._running:
                    break
                logger.warning("liquidation_watcher_ws_error", error=str(e)[:200])
            if self._running:
                await asyncio.sleep(_RECONNECT_BACKOFF_S)

    async def _keepalive_loop(self) -> None:
        # W6 listenKey halt-all：连续 N 次续签失败 → 触发 critical 回调（halt 所有策略）
        max_failures = 3
        while self._running:
            await asyncio.sleep(_KEEPALIVE_INTERVAL_MIN * 60)
            if not self._running or not self._listen_key:
                continue
            try:
                await self._put_listen_key()
                logger.info("liquidation_watcher_keepalive_ok")
                # 续签成功，重置失败计数
                if self._consecutive_keepalive_failures > 0:
                    logger.info(
                        "liquidation_watcher_keepalive_recovered",
                        previous_failures=self._consecutive_keepalive_failures,
                    )
                self._consecutive_keepalive_failures = 0
            except Exception as exc:
                self._consecutive_keepalive_failures += 1
                logger.exception(
                    "liquidation_watcher_keepalive_failed",
                    consecutive_failures=self._consecutive_keepalive_failures,
                )
                if self._consecutive_keepalive_failures >= max_failures:
                    msg = (
                        f"binance listenKey 续签连续失败 "
                        f"{self._consecutive_keepalive_failures} 次 — "
                        f"用户数据流不可信，可能丢失强平事件"
                    )
                    logger.error("liquidation_watcher_critical_halt", reason=msg)
                    if self._on_critical_failure is not None:
                        try:
                            await self._on_critical_failure(msg)
                        except Exception:
                            logger.exception("liquidation_watcher_critical_callback_failed")
                    # 重置计数避免反复触发；继续运行让 ws_loop 试着重连
                    self._consecutive_keepalive_failures = 0

    # ------------------------------------------------------------------
    # listenKey 管理
    # ------------------------------------------------------------------

    async def _get_listen_key(self) -> str:
        url = f"{_BINANCE_USDM_REST}/fapi/v1/listenKey"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, headers={"X-MBX-APIKEY": self._api_key})
            r.raise_for_status()
            data = r.json()
            return data["listenKey"]

    async def _put_listen_key(self) -> None:
        url = f"{_BINANCE_USDM_REST}/fapi/v1/listenKey"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(url, headers={"X-MBX-APIKEY": self._api_key})
            r.raise_for_status()

    # ------------------------------------------------------------------
    # 事件解析（公开以便测试）
    # ------------------------------------------------------------------

    async def _handle_event(self, event: dict) -> None:
        """处理一条用户数据流事件。仅 LIQUIDATION 类订单触发回调。"""
        if event.get("e") != "ORDER_TRADE_UPDATE":
            return
        order = event.get("o", {})
        # ot = origin type. "LIQUIDATION" 表示该订单由强平触发
        if order.get("ot") != "LIQUIDATION":
            return
        ccxt_symbol = order.get("s")  # e.g. "BTCUSDT"
        if not isinstance(ccxt_symbol, str):
            return
        symbol = self._parse_usdt_symbol(ccxt_symbol)
        if symbol is None:
            logger.warning("liquidation_unknown_symbol_format", raw=ccxt_symbol)
            return

        logger.warning(
            "perp_liquidation_detected",
            symbol=str(symbol),
            side=order.get("S"),
            quantity=order.get("q"),
            avg_price=order.get("ap"),
            status=order.get("X"),
        )
        try:
            await self._callback(symbol, order)
        except Exception:
            logger.exception("liquidation_callback_failed", symbol=str(symbol))

    @staticmethod
    def _parse_usdt_symbol(ccxt_symbol: str) -> Optional[Symbol]:
        """`BTCUSDT` → `Symbol("BTC", "USDT")`。仅支持 USDT 永续，base 必须 alphanumeric。"""
        if not ccxt_symbol.endswith("USDT") or len(ccxt_symbol) <= 4:
            return None
        base = ccxt_symbol[:-4]
        if not base.isalnum():
            return None
        return Symbol(base, "USDT")
