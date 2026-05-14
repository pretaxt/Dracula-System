"""HTX 溢价筛选器执行单元 — 按需运行 + 主动写入 candidate_symbols。

调用方：``POST /api/v1/scanner/htx-premium/run`` 触发 ``run_now()``，
        ``POST /api/v1/scanner/htx-premium/apply`` 调 ``apply_to_runtime()``。

**不**周期循环、**不**自动应用：完全由 API 端点驱动。

保护规则：
  - 应用时自动 union 当前 perp_basis 持仓中的币种 base，防止"撤候选 → scanner
    扫不到 → exit 退出条件评估失败"造成单边敞口。
  - 上限 ``max_symbols`` 防候选过宽（默认 20）。
  - 仅 STRONG（默认）或 STRONG+MODERATE 入选。

幂等：apply_to_runtime() 把结果写到 ``runtime_overrides`` 子节 ``perp_basis.candidate_symbols``，
原子文件替换 + scanner._config.candidate_symbols 同步替换（in-memory mutable list）。
重启容器后从 overrides.json 读回（lifespan 已支持）。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.strategies.perp_basis.htx_screener import (
    HTXScreenResult,
    HTXSymbolResult,
    screen_htx_premium,
)

logger = get_logger(__name__)

_DEFAULT_DAYS = 14
_DEFAULT_MAX_SYMBOLS = 20


class HTXScreenerRunner:
    """HTX screener 执行单元 — 仅按需运行，不周期循环。

    Parameters
    ----------
    adapters / hub : 同 ``screen_htx_premium``。
    days           : 历史窗口（默认 14）。
    """

    def __init__(
        self,
        adapters: dict[str, Any],
        hub: Any,
        days: int = _DEFAULT_DAYS,
    ) -> None:
        self._adapters = adapters
        self._hub = hub
        self._days = int(days)
        self._lock = asyncio.Lock()
        self._last_result: HTXScreenResult | None = None
        self._last_run_at: datetime | None = None
        self._last_elapsed_secs: float = 0.0
        self._last_error: str | None = None
        self._last_applied_at: datetime | None = None
        self._last_applied_symbols: list[str] = []
        self._last_apply_diff: dict[str, list[str]] = {
            "added": [], "removed": [], "kept": [],
        }
        self._is_running: bool = False

    # -- read-only state ---------------------------------------------------

    @property
    def days(self) -> int:
        return self._days

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def last_result(self) -> HTXScreenResult | None:
        return self._last_result

    @property
    def last_run_at(self) -> datetime | None:
        return self._last_run_at

    @property
    def last_elapsed_secs(self) -> float:
        return self._last_elapsed_secs

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def last_applied_at(self) -> datetime | None:
        return self._last_applied_at

    @property
    def last_applied_symbols(self) -> list[str]:
        return list(self._last_applied_symbols)

    @property
    def last_apply_diff(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._last_apply_diff.items()}

    # -- run ---------------------------------------------------------------

    async def run_now(self, days: int | None = None) -> HTXScreenResult:
        """触发一次 screener 运行。串行（lock 防并发重复跑）。"""
        async with self._lock:
            self._is_running = True
            t0 = time.monotonic()
            try:
                eff_days = int(days) if days else self._days
                result = await screen_htx_premium(
                    adapters=self._adapters,
                    hub=self._hub,
                    days=eff_days,
                    min_persistence_pct=0.0,
                    min_avg_diff_apr=0.0,
                    limit=200,
                )
                self._last_elapsed_secs = round(time.monotonic() - t0, 2)
                self._last_result = result
                self._last_run_at = datetime.now(timezone.utc)
                self._last_error = None
                # 写入 scanner API 缓存
                try:
                    from app.api.v1 import scanner as scanner_api  # noqa: PLC0415
                    scanner_api.warm_cache(
                        days=eff_days,
                        result=result,
                        elapsed=self._last_elapsed_secs,
                    )
                except Exception:
                    logger.exception("htx_screener_runner_cache_write_failed")
                logger.info(
                    "htx_screener_run_done",
                    elapsed_secs=self._last_elapsed_secs,
                    symbols_screened=result.symbols_screened,
                    qualified=len(result.results),
                    days=eff_days,
                )
                return result
            except Exception as e:
                self._last_error = f"{type(e).__name__}: {e}"
                logger.exception("htx_screener_run_failed")
                raise
            finally:
                self._is_running = False

    # -- apply -------------------------------------------------------------

    def select_candidates(
        self,
        tier: str = "STRONG",
        max_symbols: int = _DEFAULT_MAX_SYMBOLS,
        protect_symbols: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """从 last_result 挑出候选 base 列表。

        Returns
        -------
        (final_list, screener_picked)
          final_list      : 写入 candidate_symbols 的最终列表（含 protect union）
          screener_picked : screener 选出的（不含 protect）

        Raises
        ------
        ValueError 没跑过 screener 或 tier 非法。
        """
        if self._last_result is None:
            raise ValueError("尚未运行 screener；先调 run_now()")
        tier = tier.upper()
        if tier not in {"STRONG", "MODERATE", "STRONG+MODERATE"}:
            raise ValueError(f"非法 tier: {tier}")

        accept_strong = "STRONG" in tier
        accept_moderate = "MODERATE" in tier

        picked: list[str] = []
        for r in self._last_result.results:
            rec = r.recommendation
            ok = (accept_strong and rec == "STRONG") or (accept_moderate and rec == "MODERATE")
            if not ok:
                continue
            base = r.symbol.split("/")[0]
            if base not in picked:
                picked.append(base)
            if len(picked) >= max_symbols:
                break

        screener_picked = list(picked)
        # union protect（持仓中币种）— 保留位置：放最前
        protect_set = {s.upper() for s in (protect_symbols or [])}
        if protect_set:
            kept_protect = [s for s in protect_set if s not in set(picked)]
            picked = sorted(kept_protect) + picked

        return picked, screener_picked

    async def apply_to_runtime(
        self,
        scanner: Any,
        tier: str = "STRONG",
        max_symbols: int = _DEFAULT_MAX_SYMBOLS,
        protect_symbols: list[str] | None = None,
        mode: str = "replace",
    ) -> dict[str, Any]:
        """把筛选结果应用到运行时（perp_basis scanner + overrides.json）。

        Parameters
        ----------
        scanner         : ``PerpBasisScanner`` 实例（来自 app.state.perp_basis_runner._scanner）。
                          直接修改其 ``_config.candidate_symbols`` 让下个 scan tick 生效。
        tier            : "STRONG" / "MODERATE" / "STRONG+MODERATE"
        max_symbols     : 候选上限（含 protect_symbols）
        protect_symbols : 强制保留的 base 列表（通常是 open positions 的币种）

        Returns
        -------
        diff dict: {added, removed, kept, applied, screener_picked, protect}
        """
        # 先拿 screener 出的纯列表（不带 protect union，由我们手动控制）
        _, screener_picked = self.select_candidates(
            tier=tier, max_symbols=max_symbols, protect_symbols=None,
        )
        protect_set = [s.upper() for s in (protect_symbols or [])]
        prev_list = list(getattr(scanner._config, "candidate_symbols", []) or [])

        mode = mode.lower()
        if mode not in {"replace", "union"}:
            raise ValueError(f"非法 mode: {mode}（replace | union）")

        if mode == "union":
            # 现有 + screener + protect，去重保序，截 max_symbols
            seen: set[str] = set()
            final_list: list[str] = []
            for s in prev_list + list(screener_picked) + list(protect_set):
                u = s.upper()
                if u in seen:
                    continue
                seen.add(u)
                final_list.append(u)
                if len(final_list) >= max_symbols:
                    break
        else:
            # replace: protect 放最前 + screener_picked，截 max_symbols
            seen = set()
            final_list = []
            for s in list(protect_set) + list(screener_picked):
                u = s.upper()
                if u in seen:
                    continue
                seen.add(u)
                final_list.append(u)
                if len(final_list) >= max_symbols:
                    break

        prev_set = set(prev_list)
        new_set = set(final_list)
        added = sorted(new_set - prev_set)
        removed = sorted(prev_set - new_set)
        kept = sorted(prev_set & new_set)

        # 1) in-memory live swap
        scanner._config.candidate_symbols = list(final_list)

        # 2) persist to overrides.json
        try:
            from app.services.runtime_overrides import save_perp_basis_overrides  # noqa: PLC0415
            save_perp_basis_overrides({"candidate_symbols": list(final_list)})
        except Exception:
            logger.exception("htx_screener_apply_persist_failed")

        self._last_applied_at = datetime.now(timezone.utc)
        self._last_applied_symbols = list(final_list)
        self._last_apply_diff = {"added": added, "removed": removed, "kept": kept}

        logger.info(
            "htx_screener_apply_done",
            tier=tier,
            mode=mode,
            applied_count=len(final_list),
            added=added,
            removed=removed,
            kept_count=len(kept),
            protect=sorted(protect_symbols or []),
        )
        return {
            "mode": mode,
            "applied": final_list,
            "screener_picked": list(screener_picked),
            "protect": sorted(protect_set),
            "added": added,
            "removed": removed,
            "kept": kept,
        }
