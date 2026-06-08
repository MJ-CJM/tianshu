"""Tests for UniverseManager — 代码变体路径。"""
import subprocess
from pathlib import Path

import pytest

from tianshu.storage import Storage
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.manager import UniverseManager
from tianshu.universe.store import UniverseStore


class _FakePersona:
    def __init__(self, d: Path):
        self.runtime_dir = d

    def repoint_runtime(self, _):
        pass


class _FakeSkills:
    def __init__(self, d: Path):
        self._d = d

    @property
    def user_dir(self):
        return self._d

    def repoint_user_dir(self, _):
        pass


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


@pytest.fixture
def mgr(tmp_path: Path) -> UniverseManager:
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "SOUL.md").write_text("v1")
    (tmp_path / "skills").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")
    code_store = CodeVariantStore(repo, tmp_path / "worktrees")
    cfg = {"agent_config": {}}
    return UniverseManager(
        s, store, _FakePersona(tmp_path / "personas"), _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg, config_apply=lambda m: None, code_store=code_store,
    )


def test_branch_code_variant_creates_universe_and_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "perf-exp")
    assert cv["origin"] == "code_variant"
    assert cv["status"] == "challenger"
    assert cv["code_ref"] == f"universe/{cv['id']}"
    assert cv["parent_universe_id"] == g["id"]
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001


def test_branch_code_variant_nonexistent_parent_raises(mgr: UniverseManager):
    with pytest.raises(ValueError, match="not found"):
        mgr.branch_code_variant("ghost", "x")
