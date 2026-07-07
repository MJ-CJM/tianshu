"""Tests for CodeMutator — allowlist, failure-safe, fence-strip, git commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

from tianshu.universe.code_mutator import CodeMutator

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_EVOLVABLE = (
    "src/tianshu/planner/",  # dir prefix
    "src/tianshu/config.py",  # exact file
)


def _make_llm(content: str) -> AsyncMock:
    """Fake async LLM whose .chat() returns an object with .content."""
    m = AsyncMock()
    m.chat.return_value = type("R", (), {"content": content})()
    return m


def _mutator(content: str) -> CodeMutator:
    return CodeMutator(_make_llm(content), evolvable_paths=_EVOLVABLE)


def _git_worktree(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit containing a target file."""
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(wt), check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "init",
        ],
        cwd=str(wt),
        check=True,
    )

    target_dir = wt / "src" / "tianshu" / "planner"
    target_dir.mkdir(parents=True)
    (target_dir / "foo.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(wt), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "add file"],
        cwd=str(wt),
        check=True,
    )
    return wt


# ---------------------------------------------------------------------------
# allowlist unit tests
# ---------------------------------------------------------------------------


def test_within_evolvable_matches_file_and_dir():
    m = CodeMutator(_make_llm(""), evolvable_paths=_EVOLVABLE)

    # exact file match
    assert m.is_within_evolvable("src/tianshu/config.py") is True

    # directory prefix match (with and without leading slash)
    assert m.is_within_evolvable("src/tianshu/planner/foo.py") is True
    assert m.is_within_evolvable("src/tianshu/planner/sub/bar.py") is True

    # out of allowlist
    assert m.is_within_evolvable("src/tianshu/llm.py") is False
    assert m.is_within_evolvable("src/tianshu/planner") is False  # dir itself, no trailing slash
    assert m.is_within_evolvable("") is False


# ---------------------------------------------------------------------------
# mutate() tests
# ---------------------------------------------------------------------------


async def test_mutate_rejects_out_of_allowlist(tmp_path):
    wt = _git_worktree(tmp_path)
    original = (wt / "src" / "tianshu" / "planner" / "foo.py").read_text()

    result = await _mutator("NEW = 99\n").mutate(
        wt,
        target_path="src/tianshu/llm.py",  # NOT in allowlist
        hypothesis="some change",
    )

    assert result["applied"] is False
    assert result["commit"] is None
    assert "allowlist" in result["reason"]
    # file in worktree should be unchanged (it's a different path, but confirm no side-effects)
    assert (wt / "src" / "tianshu" / "planner" / "foo.py").read_text() == original


async def test_mutate_applies_and_commits(tmp_path):
    wt = _git_worktree(tmp_path)
    before_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt),
        text=True,
    ).strip()

    result = await _mutator("X = 42\n").mutate(
        wt,
        target_path="src/tianshu/planner/foo.py",
        hypothesis="use 42 instead",
    )

    assert result["applied"] is True
    assert result["commit"] is not None
    assert result["commit"] != before_sha
    assert (wt / "src" / "tianshu" / "planner" / "foo.py").read_text().strip() == "X = 42"


async def test_mutate_noop_on_empty_llm(tmp_path):
    wt = _git_worktree(tmp_path)
    before_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt),
        text=True,
    ).strip()
    original = (wt / "src" / "tianshu" / "planner" / "foo.py").read_text()

    result = await _mutator("").mutate(
        wt,
        target_path="src/tianshu/planner/foo.py",
        hypothesis="do nothing",
    )

    assert result["applied"] is False
    assert result["commit"] is None
    assert (wt / "src" / "tianshu" / "planner" / "foo.py").read_text() == original
    after_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt),
        text=True,
    ).strip()
    assert after_sha == before_sha


async def test_mutate_noop_on_unchanged_llm(tmp_path):
    wt = _git_worktree(tmp_path)
    before_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt),
        text=True,
    ).strip()

    # LLM returns same content as original file
    result = await _mutator("X = 1\n").mutate(
        wt,
        target_path="src/tianshu/planner/foo.py",
        hypothesis="same content",
    )

    assert result["applied"] is False
    assert result["commit"] is None
    after_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt),
        text=True,
    ).strip()
    assert after_sha == before_sha


async def test_mutate_strips_code_fence(tmp_path):
    wt = _git_worktree(tmp_path)

    result = await _mutator("```python\nX=2\n```").mutate(
        wt,
        target_path="src/tianshu/planner/foo.py",
        hypothesis="set X to 2",
    )

    assert result["applied"] is True
    content = (wt / "src" / "tianshu" / "planner" / "foo.py").read_text()
    assert "X=2" in content
    assert "```" not in content


async def test_mutate_noop_on_llm_exception(tmp_path):
    wt = _git_worktree(tmp_path)
    original = (wt / "src" / "tianshu" / "planner" / "foo.py").read_text()

    bad_llm = AsyncMock()
    bad_llm.chat.side_effect = RuntimeError("network error")
    m = CodeMutator(bad_llm, evolvable_paths=_EVOLVABLE)

    result = await m.mutate(
        wt,
        target_path="src/tianshu/planner/foo.py",
        hypothesis="try something",
    )

    assert result["applied"] is False
    assert "llm error" in result["reason"]
    assert (wt / "src" / "tianshu" / "planner" / "foo.py").read_text() == original


async def test_mutate_noop_on_missing_file(tmp_path):
    wt = _git_worktree(tmp_path)

    result = await _mutator("Y = 9\n").mutate(
        wt,
        target_path="src/tianshu/planner/nonexistent.py",
        hypothesis="some hypo",
    )

    assert result["applied"] is False
    assert "not found" in result["reason"]
