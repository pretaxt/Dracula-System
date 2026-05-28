"""
dgr_btc/live_metrics.py
=======================
LiveMetricsCollector — Phase H.live 关键运行指标 + Telegram 告警 watcher.

监控 3 个 LIVE 模式核心指标:
  1. maker_fill_rate         — post-only 拒单率 (LIVE 主风险)
  2. order_to_fill_latency   — order → fill p95 latency (broker 健康度)
  3. safety_reject_count     — safety guard 拒单次数 (配置 bug 信号)

设计:
  - 滑动窗口 deque(maxlen=200) — 单次重启清零, 无需持久化
  - 异步安全 (broker 是 async, hub coroutines 调用)
  - alert watcher 周期 (默认 60s) 检查阈值, 触发去重 telegram 推送
  - 模块级 registry — API 端点 / watcher 可按 instance_name 查 collector

阈值 (来自 §6.2 #4 审查):
  - maker fill rate < 90% 持续 ≥ 10 min        → 告警
  - latency p95 > 500ms 持续 ≥ 5 min            → 告警
  - safety guard 拒单 > 5 / hour                 → 告警
"""
from __future__ import annotations

import asyncio
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# 滑窗大小: 200 笔单覆盖 ~30-60min 真实活动 (LIVE tick 5s × 多决策)
_WINDOW_SIZE = 200


@dataclass
class MakerOrderRecord:
    """单条 maker 单的结果记录."""
    ts: datetime
    market: str               # "spot" / "perp"
    side: str                 # "BUY" / "SELL"
    outcome: str              # "filled" / "rejected_post_only" / "rejected_other" / "timeout"
    latency_ms: Optional[float]  # order placed → first fill confirmed; None for reject/timeout


@dataclass
class SafetyRejectRecord:
    """safety guard 单次拒单."""
    ts: datetime
    reason: str               # "max_order_usd" / "kill_switch_active" / ...


@dataclass
class _AlertState:
    """单条告警的去重状态."""
    breached_since: Optional[datetime] = None
    last_notified_at: Optional[datetime] = None


