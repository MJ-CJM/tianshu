"""分级急停 EstopManager(迭代 3「深防御」)——三档语义 + 持久化 + fail-closed。"""

from __future__ import annotations

import pytest

from tianshu.security.estop import EstopManager, EstopState


class TestEstopState:
    def test_engaged_flag(self):
        assert not EstopState().engaged
        assert EstopState(kill_all=True).engaged
        assert EstopState(network_kill=True).engaged
        assert EstopState(frozen_tools=frozenset({"x"})).engaged

    def test_fail_closed(self):
        s = EstopState.fail_closed()
        assert s.kill_all and s.engaged


class TestEstopManager:
    @pytest.fixture
    def mgr(self, storage):
        return EstopManager(storage)

    def test_default_all_pass(self, mgr):
        assert mgr.check("read_file") is None
        assert mgr.check("web_fetch") is None

    def test_kill_all_blocks_everything(self, mgr):
        mgr.engage(kill_all=True, reason="drill")
        assert mgr.check("read_file") is not None
        assert mgr.check("web_fetch") is not None

    def test_network_kill_blocks_only_network(self, mgr):
        mgr.engage(network_kill=True)
        assert mgr.check("web_fetch") is not None
        assert mgr.check("shell_exec") is not None  # shell 可发网络
        assert mgr.check("read_file") is None

    def test_tool_freeze_targeted(self, mgr):
        mgr.engage(freeze_tools=["shell_exec"])
        assert mgr.check("shell_exec") is not None
        assert mgr.check("read_file") is None

    def test_persistence_across_reload(self, mgr, storage):
        mgr.engage(kill_all=True)
        reloaded = EstopManager(storage)
        assert reloaded.status().kill_all

    def test_resume_all_clear(self, mgr):
        mgr.engage(kill_all=True, network_kill=True, freeze_tools=["x"])
        mgr.resume(all_clear=True)
        assert not mgr.status().engaged

    def test_resume_selective(self, mgr):
        mgr.engage(kill_all=True, network_kill=True)
        mgr.resume(kill_all=True)  # 只解 kill_all
        assert not mgr.status().kill_all
        assert mgr.status().network_kill

    def test_engage_additive(self, mgr):
        mgr.engage(network_kill=True)
        mgr.engage(freeze_tools=["a"])  # 不给 network_kill → 保持
        assert mgr.status().network_kill
        assert "a" in mgr.status().frozen_tools

    def test_fail_closed_on_corrupt_state(self, storage):
        storage.save_estop_state(
            {"kill_all": False, "network_kill": False, "frozen_tools_json": "NOT JSON{"}
        )
        mgr = EstopManager(storage)
        assert mgr.status().kill_all  # 损坏 → fail closed
