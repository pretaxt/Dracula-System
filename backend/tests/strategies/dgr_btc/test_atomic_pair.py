"""Tests for atomic_pair / pretrade_check / reconciliation (Phase E.1)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.strategies.dgr_btc.atomic_pair import (
    AtomicPairExecutor,
    PairExecutionMode,
    PairOutcome,
    pair_intents,
)
from app.strategies.dgr_btc.pretrade_check import PretradeChecker
from app.strategies.dgr_btc.reconciliation import ReconcileTask
from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
from app.strategies.dgr_btc.strategy_core import DgrBtcStrategy, OrderIntent
from app.strategies.dgr_btc.types import MarketState, MarketType, Side


def _make_market(spot: float = 76000.0, perp: float = 76000.0) -> MarketState:
    return MarketState(
        timestamp=datetime(2026, 5, 23, 20, 30, tzinfo=timezone.utc),
        spot_price=Decimal(str(spot)),
        perp_price=Decimal(str(perp)),
        funding_rate=Decimal("0.0001"),
        funding_settled=False,
    )


def _make_pair(
    grid_level: float = 76000.0,
    qty: float = 0.002,
    spot_side: Side = Side.SELL,
    perp_side: Side = Side.SELL,
) -> tuple[OrderIntent, OrderIntent]:
    spot = OrderIntent(
        market=MarketType.SPOT,
        side=spot_side,
        price=Decimal(str(grid_level)),
        quantity=Decimal(str(qty)),
        grid_level=Decimal(str(grid_level)),
        reason=f"grid_up_{grid_level}",
    )
    perp = OrderIntent(
        market=MarketType.PERP,
        side=perp_side,
        price=Decimal(str(grid_level)),
        quantity=Decimal(str(qty)),
        grid_level=Decimal(str(grid_level)),
        reason=f"add_short_{grid_level}",
    )
    return spot, perp


@pytest.fixture
def cfg() -> DgrBtcStrategyConfig:
    return DgrBtcStrategyConfig.from_yaml({})


@pytest.fixture
def strategy(cfg: DgrBtcStrategyConfig) -> DgrBtcStrategy:
    s = DgrBtcStrategy(cfg, start_price=Decimal("76000"))
    s.cash = Decimal("10000")
    s.spot_pos.quantity = Decimal("0.05")
    s.perp_pos.quantity = Decimal("-0.05")
    return s


# ============================================================
# AtomicPairExecutor — PAPER mode
# ============================================================

class TestAtomicPairPaperMode:
    @pytest.mark.asyncio
    async def test_both_legs_fill_when_no_failure(self) -> None:
        executor = AtomicPairExecutor(
            mode=PairExecutionMode.PAPER,
            single_leg_failure_rate=0.0,
        )
        spot, perp = _make_pair()
        market = _make_market()
        result = await executor.execute_pair(spot, perp, market)
        assert result.outcome == PairOutcome.BOTH_FILLED
        assert len(result.trades) == 2
        assert result.unwind_trade is None
        assert executor.n_both_filled == 1

    @pytest.mark.asyncio
    async def test_unwind_triggered_when_failure_rate_100(self, monkeypatch) -> None:
        import random as _r
        seq = iter([0.99, 0.5])
        monkeypatch.setattr(_r, "random", lambda: next(seq))
        executor = AtomicPairExecutor(
            mode=PairExecutionMode.PAPER,
            single_leg_failure_rate=0.7,
        )
        spot, perp = _make_pair()
        market = _make_market()
        result = await executor.execute_pair(spot, perp, market)
        assert result.outcome == PairOutcome.SPOT_ONLY_UNWOUND
        assert len(result.trades) == 1
        assert result.trades[0].market == MarketType.SPOT
        assert result.unwind_trade is not None
        assert result.unwind_trade.market == MarketType.SPOT
        assert result.unwind_trade.side == Side.BUY
        assert executor.n_single_leg_unwound == 1

    @pytest.mark.asyncio
    async def test_both_failed_when_high_failure_rate(self, monkeypatch) -> None:
        import random as _r
        monkeypatch.setattr(_r, "random", lambda: 0.99)
        executor = AtomicPairExecutor(
            mode=PairExecutionMode.PAPER,
            single_leg_failure_rate=1.0,
        )
        spot, perp = _make_pair()
        market = _make_market()
        result = await executor.execute_pair(spot, perp, market)
        assert result.outcome == PairOutcome.BOTH_FAILED
        assert len(result.trades) == 0


# ============================================================
# PretradeChecker
# ============================================================

class TestPretradeChecker:
    def test_validate_passes_normal_pair(self, strategy: DgrBtcStrategy) -> None:
        checker = PretradeChecker(strategy)
        spot, perp = _make_pair(grid_level=76000.0)
        market = _make_market(spot=76000.0, perp=76000.0)
        ok, reason = checker.validate(spot, perp, market)
        assert ok, f"unexpected reject: {reason}"
        assert reason is None

    def test_validate_rejects_price_deviation(self, strategy: DgrBtcStrategy) -> None:
        checker = PretradeChecker(strategy, max_price_deviation_pct=Decimal("0.005"))
        spot, perp = _make_pair(grid_level=76000.0)
        market = _make_market(spot=80000.0, perp=80000.0)
        ok, reason = checker.validate(spot, perp, market)
        assert not ok
        assert reason is not None and "price_dev" in reason

    def test_validate_rejects_spot_cap_breach(self, cfg, strategy: DgrBtcStrategy) -> None:
        strategy.spot_pos.quantity = cfg.max_spot_btc
        checker = PretradeChecker(strategy)
        spot, perp = _make_pair(spot_side=Side.BUY, perp_side=Side.BUY)
        market = _make_market()
        ok, reason = checker.validate(spot, perp, market)
        assert not ok
        assert reason is not None and "spot_cap_breach" in reason

    def test_validate_rejects_pair_mismatch(self, strategy: DgrBtcStrategy) -> None:
        checker = PretradeChecker(strategy)
        spot, perp = _make_pair()
        perp.grid_level = Decimal("75999")
        market = _make_market()
        ok, reason = checker.validate(spot, perp, market)
        assert not ok
        assert reason is not None and "pair_mismatch" in reason

    def test_validate_allows_low_cash_with_warn(self, strategy: DgrBtcStrategy) -> None:
        # Phase E (relaxed): cash 不足不 SKIP, 只 WARN; 让 broker reject + unwind 兜底
        strategy.cash = Decimal("10")
        checker = PretradeChecker(strategy)
        spot, perp = _make_pair(spot_side=Side.BUY, perp_side=Side.BUY)
        market = _make_market()
        ok, reason = checker.validate(spot, perp, market)
        assert ok is True
        assert reason is None


# ============================================================
# AtomicPairExecutor + PretradeChecker 集成
# ============================================================

class TestAtomicWithPretrade:
    @pytest.mark.asyncio
    async def test_pretrade_reject_skips_execution(self, cfg, strategy: DgrBtcStrategy) -> None:
        # 用 spot cap breach 触 reject (cash check 已改为 WARN-only)
        strategy.spot_pos.quantity = cfg.max_spot_btc
        checker = PretradeChecker(strategy)
        executor = AtomicPairExecutor(
            mode=PairExecutionMode.PAPER,
            pretrade_checker=checker,
        )
        spot, perp = _make_pair(spot_side=Side.BUY, perp_side=Side.BUY)
        market = _make_market()
        result = await executor.execute_pair(spot, perp, market)
        assert result.outcome == PairOutcome.PRETRADE_REJECTED
        assert result.reject_reason is not None
        assert "spot_cap_breach" in result.reject_reason
        assert executor.n_pretrade_rejected == 1
        assert executor.n_both_filled == 0


# ============================================================
# pair_intents helper
# ============================================================

class TestPairIntents:
    def test_groups_spot_perp_by_grid_level(self) -> None:
        spot1, perp1 = _make_pair(grid_level=76000.0)
        spot2, perp2 = _make_pair(grid_level=76250.0)
        intents = [spot1, perp1, spot2, perp2]
        pairs = pair_intents(intents)
        assert len(pairs) == 2
        for s, p in pairs:
            assert s.market == MarketType.SPOT
            assert p.market == MarketType.PERP
            assert s.grid_level == p.grid_level

    def test_orphan_intent_dropped(self) -> None:
        spot, _ = _make_pair(grid_level=76000.0)
        pairs = pair_intents([spot])
        assert len(pairs) == 0


# ============================================================
# ReconcileTask (PAPER mode self-check)
# ============================================================

class TestReconcileTask:
    @pytest.mark.asyncio
    async def test_paper_mode_no_drift(self, strategy: DgrBtcStrategy) -> None:
        class FakeSession:
            def __init__(self, s): self.strategy = s
        sess = FakeSession(strategy)
        task = ReconcileTask(sess, interval_seconds=0.05, live_mode=False)
        report = await task.tick()
        assert report.ok is True
        assert report.drift_spot == Decimal("0")
        assert report.drift_perp == Decimal("0")
        assert task.consecutive_drift == 0

    @pytest.mark.asyncio
    async def test_live_mode_drift_detected_and_telemetered(
        self, strategy: DgrBtcStrategy
    ) -> None:
        class FakeBroker:
            async def get_spot_balance(self, asset): return 0.04
            async def get_perp_position(self, sym): return -0.05

        class FakeSession:
            def __init__(self, s): self.strategy = s

        sess = FakeSession(strategy)
        task = ReconcileTask(
            sess,
            interval_seconds=0.05,
            drift_tolerance_btc=Decimal("0.001"),
            consecutive_drift_threshold=3,
            live_mode=True,
            broker_adapter=FakeBroker(),
        )
        report = await task.tick()
        assert report.ok is False
        assert report.drift_spot == Decimal("0.01")
        assert task.consecutive_drift == 1

        await task.tick()
        await task.tick()
        assert task.consecutive_drift == 3
        assert task.n_drift_events == 3
