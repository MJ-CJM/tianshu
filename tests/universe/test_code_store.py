"""Tests for CodeVariantStore — git worktree 生命周期。"""
import subprocess
from pathlib import Path

import pytest

from tianshu.universe.code_store import CodeVariantStore


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)


@pytest.fixture
def store(tmp_path: Path) -> CodeVariantStore:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.test")
    _git(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("v1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return CodeVariantStore(repo, tmp_path / "worktrees")


def test_branch_creates_worktree(store: CodeVariantStore):
    ref = store.branch_code_variant("u1")
    assert ref == "universe/u1"
    assert store.exists("u1")
    assert (store.worktree_dir("u1") / "src.txt").read_text() == "v1\n"


def test_branch_twice_raises(store: CodeVariantStore):
    store.branch_code_variant("u1")
    with pytest.raises(FileExistsError):
        store.branch_code_variant("u1")


def test_exists_false_before_branch(store: CodeVariantStore):
    assert not store.exists("ghost")


def test_diff_shows_changes(store: CodeVariantStore):
    store.branch_code_variant("u1")
    wt = store.worktree_dir("u1")
    (wt / "src.txt").write_text("v2\n")
    out = store.diff("u1")
    assert "-v1" in out and "+v2" in out


def test_diff_empty_when_unchanged(store: CodeVariantStore):
    store.branch_code_variant("u1")
    assert store.diff("u1").strip() == ""


def test_diff_missing_raises(store: CodeVariantStore):
    with pytest.raises(FileNotFoundError):
        store.diff("never")


def test_gc_removes_worktree_keeps_branch(store: CodeVariantStore):
    store.branch_code_variant("u1")
    store.gc_worktree("u1")
    assert not store.exists("u1")
    out = subprocess.run(
        ["git", "branch", "--list", "universe/u1"],
        cwd=str(store._repo), capture_output=True, text=True,  # noqa: SLF001
    ).stdout
    assert "universe/u1" in out


def test_restore_rebuilds_worktree_with_committed_work(store: CodeVariantStore):
    store.branch_code_variant("u1")
    wt = store.worktree_dir("u1")
    (wt / "src.txt").write_text("v2\n")
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.test", "-c", "user.name=t",
         "commit", "-q", "-m", "edit"],
        cwd=str(wt), check=True,
    )
    store.gc_worktree("u1")
    store.restore_worktree("u1")
    assert store.exists("u1")
    assert (wt / "src.txt").read_text() == "v2\n"


def test_restore_noop_when_present(store: CodeVariantStore):
    store.branch_code_variant("u1")
    store.restore_worktree("u1")  # 已存在 → 不报错
    assert store.exists("u1")
