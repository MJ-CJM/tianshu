"""Edict lifecycle_phase 字段与 storage 持久化测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.storage import Storage


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.db"
        s = Storage(str(db))
        s.init_db()
        yield s
        s.close()


def test_edict_runtime_default_lifecycle_phase_is_active():
    rt = EdictRuntime()
    assert rt.lifecycle_phase == "active"


def test_edict_runtime_accepts_all_lifecycle_phases():
    for phase in ("active", "paused", "winding_down", "complete"):
        rt = EdictRuntime(lifecycle_phase=phase)
        assert rt.lifecycle_phase == phase


def test_edict_runtime_rejects_unknown_lifecycle_phase():
    with pytest.raises(ValueError):
        EdictRuntime(lifecycle_phase="bogus")


def test_storage_persists_lifecycle_phase(storage: Storage):
    edict = Edict(
        goal="test goal",
        runtime=EdictRuntime(lifecycle_phase="winding_down"),
        acceptance=AcceptanceCriteria(),
    )
    storage.save_edict(edict)
    loaded = storage.get_edict(edict.id)
    assert loaded is not None
    assert loaded.runtime.lifecycle_phase == "winding_down"


def test_update_edict_lifecycle_phase(storage: Storage):
    edict = Edict(goal="g", acceptance=AcceptanceCriteria())
    storage.save_edict(edict)
    storage.update_edict_lifecycle_phase(edict.id, "paused")
    loaded = storage.get_edict(edict.id)
    assert loaded.runtime.lifecycle_phase == "paused"


def test_update_edict_lifecycle_phase_preserves_other_runtime_fields(storage: Storage):
    edict = Edict(
        goal="g",
        runtime=EdictRuntime(token_budget=99999, max_iterations=42),
        acceptance=AcceptanceCriteria(),
    )
    storage.save_edict(edict)
    storage.update_edict_lifecycle_phase(edict.id, "winding_down")
    loaded = storage.get_edict(edict.id)
    assert loaded.runtime.token_budget == 99999
    assert loaded.runtime.max_iterations == 42
    assert loaded.runtime.lifecycle_phase == "winding_down"
