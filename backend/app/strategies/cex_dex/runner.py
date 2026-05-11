"""CEX-DEX 套利主循环。

paper 模式: 扫描 + 记录 + Telegram，不下单。
live  模式: 扫描 + 二次报价确认 + 双腿同时执行 + 记录。

日损熔断: 当天已亏损超过 max_daily_loss_usd 时停止执行（仍扫描）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Optional

from app.core.logging import get_logger
from app.core.redis_client import publish
from app.exchanges.base import ExchangeAdapter
from app.notifications.telegram import notify_system

from .cex_executor import CexExecutor
from .config import CexDexConfig
from .dex_executor import DexExecutor
from .gas_oracle import check_gas_ok
from .scanner import CexDexOpportunity, CexDexScanner

logger = get_logger(__name__)

OPPORTUNITIES_CHANNEL = "dracula:cex_dex:opportunities"


class CexDexRunner:
    def __init__(
        self,
        config: CexDexConfig,
        w3: object,
        cex_adapter: ExchangeAdapter,
        private_key: str,
    ) -> None:
        self._cfg = config
        self._w3 = w3
        self._scanner = CexDexScanner(config, w3, cex_adapter)
        self._cex_exec = CexExecutor(cex_adapter, config.cex_taker_fee_rate)
        self._dex_exec = DexExecutor(w3, private_key, config.max_slippage_bps) if private_key else None

        self._daily_loss: Decimal = Decimal("0")
        self._last_loss_date: date = date.today()
        self._open_trades: int = 0
        self._eth_usd: Decimal = Decimal("3000")  # 每分钟更新

        self._running = False
        self._last_scan_opps: list = []
        self._last_scan_at: str | None = None
        self._wallet_balance_usd: Decimal = Decimal("0")

    async def run_forever(self) -> None:
        self._running = True
        logger.info("cex_dex_runner_started", mode=self._cfg.execution_mode)
        notify_system(f"CEX-DEX runner 启动 — 模式: {self._cfg.execution_mode}")

        eth_refresh = 0
        while self._running:
            try:
                # 每 60 次 tick (~5min) 刷新 ETH 价格用于 gas 估算
                if eth_refresh % 60 == 0:
                    await self._refresh_eth_price()
                    await self._refresh_wallet_balance()
                eth_refresh += 1

                self._reset_daily_loss_if_new_day()
                opps = await self._scanner.scan()
                self._last_scan_at = datetime.now(UTC).isoformat()
                self._last_scan_opps = opps

                if opps:
                    await self._publish(opps)
                    for opp in opps:
                        await self._handle_opportunity(opp)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("cex_dex_runner_tick_error")

            await asyncio.sleep(self._cfg.scan_interval_seconds)

        logger.info("cex_dex_runner_stopped")

    def stop(self) -> None:
        self._running = False

    async def _handle_opportunity(self, opp: CexDexOpportunity) -> None:
        msg = (
            f"💹 CEX-DEX 机会\n"
            f"标的: {opp.pair}  方向: {opp.direction}\n"
            f"价差: {opp.raw_spread_bps:.1f} bps  净利: ${opp.net_profit_usd:.2f}\n"
            f"CEX: {opp.cex_price:.4f}  DEX: {opp.dex_price:.4f}\n"
            f"Gas: ${opp.estimated_gas_usd:.3f}  模式: {self._cfg.execution_mode}"
        )
        notify_system(msg)

        if self._cfg.execution_mode != "live":
            logger.info("cex_dex_paper_opportunity", pair=opp.pair, direction=opp.direction,
                        net_profit=float(opp.net_profit_usd))
            return

        await self._execute(opp)

    async def _execute(self, opp: CexDexOpportunity) -> None:
        if self._dex_exec is None:
            logger.warning("dex_executor_not_configured")
            return

        if self._open_trades >= self._cfg.max_open_trades:
            logger.info("cex_dex_max_open_trades_reached", open=self._open_trades)
            return

        if self._daily_loss >= self._cfg.max_daily_loss_usd:
            logger.warning("cex_dex_daily_loss_breaker_triggered", loss=float(self._daily_loss))
            return

        gas_ok, current_gwei = await check_gas_ok(self._w3, self._cfg.max_gas_gwei)
        if not gas_ok:
            return

        # 二次报价确认（防止价格在扫描后失效）
        fresh_opps = await self._scanner.scan()
        fresh = next((o for o in fresh_opps if o.pair == opp.pair and o.direction == opp.direction), None)
        if fresh is None or fresh.net_profit_usd < self._cfg.min_net_profit_usd:
            logger.info("cex_dex_opportunity_stale", pair=opp.pair)
            return

        opp = fresh  # 使用刷新后的数据
        self._open_trades += 1

        try:
            if opp.direction == "cex_cheap":
                # 买 CEX + 卖 DEX（base→quote）
                cex_task = self._cex_exec.market_buy(opp.pair, opp.trade_usd, opp.cex_price)
                dex_task = self._dex_exec.swap(
                    token_in_key="WETH", token_out_key="USDT",
                    amount_in_usd=opp.trade_usd, expected_out_usd=opp.dex_price * opp.trade_usd / opp.cex_price,
                    pool_fee=opp.pool_fee, eth_usd_price=self._eth_usd,
                )
            else:  # dex_cheap
                # 买 DEX（quote→base）+ 卖 CEX
                cex_task = self._cex_exec.market_sell(opp.pair, opp.trade_usd, opp.cex_price)
                dex_task = self._dex_exec.swap(
                    token_in_key="USDT", token_out_key="WETH",
                    amount_in_usd=opp.trade_usd, expected_out_usd=opp.trade_usd / opp.dex_price,
                    pool_fee=opp.pool_fee, eth_usd_price=self._eth_usd,
                )

            cex_result, dex_result = await asyncio.gather(cex_task, dex_task, return_exceptions=True)

            cex_ok = not isinstance(cex_result, Exception) and cex_result.success  # type: ignore[union-attr]
            dex_ok = not isinstance(dex_result, Exception) and dex_result.success  # type: ignore[union-attr]

            if cex_ok and dex_ok:
                net = opp.net_profit_usd - (dex_result.gas_used_usd if not isinstance(dex_result, Exception) else Decimal("0"))  # type: ignore[union-attr]
                logger.info("cex_dex_trade_success", pair=opp.pair, direction=opp.direction,
                            net_profit=float(net))
                notify_system(f"✅ CEX-DEX 成交 {opp.pair} 净利 ${net:.2f}")
                if net < 0:
                    self._daily_loss += abs(net)
            else:
                logger.error("cex_dex_leg_failed", cex_ok=cex_ok, dex_ok=dex_ok, pair=opp.pair)
                notify_system(f"🚨 CEX-DEX 腿失败 {opp.pair} cex={cex_ok} dex={dex_ok}")
                # 腿失败时记录损失（保守估计）
                self._daily_loss += opp.trade_usd * Decimal("0.002")

        finally:
            self._open_trades -= 1

    async def _refresh_eth_price(self) -> None:
        try:
            ticker = await self._scanner._cex.fetch_ticker(
                __import__("app.exchanges.models", fromlist=["Symbol"]).Symbol(base="ETH", quote="USDT"),
                __import__("app.exchanges.models", fromlist=["InstrumentType"]).InstrumentType.SPOT,
            )
            self._eth_usd = ticker.last
            self._scanner._eth_usd = ticker.last
        except Exception:
            pass  # 保持旧值

    async def _refresh_wallet_balance(self) -> None:
        if self._dex_exec is None:
            return
        try:
            from web3 import AsyncWeb3  # noqa: PLC0415
            from .config import TOKENS  # noqa: PLC0415
            wallet = self._dex_exec._account
            total = Decimal("0")

            # Native ETH
            eth_wei = await self._w3.eth.get_balance(wallet)
            eth_amount = Decimal(str(eth_wei)) / Decimal("1e18")
            total += eth_amount * self._eth_usd

            # ERC20 stablecoins + WETH
            _bal_abi = [{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"}]
            for tok_key, info in TOKENS.items():
                contract = self._w3.eth.contract(
                    address=AsyncWeb3.to_checksum_address(info["address"]),
                    abi=_bal_abi,
                )
                raw = await contract.functions.balanceOf(wallet).call()
                amount = Decimal(str(raw)) / Decimal(str(10 ** info["decimals"]))
                if tok_key in ("USDT", "USDC"):
                    total += amount
                elif tok_key == "WETH":
                    total += amount * self._eth_usd

            self._wallet_balance_usd = total
            logger.debug("cex_dex_wallet_balance_refreshed", total_usd=float(total))
        except Exception:
            pass  # 保持旧值

    def _reset_daily_loss_if_new_day(self) -> None:
        today = date.today()
        if today != self._last_loss_date:
            self._daily_loss = Decimal("0")
            self._last_loss_date = today

    async def _publish(self, opps: list[CexDexOpportunity]) -> None:
        payload = json.dumps({
            "scanned_at": datetime.now(UTC).isoformat(),
            "mode": self._cfg.execution_mode,
            "opportunities": [
                {
                    "pair": o.pair,
                    "direction": o.direction,
                    "cex_price": str(o.cex_price),
                    "dex_price": str(o.dex_price),
                    "raw_spread_bps": str(o.raw_spread_bps),
                    "estimated_gas_usd": str(o.estimated_gas_usd),
                    "net_profit_usd": str(o.net_profit_usd),
                    "trade_usd": str(o.trade_usd),
                }
                for o in opps
            ],
        })
        try:
            await publish(OPPORTUNITIES_CHANNEL, payload)
        except Exception:
            logger.warning("cex_dex_publish_failed", exc_info=True)