class LiveMetricsCollector:
    """LIVE 模式实时指标收集 + 健康度评估.

    使用方:
      - broker_adapter.place_limit_maker → record_maker_outcome(...)
      - live_safety.check_pre_order      → record_safety_reject(reason) (失败时)
      - API GET /strategies/dgr-btc/health → health()
      - alert_watcher 任务 → 周期 check_thresholds() 触发 telegram

    线程模型: 同一 asyncio loop 内的串行访问 (broker / safety 同步调用,
    无跨线程; deque + Counter 在 GIL 下安全, 不需 lock).
    """

    # 阈值 (硬编码; 后续需要调可移到 config)
    MAKER_FILL_RATE_FLOOR = 0.90
    MAKER_FILL_RATE_BREACH_SEC = 600    # 10 min
    LATENCY_P95_CEIL_MS = 500.0
    LATENCY_BREACH_SEC = 300            # 5 min
    SAFETY_REJECT_CEIL_PER_HOUR = 5

    # 告警发出后冷却时间: 同一告警不重复推送
    ALERT_COOLDOWN_SEC = 1800           # 30 min

    def __init__(self, instance_name: str = "dgr_btc") -> None:
        self.instance_name = instance_name
        # 滑窗
        self._maker_records: deque[MakerOrderRecord] = deque(maxlen=_WINDOW_SIZE)
        self._safety_records: deque[SafetyRejectRecord] = deque(maxlen=_WINDOW_SIZE)
        # 累计统计 (永不清零, 重启清零)
        self._n_maker_placed = 0
        self._n_maker_filled = 0
        self._n_maker_post_only_rejected = 0
        self._n_maker_other_rejected = 0
        self._n_maker_timeout = 0
        self._n_safety_reject = 0
        # unwind (taker) — 不影响 fill rate, 单独 latency 追踪
        self._n_unwind = 0
        self._unwind_latencies: deque[float] = deque(maxlen=_WINDOW_SIZE)
        # 告警状态
        self._alert_states: dict[str, _AlertState] = {
            "maker_fill_rate": _AlertState(),
            "latency_p95": _AlertState(),
            "safety_reject_rate": _AlertState(),
        }
        self._created_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 记录接口 (broker / safety 调用)
    # ------------------------------------------------------------------

    def record_maker_outcome(
        self,
        market: str,
        side: str,
        outcome: str,
        latency_ms: Optional[float] = None,
    ) -> None:
        """记录一笔 maker 单的最终结果.

        outcome 取值:
          - "filled"               — 成交 (含 partial)
          - "rejected_post_only"   — binance post-only crossed (-2010/-1013)
          - "rejected_other"       — 其他 binance 错误 (鉴权/资金/网络)
          - "timeout"              — 5s 内未 fill, cancel 路径走完
        """
        rec = MakerOrderRecord(
            ts=datetime.now(timezone.utc),
            market=market,
            side=side,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        self._maker_records.append(rec)
        self._n_maker_placed += 1
        if outcome == "filled":
            self._n_maker_filled += 1
        elif outcome == "rejected_post_only":
            self._n_maker_post_only_rejected += 1
        elif outcome == "rejected_other":
            self._n_maker_other_rejected += 1
        elif outcome == "timeout":
            self._n_maker_timeout += 1

    def record_unwind_outcome(
        self,
        market: str,
        side: str,
        latency_ms: float,
    ) -> None:
        """记录 market unwind (taker) 完成. 仅做 latency 追踪 — 不影响 maker fill rate."""
        self._n_unwind += 1
        if latency_ms is not None:
            self._unwind_latencies.append(latency_ms)

    def record_safety_reject(self, reason: str) -> None:
        """记录一次 safety guard 拒单 (reason 来自 LiveSafetyGuard.check_pre_order)."""
        self._safety_records.append(SafetyRejectRecord(
            ts=datetime.now(timezone.utc),
            reason=reason,
        ))
        self._n_safety_reject += 1

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    def maker_fill_rate(self, window_sec: int = 600) -> Optional[float]:
        """maker fill 成功率 (filled / placed) over last window_sec.

        返回 None 表示样本不足 (< 5 笔) — 调用方应忽略,
        避免冷启动期误告警.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
        recent = [r for r in self._maker_records if r.ts >= cutoff]
        if len(recent) < 5:
            return None
        filled = sum(1 for r in recent if r.outcome == "filled")
        return filled / len(recent)

    def latency_p95_ms(self, window_sec: int = 300) -> Optional[float]:
        """order → fill p95 latency over last window_sec.

        仅统计 filled 笔次. 返回 None 表示样本不足 (< 3 笔).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_sec)
        latencies = [
            r.latency_ms for r in self._maker_records
            if r.ts >= cutoff and r.outcome == "filled" and r.latency_ms is not None
        ]
        if len(latencies) < 3:
            return None
        latencies.sort()
        # p95 index: ceil(len * 0.95) - 1
        idx = max(0, int(len(latencies) * 0.95) - 1)
        if idx >= len(latencies):
            idx = len(latencies) - 1
        return latencies[idx]

    def safety_reject_rate_per_hour(self) -> int:
        """safety reject count over last 1 hour (整型计数)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        return sum(1 for r in self._safety_records if r.ts >= cutoff)

    def safety_reject_breakdown_1h(self) -> dict[str, int]:
        """按 reason 拆分最近 1h 的 safety reject 计数."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        c: Counter[str] = Counter()
        for r in self._safety_records:
            if r.ts >= cutoff:
                # reason 可能是 "max_order_usd $X > $Y", 取冒号/空格前作 key
                key = r.reason.split(":")[0].split(" ")[0]
                c[key] += 1
        return dict(c)

    # ------------------------------------------------------------------
    # 健康度 / API 输出
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """API GET /strategies/dgr-btc/health 用. 返回 dict (json 友好)."""
        fill_rate = self.maker_fill_rate()
        latency = self.latency_p95_ms()
        safety_per_hour = self.safety_reject_rate_per_hour()

        # 各指标 status: ok / warning / breach / insufficient_data
        def _status_fill() -> str:
            if fill_rate is None:
                return "insufficient_data"
            if fill_rate < self.MAKER_FILL_RATE_FLOOR:
                return "breach"
            if fill_rate < self.MAKER_FILL_RATE_FLOOR + 0.03:  # within 3% of floor
                return "warning"
            return "ok"

        def _status_latency() -> str:
            if latency is None:
                return "insufficient_data"
            if latency > self.LATENCY_P95_CEIL_MS:
                return "breach"
            if latency > self.LATENCY_P95_CEIL_MS * 0.8:
                return "warning"
            return "ok"

        def _status_safety() -> str:
            if safety_per_hour > self.SAFETY_REJECT_CEIL_PER_HOUR:
                return "breach"
            if safety_per_hour > self.SAFETY_REJECT_CEIL_PER_HOUR * 0.6:
                return "warning"
            return "ok"

        return {
            "instance_name": self.instance_name,
            "created_at": self._created_at.isoformat(),
            "now": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "maker_fill_rate": {
                    "value": fill_rate,
                    "window_sec": 600,
                    "floor": self.MAKER_FILL_RATE_FLOOR,
                    "status": _status_fill(),
                },
                "latency_p95_ms": {
                    "value": latency,
                    "window_sec": 300,
                    "ceiling_ms": self.LATENCY_P95_CEIL_MS,
                    "status": _status_latency(),
                },
                "safety_reject_per_hour": {
                    "value": safety_per_hour,
                    "ceiling": self.SAFETY_REJECT_CEIL_PER_HOUR,
                    "breakdown": self.safety_reject_breakdown_1h(),
                    "status": _status_safety(),
                },
            },
            "counters": {
                "n_maker_placed": self._n_maker_placed,
                "n_maker_filled": self._n_maker_filled,
                "n_maker_post_only_rejected": self._n_maker_post_only_rejected,
                "n_maker_other_rejected": self._n_maker_other_rejected,
                "n_maker_timeout": self._n_maker_timeout,
                "n_safety_reject": self._n_safety_reject,
                "n_unwind": self._n_unwind,
                "unwind_latency_avg_ms": (
                    sum(self._unwind_latencies) / len(self._unwind_latencies)
                    if self._unwind_latencies else None
                ),
            },
            "window_size": _WINDOW_SIZE,
        }

    # ------------------------------------------------------------------
    # 告警评估 (watcher 周期调用)
    # ------------------------------------------------------------------

    def check_thresholds(self) -> list[dict]:
        """检查 3 个阈值. 返回需推送的告警列表 (可能为空).

        触发规则:
          - 指标 status == "breach" → 进入 breach 状态; 持续 N 秒未恢复且
            上次告警距今 > ALERT_COOLDOWN_SEC → 触发推送
          - 指标恢复 ok → 清空 breach 状态

        返回列表元素结构:
          {"key": "maker_fill_rate", "level": "alert",
           "message": "...", "value": ..., "threshold": ...}
        """
        out: list[dict] = []
        now = datetime.now(timezone.utc)

        fill_rate = self.maker_fill_rate()
        latency = self.latency_p95_ms()
        safety_per_hour = self.safety_reject_rate_per_hour()

        # 1. maker fill rate
        out.extend(self._eval_threshold(
            key="maker_fill_rate",
            now=now,
            in_breach=(fill_rate is not None and fill_rate < self.MAKER_FILL_RATE_FLOOR),
            breach_required_sec=self.MAKER_FILL_RATE_BREACH_SEC,
            build_msg=lambda: (
                f"⚠️ dgr_btc maker fill rate = {fill_rate * 100:.1f}% "
                f"(< {self.MAKER_FILL_RATE_FLOOR * 100:.0f}% 持续 ≥ "
                f"{self.MAKER_FILL_RATE_BREACH_SEC // 60} min)\n"
                f"含义: post-only 频繁被撞 → 流动性/价格漂移异常\n"
                f"行动: 查 binance 价格 + risk_filter 边界 + ticker delay"
            ),
            value=fill_rate,
            threshold=self.MAKER_FILL_RATE_FLOOR,
        ))

        # 2. latency p95
        out.extend(self._eval_threshold(
            key="latency_p95",
            now=now,
            in_breach=(latency is not None and latency > self.LATENCY_P95_CEIL_MS),
            breach_required_sec=self.LATENCY_BREACH_SEC,
            build_msg=lambda: (
                f"⚠️ dgr_btc broker latency p95 = {latency:.0f}ms "
                f"(> {self.LATENCY_P95_CEIL_MS:.0f}ms 持续 ≥ "
                f"{self.LATENCY_BREACH_SEC // 60} min)\n"
                f"含义: binance API 退化 / 网络抖动\n"
                f"行动: ping binance + 看 dracula → binance route 健康度"
            ),
            value=latency,
            threshold=self.LATENCY_P95_CEIL_MS,
        ))

        # 3. safety reject per hour — 不需"持续 N 秒", 触发即报 (per_hour 是即时窗口)
        out.extend(self._eval_threshold(
            key="safety_reject_rate",
            now=now,
            in_breach=(safety_per_hour > self.SAFETY_REJECT_CEIL_PER_HOUR),
            breach_required_sec=0,
            build_msg=lambda: (
                f"⚠️ dgr_btc safety guard 拒单 {safety_per_hour}/h "
                f"(阈值 {self.SAFETY_REJECT_CEIL_PER_HOUR}/h)\n"
                f"breakdown: {self.safety_reject_breakdown_1h()}\n"
                f"含义: 配置 cap 太紧或调用方参数异常\n"
                f"行动: 查 max_order_usd / daily_notional / kill switch 是否被触发"
            ),
            value=float(safety_per_hour),
            threshold=float(self.SAFETY_REJECT_CEIL_PER_HOUR),
        ))

        return out

    def _eval_threshold(
        self,
        key: str,
        now: datetime,
        in_breach: bool,
        breach_required_sec: int,
        build_msg,
        value,
        threshold,
    ) -> list[dict]:
        """单条阈值评估; 返回 0/1 个告警 dict.

        - in_breach=True + breached_since=None → set breached_since
        - in_breach=True + 持续 >= breach_required_sec + 距上次推送 >= cooldown → 推送
        - in_breach=False → clear breached_since
        """
        state = self._alert_states[key]
        if not in_breach:
            if state.breached_since is not None:
                state.breached_since = None
                logger.info("dgr_btc_metric_recovered", key=key)
            return []

        # in breach
        if state.breached_since is None:
            state.breached_since = now
            # breach_required_sec=0 → 触发即报, 不等下一次 tick
            if breach_required_sec > 0:
                return []

        elapsed = (now - state.breached_since).total_seconds()
        if elapsed < breach_required_sec:
            return []

        # 持续超阈值: 看 cooldown
        if state.last_notified_at is not None:
            since_last = (now - state.last_notified_at).total_seconds()
            if since_last < self.ALERT_COOLDOWN_SEC:
                return []

        state.last_notified_at = now
        return [{
            "key": key,
            "level": "alert",
            "message": build_msg(),
            "value": value,
            "threshold": threshold,
            "breached_since": state.breached_since.isoformat(),
        }]


