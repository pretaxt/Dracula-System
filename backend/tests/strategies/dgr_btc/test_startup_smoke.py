"""dgr_btc 启动 smoke test (round 2 audit arch 阻塞项).

防 chip 重构再次误删 main.py 启动段类事故.

策略: 不真启动 FastAPI / TaskSupervisor (太重), 只 import 关键 module +
构造 session 实例 + 验证 caller contract 完整, 任何 import / 构造失败即 fail.

CI 用法:
  pytest tests/strategies/dgr_btc/test_startup_smoke.py -v
  退出码非 0 即阻塞 PR merge.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


# ─── 1. 关键 module 都可 import (检测 syntax / 循环依赖 / 缺失) ───


def test_import_paper_trading():
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession  # noqa: F401
    assert DgrBtcPaperSession is not None


def test_import_engine():
    from app.strategies.dgr_btc.engine import (  # noqa: F401
        Decision, DecisionKind, EngineConfig, Layer, MartingaleEngine, StrategyState,
    )


def test_import_backtest_runner():
    from app.strategies.dgr_btc.backtest_runner import (  # noqa: F401
        BacktestResult, MartingaleBacktestRunner,
    )


def test_import_regime_detector():
    """#8 regime detector"""
    from app.strategies.dgr_btc.regime_detector import (  # noqa: F401
        RegimeMetrics, compute_regime_metrics, aggregate_hourly_to_daily,
    )


def test_import_mirror_scheduler():
    from app.strategies.dgr_btc.mirror_scheduler import run_forever  # noqa: F401
    assert run_forever is not None


def test_import_config():
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig  # noqa: F401


def test_import_config_schema():
    """#1 pydantic schema"""
    from app.strategies.dgr_btc.config_schema import validate_strategy_config  # noqa: F401


def test_import_execution_adapter():
    """#1 ExecutionAdapter 抽象"""
    from app.strategies.dgr_btc.execution_adapter import (  # noqa: F401
        BacktestBroker, PaperBroker,
    )


def test_import_live_metrics():
    """#4 LIVE metrics — 模块可导入, 不强制要求特定类名"""
    import app.strategies.dgr_btc.live_metrics as lm  # noqa: F401
    assert lm is not None


# ─── 2. DgrBtcPaperSession 可构造 (检测 __init__ 没 break) ───


@pytest.fixture
def tmpdir_state(monkeypatch, tmp_path):
    """覆盖 /app/state 容器路径 → 临时目录, 避免 mac 本地 ROFS 错误"""
    monkeypatch.setenv("DGR_BTC_STATE_DIR", str(tmp_path))
    return tmp_path


def test_session_can_construct_paper_mode(tmpdir_state):
    """paper 模式构造不能抛"""
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession

    cfg = DgrBtcStrategyConfig()
    adapter = MagicMock()
    sess = DgrBtcPaperSession(cfg=cfg, adapter=adapter, live_mode=False)
    assert sess is not None
    assert sess.live_mode is False


def test_session_can_construct_live_mode(tmpdir_state):
    """LIVE 模式 (broker_adapter 注入) 构造不能抛"""
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession

    cfg = DgrBtcStrategyConfig()
    adapter = MagicMock()
    broker = MagicMock()
    sess = DgrBtcPaperSession(
        cfg=cfg, adapter=adapter, live_mode=True, broker_adapter=broker,
    )
    assert sess is not None
    assert sess.live_mode is True
    assert sess._broker_adapter is broker


# ─── 3. Caller contract: app.state.dgr_btc_paper 期望的属性 ───


def test_session_exposes_caller_contract_attributes(tmpdir_state):
    """telegram / dashboard / positions 期望读这些属性, 不能丢"""
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession

    sess = DgrBtcPaperSession(cfg=DgrBtcStrategyConfig(), adapter=MagicMock())
    required_attrs = [
        "cfg", "strategy", "inflight_manager", "live_mode",
        "_running", "_last_spot_px", "_last_tick_at",
        "_state", "_cash",
        # round 2 新增
        "_last_deleverage_at", "_n_deleverages",
    ]
    for attr in required_attrs:
        assert hasattr(sess, attr), f"caller contract 丢失属性: {attr}"


# ─── 4. snapshot() / update_cfg() 接口可调 ───


def test_session_snapshot_returns_dict(tmpdir_state):
    """v1 caller 依赖 sess.snapshot() 返回 dict"""
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession

    sess = DgrBtcPaperSession(cfg=DgrBtcStrategyConfig(), adapter=MagicMock())
    snap = sess.snapshot()
    assert isinstance(snap, dict)
    # dashboard 依赖的 keys
    for k in ("running", "instance", "live_mode", "cycle_id", "n_layers", "cash", "total_equity"):
        assert k in snap, f"snapshot 缺 key: {k}"


# ─── 5. main.py 启动段关键调用可 resolve (防 chip 误删) ───


def test_main_py_can_import_dgr_btc_entry_points():
    """main.py 启动段引用的 dgr_btc 入口必须可 import.

    防本会话两次误删事故重演. runtime_overrides 的 load/save 函数也要可访问.
    """
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig  # noqa: F401
    from app.strategies.dgr_btc.paper_trading import DgrBtcPaperSession  # noqa: F401
    from app.strategies.dgr_btc.mirror_scheduler import run_forever  # noqa: F401

    # runtime_overrides 至少可 import (chip 加的 dgr_btc 子节函数可能仅 host 有)
    import app.services.runtime_overrides as ro  # noqa: F401
    assert ro is not None


def test_main_py_dgr_btc_paper_session_registered_token():
    """main.py 必须包含 task_supervisor.register('dgr_btc_paper_session', ...)
    + dgr_btc_pnl_writer 注册 + dgr_btc_mirror_scheduler spawn.

    grep 静态检测 (启动 lifespan 不真跑, 只查代码字符串).
    """
    from pathlib import Path
    main_py = Path(__file__).resolve().parents[3] / "app" / "main.py"
    assert main_py.exists(), f"main.py 不存在: {main_py}"
    content = main_py.read_text(encoding="utf-8")

    required_tokens = [
        "dgr_btc_paper_session",     # paper session 注册
        "dgr_btc_pnl_writer",        # PnL writer (本会话被误删过)
        "dgr_btc_mirror_scheduler",  # mirror scheduler
        "DgrBtcPaperSession",        # session 类引用
        "load_dgr_btc_overrides",    # override 加载
    ]
    for token in required_tokens:
        assert token in content, f"main.py 缺失关键 token: {token} (chip 可能误删启动段)"


# ─── 6. config validate (pydantic schema 单位 bug 拦截) ───


def test_default_config_passes_pydantic_validate():
    """默认 config 必须通过 pydantic schema 校验, 防单位 bug 类问题"""
    from app.strategies.dgr_btc.config import DgrBtcStrategyConfig

    cfg = DgrBtcStrategyConfig()
    cfg.validate()  # 不抛 = OK
