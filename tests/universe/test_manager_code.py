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
    yield UniverseManager(
        s,
        store,
        _FakePersona(tmp_path / "personas"),
        _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg,
        config_apply=lambda m: None,
        code_store=code_store,
    )
    s.close()


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


def test_code_diff_returns_git_diff(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    wt = mgr._code_store.worktree_dir(cv["id"])  # noqa: SLF001
    (wt / "src.txt").write_text("v2\n")
    assert "+v2" in mgr.code_diff(cv["id"])


def test_code_diff_on_data_universe_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    with pytest.raises(ValueError, match="code variant"):
        mgr.code_diff(g["id"])


def test_switch_to_code_variant_raises(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    with pytest.raises(ValueError, match="Deployer"):
        mgr.switch(cv["id"])


def test_archive_code_variant_gcs_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001
    mgr.archive(cv["id"])
    assert not mgr._code_store.exists(cv["id"])  # noqa: SLF001


def test_restore_code_variant_rebuilds_worktree(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")
    mgr.archive(cv["id"])
    mgr.restore(cv["id"])
    assert mgr._code_store.exists(cv["id"])  # noqa: SLF001


# ---------------------------------------------------------------------------
# promote_code_variant tests (uses a fake deployer)
# ---------------------------------------------------------------------------


class _FakeDeployer:
    """Records stage() calls; does not relaunch."""

    def __init__(self):
        self.stage_calls: list[dict] = []

    def stage(self, *, ref: str, worktree: str | None) -> None:
        self.stage_calls.append({"ref": ref, "worktree": worktree})


@pytest.fixture
def mgr_with_deployer(tmp_path: Path):
    """Build a fresh manager that has a fake deployer injected."""
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "SOUL.md").write_text("v1")
    (tmp_path / "skills").mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.test"], cwd=str(repo), capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=str(repo), capture_output=True, check=True
    )
    (repo / "src.txt").write_text("v1\n")
    subprocess.run(["git", "add", "-A"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(repo), capture_output=True, check=True
    )
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")
    code_store = CodeVariantStore(repo, tmp_path / "worktrees")
    cfg = {"agent_config": {}}
    fake_deployer = _FakeDeployer()
    mgr = UniverseManager(
        s,
        store,
        _FakePersona(tmp_path / "personas"),
        _FakeSkills(tmp_path / "skills"),
        config_snapshot=lambda: cfg,
        config_apply=lambda m: None,
        code_store=code_store,
        deployer=fake_deployer,
    )
    yield mgr, fake_deployer
    s.close()


def test_promote_code_variant_flips_and_stages(mgr_with_deployer):
    mgr, fake_deployer = mgr_with_deployer
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "perf-exp")

    result = mgr.promote_code_variant(cv["id"])

    # Variant is now champion
    assert result["status"] == "champion"
    # Old champion (genesis) demoted to challenger
    demoted = mgr._storage.get_universe(g["id"])  # noqa: SLF001
    assert demoted["status"] == "challenger"
    # deployer.stage called exactly once with correct ref + worktree
    assert len(fake_deployer.stage_calls) == 1
    call = fake_deployer.stage_calls[0]
    assert call["ref"] == cv["code_ref"]
    expected_wt = str(mgr._code_store.worktree_dir(cv["id"]))  # noqa: SLF001
    assert call["worktree"] == expected_wt


def test_promote_code_variant_requires_deployer(mgr_with_deployer):
    mgr, _ = mgr_with_deployer
    # Replace deployer with None
    mgr._deployer = None  # noqa: SLF001
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "exp")

    with pytest.raises(RuntimeError, match="deployer not configured"):
        mgr.promote_code_variant(cv["id"])


def test_promote_code_variant_rejects_data_universe(mgr_with_deployer):
    mgr, _ = mgr_with_deployer
    g = mgr.ensure_genesis()

    with pytest.raises(ValueError, match="code variant"):
        mgr.promote_code_variant(g["id"])


# --- delete tests for code variant ---


def test_delete_code_variant_removes_universe_and_worktree_and_branch(mgr: UniverseManager):
    g = mgr.ensure_genesis()
    cv = mgr.branch_code_variant(g["id"], "perf-exp")
    cv_id = cv["id"]
    assert mgr._code_store.exists(cv_id)  # noqa: SLF001

    result = mgr.delete(cv_id)

    assert result == {"id": cv_id}
    # Universe gone from DB
    assert cv_id not in {u["id"] for u in mgr.list()}
    # Worktree removed
    assert not mgr._code_store.exists(cv_id)  # noqa: SLF001
    # Branch deleted
    branch_list = subprocess.run(
        ["git", "branch", "--list", f"universe/{cv_id}"],
        cwd=str(mgr._code_store._repo),  # noqa: SLF001
        capture_output=True,
        text=True,
    ).stdout
    assert f"universe/{cv_id}" not in branch_list
