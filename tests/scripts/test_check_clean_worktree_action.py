from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_ACTION = _ROOT / ".github" / "actions" / "check-clean-worktree" / "action.yml"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )


def _initialized_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "CI Test")
    _git(repo, "config", "user.email", "ci-test@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.hooksPath", "")
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "test baseline")
    return repo


def _action_script() -> str:
    assert _ACTION.is_file(), f"missing clean-worktree action: {_ACTION}"
    lines = _ACTION.read_text(encoding="utf-8").splitlines()
    run_index = next(index for index, line in enumerate(lines) if line.strip() == "run: |")
    return textwrap.dedent("\n".join(lines[run_index + 1 :]))


def _run_action(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GITHUB_WORKSPACE"] = str(repo)
    return subprocess.run(
        ["/bin/bash", "-c", _action_script()],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_clean_worktree_action_accepts_clean_repo(tmp_path: Path) -> None:
    repo = _initialized_repo(tmp_path)

    result = _run_action(repo, tmp_path)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("dirty_state", ("tracked", "staged", "untracked"))
def test_clean_worktree_action_rejects_all_visible_dirt(tmp_path: Path, dirty_state: str) -> None:
    repo = _initialized_repo(tmp_path)
    if dirty_state == "tracked":
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
    elif dirty_state == "staged":
        (repo / "tracked.txt").write_text("staged\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
    else:
        (repo / "generated.txt").write_text("untracked\n", encoding="utf-8")

    result = _run_action(repo, tmp_path)

    assert result.returncode == 1
    assert "worktree is not clean" in result.stderr


def test_clean_worktree_action_ignores_declared_build_outputs(tmp_path: Path) -> None:
    repo = _initialized_repo(tmp_path)
    ignored = repo / "ignored" / "build.txt"
    ignored.parent.mkdir()
    ignored.write_text("generated\n", encoding="utf-8")

    result = _run_action(repo, tmp_path)

    assert result.returncode == 0, result.stderr
