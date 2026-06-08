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
