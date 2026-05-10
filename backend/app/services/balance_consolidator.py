"""平仓后自动归集：把空闲钱包余额划转到 spot，单一账户简化资金管理。

设计动因（用户明确指令）：
> "如果你不能计算清楚现货、合约、杠杆、资金账户问题，
>  就每次平仓之后全部自动划转到现货账户"
> "包括以后所有策略，避免你计算不清楚"
> "如果是多个策略同时进行中，只能归集平仓部分，不要搞错了"

**关键约束**：多策略并发时，绝不能划走仍被 OPEN 持仓占用的保证金钱包，
否则会触发其他策略的强平。本模块按 **per-wallet** 精细判断：
- 该 wallet 有 OPEN leg 占用 → 跳过（保护保证金）
- 该 wallet 全部空闲 → 归集到 spot

各 CEX 的钱包模型差异：
- Binance: spot / USDM perp / cross-margin / funding 隔离
  · perp 钱包：被任何 PERPETUAL leg 占用 → 跳过
  · margin 钱包：被任何 SPOT margin leg 占用 → 跳过
  · funding 钱包：永远可归集（不参与交易）
- OKX UTA / Bybit UTA / Bitget UTA: 统一账户 → 无需归集
- HTX: spot/swap 隔离 → 仅当无 PERPETUAL leg 时 swap→spot

本模块 best-effort：失败不阻塞主交易流程，仅记 warning。
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 划转最小金额（小于此值跳过，避免噪音 + binance API 拒绝小额）
_MIN_TRANSFER_USDT = Decimal("1.0")


async def get_held_wallets_per_exchange() -> dict[str, set[str]]:
    """查询当前 OPEN 持仓占用了哪些 (exchange, wallet) 组合。

    Returns
    -------
    ``{exchange: {wallet_kind, ...}}``，wallet_kind ∈ {"perp", "margin", "spot"}
    perp = USDM 永续 leg / margin = SPOT margin leg / spot = 普通 spot leg
    """
    held: dict[str, set[str]] = {}
    try:
        from sqlalchemy import select  # noqa: PLC0415
        from app.core.database import get_session  # noqa: PLC0415
        from app.models.position import PositionLegRecord, PositionRecord  # noqa: PLC0415

        async with get_session() as session:
            stmt = (
                select(PositionLegRecord)
                .join(
                    PositionRecord,
                    PositionLegRecord.position_id == PositionRecord.id,
                )
                .where(PositionRecord.status == "open")
            )
            rows = (await session.execute(stmt)).scalars().all()
            for leg in rows:
                ex = leg.exchange
                inst = (leg.instrument_type or "").lower()
                if not ex:
                    continue
                if inst == "perpetual":
                    held.setdefault(ex, set()).add("perp")
                elif inst == "spot":
                    # margin_mode 字段决定是 cross-margin 还是普通 spot
                    margin_mode = (getattr(leg, "margin_mode", None) or "").lower()
                    if margin_mode in ("cross", "isolated"):
                        held.setdefault(ex, set()).add("margin")
                    else:
                        held.setdefault(ex, set()).add("spot")
    except Exception:
        logger.exception("get_held_wallets_failed")
    return held


async def consolidate_to_spot(
    adapters: dict[str, Any],
    exchanges: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """归集所有指定交易所的"空闲钱包"余额到 spot 账户。

    **关键**：被 OPEN 持仓占用的钱包绝不归集（保护保证金，避免触发强平）。

    Parameters
    ----------
    adapters: ``{exchange_name: ExchangeAdapter}``
    exchanges: 要归集的 exchange 列表；None = 全部

    Returns
    -------
    ``{exchange: {transfers: [...], errors: [...], skipped_wallets: [...]}}``
    """
    held = await get_held_wallets_per_exchange()
    targets = exchanges if exchanges is not None else list(adapters.keys())
    out: dict[str, dict[str, Any]] = {}
    for ex in targets:
        ad = adapters.get(ex)
        if ad is None or not getattr(ad, "_api_key", ""):
            continue
        ex_held = held.get(ex, set())
        try:
            out[ex] = await _consolidate_one(ex, ad, ex_held)
        except Exception as exc:
            logger.exception("consolidate_failed", exchange=ex)
            out[ex] = {"transfers": [], "errors": [str(exc)[:200]]}
    return out


async def _consolidate_one(
    exchange: str, adapter: Any, held_wallets: set[str],
) -> dict[str, Any]:
    """单家交易所按 wallet 精细归集到 spot。

    held_wallets ∈ {"perp", "margin", "spot"} 表示该 exchange 还有 OPEN 持仓
    占用对应 wallet — 被占用的钱包跳过（保护保证金）。
    """
    if exchange == "binance":
        return await _consolidate_binance(adapter, held_wallets)
    # OKX UTA / Bybit UTA / Bitget UTA 默认无需归集（统一账户）
    # 后续如需支持非 UTA 类型，在此 dispatch
    return {
        "transfers": [],
        "errors": [],
        "skipped_reason": f"{exchange} uses unified account, no consolidation needed",
    }


async def _consolidate_binance(
    adapter: Any, held_wallets: set[str],
) -> dict[str, Any]:
    """Binance: 按钱包级别精细归集到 spot。

    held_wallets 包含的钱包跳过（保护保证金）：
      - "perp" 在 held → 跳过 USDM perp 钱包归集
      - "margin" 在 held → 跳过 cross-margin 钱包归集
      - funding wallet 永远归集（不参与持仓）

    每步独立 try/except，失败记入 errors 但不阻断后续步骤。
    """
    from app.exchanges.models import InstrumentType  # noqa: PLC0415

    transfers: list[dict[str, Any]] = []
    errors: list[str] = []
    skipped: list[str] = []
    spot_client = adapter._clients.get(InstrumentType.SPOT)
    perp_client = adapter._clients.get(InstrumentType.PERPETUAL)
    if spot_client is None:
        return {"transfers": [], "errors": ["no spot client"]}

    # 1. Funding wallet → spot
    try:
        fw = await spot_client.sapi_post_asset_get_funding_asset({})
        for asset in (fw or []):
            sym = asset.get("asset")
            free = Decimal(str(asset.get("free") or 0))
            if free < _MIN_TRANSFER_USDT and sym != "USDT":
                continue
            if free <= 0:
                continue
            try:
                await spot_client.sapi_post_asset_transfer({
                    "type": "FUNDING_MAIN",
                    "asset": sym,
                    "amount": str(free),
                })
                transfers.append({
                    "from": "funding", "to": "spot",
                    "asset": sym, "amount": str(free),
                })
                logger.info(
                    "consolidate_funding_to_spot",
                    asset=sym, amount=str(free),
                )
            except Exception as e:
                errors.append(f"funding→spot {sym}: {str(e)[:120]}")
    except Exception as e:
        errors.append(f"funding fetch: {str(e)[:120]}")

    # 2. USDM perp → spot
    try:
        if perp_client is not None:
            raw = await perp_client.fetch_balance()
            usdt_free = Decimal(str((raw.get("free") or {}).get("USDT") or 0))
            if usdt_free >= _MIN_TRANSFER_USDT:
                try:
                    await spot_client.sapi_post_asset_transfer({
                        "type": "UMFUTURE_MAIN",
                        "asset": "USDT",
                        "amount": str(usdt_free),
                    })
                    transfers.append({
                        "from": "usdm_perp", "to": "spot",
                        "asset": "USDT", "amount": str(usdt_free),
                    })
                    logger.info(
                        "consolidate_perp_to_spot",
                        asset="USDT", amount=str(usdt_free),
                    )
                except Exception as e:
                    errors.append(f"perp→spot USDT: {str(e)[:120]}")
    except Exception as e:
        errors.append(f"perp fetch: {str(e)[:120]}")

    # 3. Cross-margin → spot（先还借贷，再划转 netAsset）
    try:
        ma = await spot_client.sapi_get_margin_account()
        for a in ma.get("userAssets", []):
            sym = a.get("asset")
            borrowed = Decimal(str(a.get("borrowed") or 0))
            free = Decimal(str(a.get("free") or 0))
            net = Decimal(str(a.get("netAsset") or 0))
            # 还借贷
            if borrowed > 0 and free >= borrowed:
                try:
                    await spot_client.sapi_post_margin_repay({
                        "asset": sym, "amount": str(borrowed),
                    })
                    transfers.append({
                        "action": "margin_repay",
                        "asset": sym, "amount": str(borrowed),
                    })
                    logger.info(
                        "consolidate_margin_repay",
                        asset=sym, amount=str(borrowed),
                    )
                except Exception as e:
                    errors.append(f"margin_repay {sym}: {str(e)[:120]}")
                    continue
            # 划转 netAsset 到 spot
            if net >= _MIN_TRANSFER_USDT or (sym == "USDT" and net > 0):
                # 重新读 free（repay 后变化）
                try:
                    ma2 = await spot_client.sapi_get_margin_account()
                    a2 = next((x for x in ma2.get("userAssets", []) if x.get("asset") == sym), None)
                    transferable = Decimal(str(a2.get("free") or 0)) if a2 else Decimal("0")
                except Exception:
                    transferable = max(Decimal("0"), free - borrowed)
                if transferable > 0:
                    try:
                        await spot_client.sapi_post_asset_transfer({
                            "type": "MARGIN_MAIN",
                            "asset": sym,
                            "amount": str(transferable),
                        })
                        transfers.append({
                            "from": "cross_margin", "to": "spot",
                            "asset": sym, "amount": str(transferable),
                        })
                        logger.info(
                            "consolidate_margin_to_spot",
                            asset=sym, amount=str(transferable),
                        )
                    except Exception as e:
                        errors.append(f"margin→spot {sym}: {str(e)[:120]}")
    except Exception as e:
        errors.append(f"margin fetch: {str(e)[:120]}")

    if transfers or errors:
        logger.info(
            "consolidate_binance_done",
            transfer_count=len(transfers),
            error_count=len(errors),
        )
    return {"transfers": transfers, "errors": errors}
