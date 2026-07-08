"""司礼监·代批(迭代 7「制度补全」)——四道闸:低风险 / 留痕可撤 / 急停穿透 / 历史高通过率。"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.executor.silijian import Silijian
from tianshu.models.common import TaskStatus
from tianshu.models.decree import Decree
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.tools.types import ToolTier


@dataclass
class _EstopState:
    engaged: bool


class _FakeEstop:
    def __init__(self, engaged: bool = False):
        self._engaged = engaged

    def status(self):
        return _EstopState(self._engaged)


def _seed_human_approvals(storage, approve: int, reject: int) -> None:
    storage.save_edict(Edict(id="e1", goal="seed"))
    for i in range(approve + reject):
        m = Memorial(id=f"m{i}", edict_id="e1", status=TaskStatus.COMPLETED)
        storage.save_memorial(m)
        action = "approve" if i < approve else "reject"
        storage.save_decree(Decree(memorial_id=f"m{i}", action=action, actor="human"))


def _silijian(storage, config_manager, *, engaged=False):
    config_manager.update_agent_config(
        silijian_enabled=True,
        silijian_max_tier=1,
        silijian_min_approval_rate=0.9,
        silijian_min_samples=10,
    )
    return Silijian(storage, config_manager, estop_manager=_FakeEstop(engaged))


class TestGates:
    def test_disabled_returns_none(self, storage, config_manager):
        config_manager.update_agent_config(silijian_enabled=False)
        s = Silijian(storage, config_manager, estop_manager=_FakeEstop())
        assert s.maybe_auto_approve("m", ToolTier.T1_WORKSPACE, "r") is None

    def test_estop_engaged_blocks(self, storage, config_manager):
        _seed_human_approvals(storage, approve=12, reject=0)
        s = _silijian(storage, config_manager, engaged=True)
        assert s.maybe_auto_approve("mx", ToolTier.T1_WORKSPACE, "r") is None

    def test_high_tier_blocked(self, storage, config_manager):
        _seed_human_approvals(storage, approve=12, reject=0)
        s = _silijian(storage, config_manager)
        assert s.maybe_auto_approve("mx", ToolTier.T3_WRITE, "r") is None

    def test_cold_start_insufficient_samples(self, storage, config_manager):
        _seed_human_approvals(storage, approve=3, reject=0)  # < min_samples=10
        s = _silijian(storage, config_manager)
        assert s.maybe_auto_approve("mx", ToolTier.T1_WORKSPACE, "r") is None

    def test_low_approval_rate_blocked(self, storage, config_manager):
        _seed_human_approvals(storage, approve=6, reject=6)  # 50% < 90%
        s = _silijian(storage, config_manager)
        assert s.maybe_auto_approve("mx", ToolTier.T1_WORKSPACE, "r") is None

    def test_all_gates_pass_auto_approves(self, storage, config_manager):
        _seed_human_approvals(storage, approve=12, reject=0)
        s = _silijian(storage, config_manager)
        storage.save_memorial(Memorial(id="mx", edict_id="e1", status=TaskStatus.SUBMITTED))
        decree = s.maybe_auto_approve("mx", ToolTier.T1_WORKSPACE, "workspace_write")
        assert decree is not None
        assert decree.action == "approve" and decree.actor == "silijian"
        assert decree.grant_scope == "once"
        # 闸2:留痕——落库可查可撤
        persisted = storage.list_decrees_by_memorial("mx")
        assert any(d.actor == "silijian" for d in persisted)

    def test_self_approvals_excluded_from_signal(self, storage, config_manager):
        # 只人工 decree 计入通过率:全是 silijian 自批时样本不足,不自我强化
        storage.save_edict(Edict(id="e1", goal="seed"))
        for i in range(12):
            storage.save_memorial(Memorial(id=f"s{i}", edict_id="e1", status=TaskStatus.COMPLETED))
            storage.save_decree(Decree(memorial_id=f"s{i}", action="approve", actor="silijian"))
        s = _silijian(storage, config_manager)
        assert s.maybe_auto_approve("mx", ToolTier.T1_WORKSPACE, "r") is None
