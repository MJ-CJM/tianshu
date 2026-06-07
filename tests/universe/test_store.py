"""Tests for UniverseStore — snapshot / branch / restore."""
from pathlib import Path

import pytest

from tianshu.universe.store import UniverseStore


@pytest.fixture
def store(tmp_path: Path) -> UniverseStore:
    (tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (tmp_path / "personas" / "bingbu" / "SOUL.md").write_text("soul-v1")
    (tmp_path / "skills").mkdir()
    return UniverseStore(
        tmp_path / "universes",
        tmp_path / "personas",
        tmp_path / "skills",
    )


def test_snapshot_and_read_manifest(store: UniverseStore):
    store.snapshot_live("u1", {"agent_config": {"x": 1}})
    assert store.exists("u1")
    assert store.read_manifest("u1") == {"agent_config": {"x": 1}}
    assert (store.personas_dir("u1") / "bingbu" / "SOUL.md").read_text() == "soul-v1"


def test_snapshot_creates_skills_dir(store: UniverseStore):
    store.snapshot_live("u1", {})
    assert store.skills_dir("u1").exists()


def test_branch_is_full_copy(store: UniverseStore):
    store.snapshot_live("u1", {"a": 1})
    store.branch_from("u1", "u2")
    assert (store.personas_dir("u2") / "bingbu" / "SOUL.md").read_text() == "soul-v1"
    assert store.read_manifest("u2") == {"a": 1}


def test_branch_missing_parent_raises(store: UniverseStore):
    with pytest.raises(FileNotFoundError):
        store.branch_from("nope", "u2")


def test_branch_existing_child_raises(store: UniverseStore):
    store.snapshot_live("u1", {})
    store.branch_from("u1", "u2")
    with pytest.raises(FileExistsError):
        store.branch_from("u1", "u2")


def test_restore_overwrites_live(store: UniverseStore):
    store.snapshot_live("u1", {"a": 1})
    # Drift live after snapshot
    (store._live_personas / "bingbu" / "SOUL.md").write_text("soul-v2")  # noqa: SLF001
    manifest = store.restore_to_live("u1")
    assert manifest == {"a": 1}
    assert (store._live_personas / "bingbu" / "SOUL.md").read_text() == "soul-v1"  # noqa: SLF001


def test_restore_missing_universe_raises(store: UniverseStore):
    with pytest.raises(FileNotFoundError):
        store.restore_to_live("ghost")


def test_read_manifest_missing_returns_empty(store: UniverseStore):
    assert store.read_manifest("nonexistent") == {}


def test_exists_false_before_snapshot(store: UniverseStore):
    assert not store.exists("u99")


def test_write_and_read_manifest_roundtrip(store: UniverseStore):
    store.snapshot_live("u1", {})
    store.write_manifest("u1", {"key": "value", "nested": {"n": 42}})
    assert store.read_manifest("u1") == {"key": "value", "nested": {"n": 42}}
