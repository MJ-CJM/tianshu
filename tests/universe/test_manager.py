"""Tests for UniverseManager — genesis / branch / switch / archive / diff."""
from pathlib import Path

import pytest

from tianshu.storage import Storage
from tianshu.universe.manager import UniverseManager
from tianshu.universe.store import UniverseStore


class _FakePersona:
    def __init__(self, d: Path):
        self.runtime_dir = d

    def repoint_runtime(self, _):
        pass


class _FakeSkills:
    def __init__(self, d: Path):
        self._user_dir = d

    @property
    def user_dir(self):
        return self._user_dir

    def repoint_user_dir(self, _):
        pass


@pytest.fixture
def mgr(tmp_path: Path) -> UniverseManager:
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "SOUL.md").write_text("v1")
    (tmp_path / "skills").mkdir()
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")
    cfg = {"agent_config": {}}
    return UniverseManager(
        s,
        store,
        _FakePersona(tmp_path / "personas"),
        _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg,
        config_apply=lambda m: None,
    )


def test_ensure_genesis_is_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    assert g["status"] == "champion"
    assert mgr.ensure_genesis()["id"] == g["id"]  # idempotent


def test_genesis_origin(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    assert g["origin"] == "genesis"


def test_list_returns_genesis(mgr: UniverseManager):
    mgr.ensure_genesis()
    universes = mgr.list()
    assert len(universes) == 1


def test_branch_then_switch_keeps_single_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    assert ch["status"] == "challenger"
    mgr.switch(ch["id"])
    champs = [u for u in mgr.list() if u["status"] == "champion"]
    assert len(champs) == 1 and champs[0]["id"] == ch["id"]


def test_branch_has_correct_parent(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp", description="test branch")
    assert ch["parent_universe_id"] == g["id"]


def test_cannot_archive_champion(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    with pytest.raises(ValueError, match="champion"):
        mgr.archive(g["id"])


def test_can_archive_challenger(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    archived = mgr.archive(ch["id"])
    assert archived["status"] == "archived"


def test_switch_to_self_is_noop(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    result = mgr.switch(g["id"])
    assert result["id"] == g["id"]
    # Still champion, still only one
    champs = [u for u in mgr.list() if u["status"] == "champion"]
    assert len(champs) == 1


def test_switch_archived_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    mgr.archive(ch["id"])
    with pytest.raises(ValueError, match="archived"):
        mgr.switch(ch["id"])


def test_rollback_restores_historical_state(mgr: UniverseManager, tmp_path: Path):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    mgr.switch(ch["id"])
    # Drift live while ch is champion
    (tmp_path / "personas" / "bingbu" / "SOUL.md").write_text("v2")
    ch2 = mgr.branch(ch["id"], "exp2")
    # Rollback to g
    mgr.switch(g["id"])
    assert (tmp_path / "personas" / "bingbu" / "SOUL.md").read_text() == "v1"
    diff = mgr.diff(g["id"], ch2["id"])
    assert "bingbu/SOUL.md" in diff["personas"]["changed"]


def test_switch_nonexistent_raises(mgr: UniverseManager):
    mgr.ensure_genesis()
    with pytest.raises(ValueError, match="not found"):
        mgr.switch("ghost-id")


def test_branch_nonexistent_parent_raises(mgr: UniverseManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.branch("ghost-id", "child")


def test_archive_nonexistent_raises(mgr: UniverseManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.archive("ghost-id")


def test_diff_shows_no_changes_for_same(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    d = mgr.diff(g["id"], ch["id"])
    # Immediate branch from g without drift: personas same, skills same
    assert d["personas"]["changed"] == []
    assert d["personas"]["only_in_a"] == []
    assert d["personas"]["only_in_b"] == []


def test_champion_helper(mgr: UniverseManager):
    assert mgr.champion() is None
    g = mgr.ensure_genesis()
    assert mgr.champion()["id"] == g["id"]
    assert mgr.champion_id() == g["id"]


def test_list_exclude_archived(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    mgr.archive(ch["id"])
    active = mgr.list(include_archived=False)
    ids = {u["id"] for u in active}
    assert ch["id"] not in ids
    assert g["id"] in ids


def test_restore_archived_to_challenger(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")
    mgr.archive(ch["id"])
    restored = mgr.restore(ch["id"])
    assert restored["status"] == "challenger"


def test_restore_non_archived_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    with pytest.raises(ValueError):
        mgr.restore(g["id"])  # champion is not archived
