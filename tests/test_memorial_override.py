"""Memorial.runtime_override / acceptance_override 端到端单元测试。

覆盖三种 follow-up 场景（来自 plan）：
1. 沿用：override 全空 → executor 合并不改 edict
2. 升级长任务：acceptance_override 提供 → 合并后 edict.acceptance 非空
3. 改 runtime：runtime_override 字段级覆盖 edict.runtime
"""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

from tianshu.executor.executor import Executor
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage


def _make_storage() -> Storage:
    s = Storage(tempfile.mktemp(suffix=".db"))
    s.init_db()
    return s


def _make_executor(storage: Storage) -> Executor:
    return Executor(
        event_bus=MagicMock(),
        storage=storage,
        config_manager=MagicMock(),
        hook_registry=MagicMock(),
    )


# ---------- Storage round-trip ----------


def test_memorial_persists_runtime_override():
    s = _make_storage()
    e = Edict(goal="g")
    s.save_edict(e)
    m = Memorial(
        edict_id=e.id,
        runtime_override={"timeout_seconds": 60, "max_iterations": 8},
    )
    s.save_memorial(m)

    fetched = s.get_memorial(m.id)
    assert fetched is not None
    assert fetched.runtime_override == {"timeout_seconds": 60, "max_iterations": 8}
    assert fetched.acceptance_override is None


def test_memorial_persists_acceptance_override():
    s = _make_storage()
    e = Edict(goal="g")
    s.save_edict(e)
    ao = AcceptanceCriteria(
        max_outer_iterations=3,
        critic=CriticSpec(persona_ids=["ducha"], strictness="strict"),
    )
    m = Memorial(edict_id=e.id, acceptance_override=ao)
    s.save_memorial(m)

    fetched = s.get_memorial(m.id)
    assert fetched is not None
    assert fetched.acceptance_override is not None
    assert fetched.acceptance_override.max_outer_iterations == 3
    assert fetched.acceptance_override.critic.strictness == "strict"
    assert fetched.acceptance_override.critic.persona_ids == ["ducha"]


def test_memorial_default_overrides_are_none():
    s = _make_storage()
    e = Edict(goal="g")
    s.save_edict(e)
    m = Memorial(edict_id=e.id)
    s.save_memorial(m)

    fetched = s.get_memorial(m.id)
    assert fetched is not None
    assert fetched.runtime_override is None
    assert fetched.acceptance_override is None


# ---------- Executor merge ----------


def test_apply_override_returns_same_edict_when_empty():
    """情况 1：override 全空 → 返回原 edict（identity，不复制）。"""
    s = _make_storage()
    ex = _make_executor(s)
    e = Edict(goal="g", runtime=EdictRuntime(timeout_seconds=300))
    m = Memorial(edict_id=e.id)
    assert ex._apply_memorial_override(e, m) is e


def test_apply_runtime_override_field_level_merge():
    """情况 3：runtime_override 字段级浅合并 — 只覆盖填了的字段。"""
    s = _make_storage()
    ex = _make_executor(s)
    e = Edict(
        goal="g",
        runtime=EdictRuntime(timeout_seconds=300, max_iterations=20, retry_limit=5),
    )
    m = Memorial(edict_id=e.id, runtime_override={"timeout_seconds": 60})

    merged = ex._apply_memorial_override(e, m)
    # 覆盖字段
    assert merged.runtime.timeout_seconds == 60
    # 未覆盖字段保留 edict 原值
    assert merged.runtime.max_iterations == 20
    assert merged.runtime.retry_limit == 5
    # 不污染原 edict
    assert e.runtime.timeout_seconds == 300


def test_apply_acceptance_override_replaces_completely():
    """情况 2：原 edict 无 acceptance，override 提供则整体注入（升级长任务）。"""
    s = _make_storage()
    ex = _make_executor(s)
    e = Edict(goal="g")
    assert e.acceptance is None
    ao = AcceptanceCriteria(
        max_outer_iterations=4,
        critic=CriticSpec(persona_ids=["ducha"]),
    )
    m = Memorial(edict_id=e.id, acceptance_override=ao)

    merged = ex._apply_memorial_override(e, m)
    assert merged.acceptance is not None
    assert merged.acceptance.max_outer_iterations == 4
    assert merged.acceptance.critic.persona_ids == ["ducha"]
    # 原 edict 仍无 acceptance
    assert e.acceptance is None


def test_apply_acceptance_override_when_edict_already_has_acceptance():
    """edict 已有 acceptance，override 整体替换（不深合并）。"""
    s = _make_storage()
    ex = _make_executor(s)
    e = Edict(
        goal="g",
        acceptance=AcceptanceCriteria(
            max_outer_iterations=10,
            critic=CriticSpec(persona_ids=["a"]),
        ),
    )
    ao = AcceptanceCriteria(
        max_outer_iterations=2,
        critic=CriticSpec(persona_ids=["b"]),
    )
    m = Memorial(edict_id=e.id, acceptance_override=ao)

    merged = ex._apply_memorial_override(e, m)
    assert merged.acceptance.max_outer_iterations == 2
    assert merged.acceptance.critic.persona_ids == ["b"]


def test_apply_both_overrides_simultaneously():
    s = _make_storage()
    ex = _make_executor(s)
    e = Edict(goal="g", runtime=EdictRuntime(timeout_seconds=300))
    m = Memorial(
        edict_id=e.id,
        runtime_override={"timeout_seconds": 60},
        acceptance_override=AcceptanceCriteria(critic=CriticSpec(persona_ids=["x"])),
    )
    merged = ex._apply_memorial_override(e, m)
    assert merged.runtime.timeout_seconds == 60
    assert merged.acceptance is not None
    assert merged.acceptance.critic.persona_ids == ["x"]
