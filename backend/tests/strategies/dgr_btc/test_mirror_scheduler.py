"""mirror_scheduler 单测 — codex review follow-up #1 验证连续失败 raise 行为.

覆盖:
  - 单次失败 swallow (不抛, 等下一天)
  - 连续 3 次失败 raise (让 supervisor 接管)
  - 成功后 consecutive_failures 重置
  - module load 失败立即抛 (supervisor backoff 起作用)
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_mirror_module():
    """构造一个 fake mirror_check module, main() 可控制成功/失败."""
    mod = SimpleNamespace()
    mod.main = AsyncMock(return_value=0)
    return mod


@pytest.mark.asyncio
async def test_single_failure_swallowed(monkeypatch, mock_mirror_module):
    """单次失败不抛, 静默吞 + log, 等下一天.

    用 call_count 控制: 第 1 次抛 fail, 第 2 次抛 CancelledError 退出循环.
    """
    from app.strategies.dgr_btc import mirror_scheduler as ms

    call_count = {"n": 0}

    async def side_effect():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated single failure")
        # 第 2 次: 抛 CancelledError 让循环退出
        raise asyncio.CancelledError()

    mock_mirror_module.main = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(ms, "_load_mirror_check_module", lambda: mock_mirror_module)
    monkeypatch.setattr(ms, "_seconds_until_next_utc_midnight", lambda: 0)

    # run_forever 应被 CancelledError 终止 (不是 RuntimeError 抛上来)
    with pytest.raises(asyncio.CancelledError):
        await ms.run_forever()

    # main() 被调了 2 次, 但第 1 次的 RuntimeError 被 swallow (没抛给 run_forever 的 caller)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_consecutive_failures_raise_after_threshold(monkeypatch, mock_mirror_module):
    """连续 _MAX_CONSECUTIVE_FAILURES 次失败必须 raise 给 supervisor."""
    from app.strategies.dgr_btc import mirror_scheduler as ms

    mock_mirror_module.main.side_effect = RuntimeError("永久失败")
    monkeypatch.setattr(ms, "_load_mirror_check_module", lambda: mock_mirror_module)
    monkeypatch.setattr(ms, "_seconds_until_next_utc_midnight", lambda: 0)

    # 直接 await run_forever, 期望第 N 次失败时抛
    with pytest.raises(RuntimeError, match="永久失败"):
        await asyncio.wait_for(ms.run_forever(), timeout=2.0)

    # main 应该被调用了 _MAX_CONSECUTIVE_FAILURES 次
    assert mock_mirror_module.main.call_count == ms._MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_success_resets_failure_counter(monkeypatch, mock_mirror_module):
    """失败-失败-成功-失败 序列: 成功重置 counter, 不会触发 raise.

    序列: fail, fail, success, fail, fail (累计 fail=2, 不到阈值 3),
    第 6 次 CancelledError 退出.
    """
    from app.strategies.dgr_btc import mirror_scheduler as ms

    call_outcomes = [
        RuntimeError("f1"),
        RuntimeError("f2"),
        0,  # success
        RuntimeError("f3"),
        RuntimeError("f4"),
        asyncio.CancelledError(),  # 退出
    ]

    async def side_effect():
        outcome = call_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    mock_mirror_module.main = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(ms, "_load_mirror_check_module", lambda: mock_mirror_module)
    monkeypatch.setattr(ms, "_seconds_until_next_utc_midnight", lambda: 0)

    with pytest.raises(asyncio.CancelledError):
        await ms.run_forever()

    # 6 次都被调过, RuntimeError 全部 swallow (因为 success 重置 counter)
    assert mock_mirror_module.main.call_count == 6


@pytest.mark.asyncio
async def test_module_load_failure_raises_immediately(monkeypatch):
    """module load 失败立即抛 → supervisor 重启."""
    from app.strategies.dgr_btc import mirror_scheduler as ms

    def boom():
        raise ImportError("simulated module load failure")

    monkeypatch.setattr(ms, "_load_mirror_check_module", boom)

    with pytest.raises(ImportError, match="simulated module load failure"):
        await ms.run_forever()