# ----------------------------------------------------------------------
# Module-level registry — broker_adapter 注册, API / watcher 读取
# ----------------------------------------------------------------------

_active_collectors: dict[str, LiveMetricsCollector] = {}


def register_collector(instance_name: str, collector: LiveMetricsCollector) -> None:
    _active_collectors[instance_name] = collector
    logger.info("dgr_btc_metrics_registered", instance=instance_name)


def unregister_collector(instance_name: str) -> None:
    _active_collectors.pop(instance_name, None)


def get_collector(instance_name: str) -> Optional[LiveMetricsCollector]:
    return _active_collectors.get(instance_name)


def list_collectors() -> dict[str, LiveMetricsCollector]:
    return dict(_active_collectors)


# ----------------------------------------------------------------------
# Telegram alert watcher (后台 task)
# ----------------------------------------------------------------------

async def run_alert_watcher(
    instance_name: str,
    poll_interval_sec: float = 60.0,
    telegram_enabled: bool = True,
) -> None:
    """后台 task: 周期检查阈值, 触发 telegram 推送.

    使用方:
        task = asyncio.create_task(
            run_alert_watcher("dgr_btc", telegram_enabled=cfg.live_safety_telegram_alerts_enabled)
        )

    停机: cancel(task).
    """
    logger.info(
        "dgr_btc_alert_watcher_started",
        instance=instance_name, poll_interval=poll_interval_sec,
    )
    try:
        while True:
            try:
                await asyncio.sleep(poll_interval_sec)
            except asyncio.CancelledError:
                raise

            collector = get_collector(instance_name)
            if collector is None:
                continue

            try:
                alerts = collector.check_thresholds()
            except Exception:
                logger.exception("dgr_btc_alert_watcher_check_failed")
                continue

            if not alerts:
                continue

            if not telegram_enabled:
                # 告警禁用 telegram, 只 log
                for a in alerts:
                    logger.warning(
                        "dgr_btc_alert_suppressed_no_telegram",
                        key=a["key"], message=a["message"][:200],
                    )
                continue

            try:
                from app.notifications.telegram import notify_system  # noqa: PLC0415
                for a in alerts:
                    notify_system(a["message"])
                    logger.warning(
                        "dgr_btc_alert_fired",
                        key=a["key"], value=a.get("value"),
                        threshold=a.get("threshold"),
                    )
            except Exception:
                logger.exception("dgr_btc_alert_watcher_telegram_failed")
    except asyncio.CancelledError:
        logger.info("dgr_btc_alert_watcher_canceled", instance=instance_name)
        raise
