"""Strict named Git backend contracts."""

from __future__ import annotations

import importlib.util
import inspect
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tianshu.executor.git_backend import (
    GitBackend,
    GitBackendError,
    GitIdentity,
    GitLocation,
    GitLogEntry,
)

_TRUSTED_GIT = shutil.which("git", path=os.defpath)


def _raw_git(repo: Path, *args: str) -> str:
    assert _TRUSTED_GIT is not None
    return subprocess.run(
        [_TRUSTED_GIT, *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _write_sentinel_helper(path: Path, sentinel: Path) -> None:
    path.write_text(
        f"#!/bin/sh\nprintf invoked > {shlex.quote(str(sentinel))}\nexit 99\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_process_output(target: object, data: bytes) -> None:
    if isinstance(target, int):
        os.write(target, data)
        return
    assert hasattr(target, "write")
    target.write(data)


def _loose_objects(repo: Path) -> set[str]:
    objects = repo / ".git" / "objects"
    return {
        path.relative_to(objects).as_posix()
        for path in objects.rglob("*")
        if path.is_file() and len(path.parent.name) == 2
    }


def _write_fake_signed_commit(repo: Path) -> str:
    assert _TRUSTED_GIT is not None
    tree = _raw_git(repo, "write-tree")
    parent = _raw_git(repo, "rev-parse", "HEAD")
    payload = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        "author Signed Fixture <signed@example.invalid> 1700000000 +0000\n"
        "committer Signed Fixture <signed@example.invalid> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n"
        " fake-signature-data\n"
        " -----END PGP SIGNATURE-----\n"
        "\n"
        "fake signed fixture\n"
    ).encode()
    commit = (
        subprocess.run(
            [_TRUSTED_GIT, "hash-object", "-t", "commit", "-w", "--stdin"],
            cwd=repo,
            input=payload,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    _raw_git(repo, "update-ref", "HEAD", commit, parent)
    return commit


def test_git_backend_module_exists() -> None:
    assert importlib.util.find_spec("tianshu.executor.git_backend") is not None


def test_git_backend_exposes_named_operations_not_raw_execution() -> None:
    public_callables = {
        name
        for name, value in inspect.getmembers(GitBackend)
        if not name.startswith("_") and callable(value)
    }

    assert {
        "commit",
        "commit_timestamp",
        "create_branch_worktree",
        "delete_branch",
        "diff",
        "init_repository",
        "list_log",
        "remove_worktree",
        "resolve_revision",
        "restore_branch_worktree",
        "restore_snapshot",
        "stage_all",
        "stage_paths",
    } <= public_callables
    assert public_callables.isdisjoint({"argv", "command", "execute", "invoke", "run"})


def test_git_backend_validates_untrusted_tokens_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid input reached subprocess.run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    backend = GitBackend()
    location = GitLocation(tmp_path)

    with pytest.raises(ValueError, match="revision"):
        backend.resolve_revision(location, "--upload-pack=evil")
    with pytest.raises(ValueError, match="branch"):
        backend.delete_branch(location, "bad..branch")
    with pytest.raises(ValueError, match="SHA"):
        backend.restore_snapshot(
            location,
            "HEAD",
            identity=GitIdentity("Tianshu", "tianshu@example.invalid"),
        )
    with pytest.raises(ValueError, match="pathspec"):
        backend.stage_paths(location, ("../outside",))
    with pytest.raises(ValueError, match="pathspec"):
        backend.stage_paths(location, ("nested/.GIT/config",))


def test_git_backend_uses_clean_noninteractive_environment_and_hardened_git_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        stdout = kwargs["stdout"]
        _write_process_output(stdout, b"abc1234\n")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("TIANSHU_LLM_API_KEY", "sk-super-secret-that-must-not-pass")
    monkeypatch.setattr(subprocess, "run", fake_run)

    sha = GitBackend().resolve_revision(GitLocation(tmp_path), "HEAD", short=True)

    assert sha == "abc1234"
    command = observed["command"]
    assert isinstance(command, list)
    assert Path(command[0]).is_absolute()
    assert Path(command[0]).name == "git"
    assert command[1] == "-c"
    assert "core.hooksPath=/dev/null" in command
    assert "diff.external=" in command
    assert command[-4:] == ["rev-parse", "--verify", "--short", "HEAD"]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert "TIANSHU_LLM_API_KEY" not in env
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["check"] is False


def test_git_backend_bounds_and_redacts_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stderr = kwargs["stderr"]
        _write_process_output(stderr, (secret + "\n" + ("x" * 500)).encode())
        return subprocess.CompletedProcess(command, 2)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GitBackendError) as raised:
        GitBackend(output_limit_bytes=80).resolve_revision(GitLocation(tmp_path), "HEAD")

    assert secret not in str(raised.value)
    assert "REDACTED" in str(raised.value)
    assert raised.value.operation == "resolve_revision"
    assert raised.value.returncode == 2
    assert raised.value.output_truncated is True


def test_git_backend_turns_timeout_into_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GitBackendError, match="timed out") as raised:
        GitBackend(timeout_seconds=0.01).resolve_revision(GitLocation(tmp_path), "HEAD")

    assert raised.value.timed_out is True


def test_git_backend_drains_large_output_through_bounded_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted = 2_000_000

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout_fd = kwargs["stdout"]
        stderr_fd = kwargs["stderr"]
        assert isinstance(stdout_fd, int)
        assert isinstance(stderr_fd, int)
        remaining = emitted
        chunk = b"a" * 65_536
        while remaining:
            written = os.write(stdout_fd, chunk[:remaining])
            remaining -= written
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(GitBackendError) as raised:
        GitBackend(output_limit_bytes=128).resolve_revision(GitLocation(tmp_path), "HEAD")

    assert raised.value.output_truncated is True
    assert "128 bytes" in str(raised.value)


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_does_not_resolve_git_from_mutable_process_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "initial",
    )
    sentinel = tmp_path / "path-git-invoked"
    fake_git = tmp_path / "git"
    _write_sentinel_helper(fake_git, sentinel)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")

    revision = GitBackend().resolve_revision(GitLocation(repo), "HEAD", short=True)

    assert revision
    assert not sentinel.exists()


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_neutralizes_repo_controlled_secondary_processes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    tracked = repo / "tracked.txt"
    tracked.write_text("v1\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("*.txt filter=evil diff=evil\n", encoding="utf-8")
    _raw_git(repo, "add", "-A")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )

    sentinel = tmp_path / "secondary-process-invoked"
    helper = tmp_path / "malicious-helper"
    _write_sentinel_helper(helper, sentinel)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    pre_commit = hooks / "pre-commit"
    _write_sentinel_helper(pre_commit, sentinel)
    for key, value in (
        ("core.fsmonitor", str(helper)),
        ("core.hooksPath", str(hooks)),
        ("filter.evil.clean", str(helper)),
        ("filter.evil.smudge", str(helper)),
        ("filter.evil.process", str(helper)),
        ("diff.evil.textconv", str(helper)),
        ("diff.external", str(helper)),
        ("commit.gpgSign", "true"),
        ("gpg.program", str(helper)),
    ):
        _raw_git(repo, "config", key, value)

    backend = GitBackend()
    location = GitLocation(repo)
    identity = GitIdentity("Safe Git", "safe@example.invalid")
    tracked.write_text("v2\n", encoding="utf-8")
    backend.stage_paths(location, ("tracked.txt",))
    backend.stage_all(location)
    revision = backend.commit(location, "safe commit", identity=identity)
    tracked.write_text("v3\n", encoding="utf-8")
    assert "+v3" in backend.diff(location, revision)
    worktree = tmp_path / "worktree"
    backend.create_branch_worktree(
        location,
        worktree,
        branch="universe/safe",
        start_ref=revision,
    )

    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "v2\n"
    backend.restore_snapshot(location, revision, identity=identity)
    assert tracked.read_text(encoding="utf-8") == "v2\n"
    assert not sentinel.exists()


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_disables_repo_controlled_signature_verification(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _raw_git(repo, "add", "-A")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    signed_commit = _write_fake_signed_commit(repo)
    sentinel = tmp_path / "signature-helper-invoked"
    helper = tmp_path / "malicious-gpg"
    _write_sentinel_helper(helper, sentinel)
    _raw_git(repo, "config", "log.showSignature", "true")
    _raw_git(repo, "config", "gpg.program", str(helper))
    backend = GitBackend()
    location = GitLocation(repo)

    assert backend.list_log(location)
    assert backend.commit_timestamp(location, signed_commit)
    assert not sentinel.exists()


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_diff_uses_isolated_object_database(tmp_path: Path) -> None:
    backend = GitBackend()
    identity = GitIdentity("Diff Test", "diff@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    location = GitLocation(repo)
    backend.init_repository(location)
    tracked = repo / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    backend.stage_all(location)
    base = backend.commit(location, "base", identity=identity)
    before = _loose_objects(repo)
    tracked.write_text("unique-uncommitted-change\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("unique-untracked-content\n", encoding="utf-8")

    diff = backend.diff(location, base)

    assert "unique-uncommitted-change" in diff
    assert "unique-untracked-content" in diff
    assert _loose_objects(repo) == before


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_inspect_repository_does_not_write_dirty_index_tree_to_source_objects(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    _raw_git(repo, "config", "user.name", "Fixture")
    _raw_git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "tracked.txt").write_text("base\n")
    _raw_git(repo, "add", "tracked.txt")
    _raw_git(repo, "commit", "-qm", "base")
    (repo / "staged-secret.txt").write_text("staged but not committed\n")
    _raw_git(repo, "add", "staged-secret.txt")
    before = _loose_objects(repo)

    snapshot = GitBackend().inspect_repository(GitLocation(repo))

    assert snapshot.clean is False
    assert _loose_objects(repo) == before


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_preflights_aggregate_stage_limit_before_writing_objects(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    (repo / "a.bin").write_bytes(b"a" * 10)
    (repo / "b.bin").write_bytes(b"b" * 10)
    before = _loose_objects(repo)
    backend = GitBackend(blob_limit_bytes=16, materialization_limit_bytes=16)

    with pytest.raises(GitBackendError, match="staged content exceeds 16 byte limit"):
        backend.stage_all(GitLocation(repo))

    assert _loose_objects(repo) == before


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_preflights_stage_path_count_before_writing_objects(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    for name in ("a.txt", "b.txt", "c.txt"):
        (repo / name).touch()
    before = _loose_objects(repo)
    backend = GitBackend(stage_path_limit=2)

    with pytest.raises(GitBackendError, match="staged path count exceeds 2 path limit"):
        backend.stage_all(GitLocation(repo))

    assert _loose_objects(repo) == before


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_batches_large_regular_file_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    backend = GitBackend()
    location = GitLocation(repo)
    backend.init_repository(location)
    for index in range(1_000):
        (repo / f"file-{index:04d}.txt").touch()
    original_invoke = backend._invoke
    operations: list[str] = []

    def recording_invoke(
        operation: str,
        invoke_location: GitLocation,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> Any:
        operations.append(operation)
        return original_invoke(operation, invoke_location, args, **kwargs)

    monkeypatch.setattr(backend, "_invoke", recording_invoke)

    backend.stage_all(location)

    assert operations.count("hash_blobs") == 1
    assert operations.count("update_index_paths") == 1
    assert len(operations) <= 6


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_batches_large_worktree_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    for index in range(250):
        (repo / f"file-{index:04d}.txt").write_text(f"content-{index}\n", encoding="utf-8")
    _raw_git(repo, "add", "-A")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    backend = GitBackend()
    original_invoke = backend._invoke
    operations: list[str] = []

    def recording_invoke(
        operation: str,
        invoke_location: GitLocation,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> Any:
        operations.append(operation)
        return original_invoke(operation, invoke_location, args, **kwargs)

    monkeypatch.setattr(backend, "_invoke", recording_invoke)
    destination = tmp_path / "worktree"

    backend.create_branch_worktree(
        GitLocation(repo),
        destination,
        branch="universe/materialized",
        start_ref="HEAD",
    )

    assert (destination / "file-0249.txt").read_text(encoding="utf-8") == "content-249\n"
    assert operations.count("read_blob_sizes") == 1
    assert operations.count("read_blobs") == 1
    assert len(operations) <= 7


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_uses_repository_hash_width_when_staging_deletion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init = subprocess.run(
        [_TRUSTED_GIT, "init", "--object-format=sha256", "-q"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    tracked = repo / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    _raw_git(repo, "add", "--", "tracked.txt")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    tracked.unlink()
    backend = GitBackend()
    location = GitLocation(repo)

    backend.stage_all(location)
    backend.commit(
        location,
        "delete tracked",
        identity=GitIdentity("Safe Git", "safe@example.invalid"),
    )

    assert "tracked.txt" not in _raw_git(repo, "ls-tree", "--name-only", "HEAD")


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_first_commit_uses_compare_and_swap_when_ref_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    backend = GitBackend()
    location = GitLocation(repo)
    backend.init_repository(location)
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    backend.stage_all(location)
    original_invoke = backend._invoke
    competing_sha: str | None = None

    def inject_competing_commit(
        operation: str,
        invoke_location: GitLocation,
        args: tuple[str, ...],
        **kwargs: object,
    ) -> Any:
        nonlocal competing_sha
        if operation == "commit.update_ref":
            tree_sha = _raw_git(repo, "write-tree")
            competing_sha = _raw_git(
                repo,
                "-c",
                "user.name=Competing Writer",
                "-c",
                "user.email=competing@example.invalid",
                "commit-tree",
                tree_sha,
                "-m",
                "competing first commit",
            )
            _raw_git(repo, "update-ref", "HEAD", competing_sha)
        return original_invoke(operation, invoke_location, args, **kwargs)

    monkeypatch.setattr(backend, "_invoke", inject_competing_commit)

    with pytest.raises(GitBackendError, match="cannot lock ref|reference already exists"):
        backend.commit(
            location,
            "our first commit",
            identity=GitIdentity("Safe Git", "safe@example.invalid"),
        )

    assert competing_sha is not None
    assert _raw_git(repo, "rev-parse", "HEAD") == competing_sha


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_rejects_oversized_blob_before_materializing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _raw_git(repo, "init", "-q")
    (repo / "large.bin").write_bytes(b"x" * 64)
    _raw_git(repo, "add", "-A")
    _raw_git(
        repo,
        "-c",
        "user.name=Initial",
        "-c",
        "user.email=initial@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    backend = GitBackend(blob_limit_bytes=16, materialization_limit_bytes=64)

    with pytest.raises(GitBackendError, match="blob exceeds 16 byte limit"):
        backend.create_branch_worktree(
            GitLocation(repo),
            tmp_path / "worktree",
            branch="universe/oversized",
            start_ref="HEAD",
        )


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_fails_closed_on_symlink_parent_escape(tmp_path: Path) -> None:
    backend = GitBackend()
    identity = GitIdentity("Path Test", "path@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    location = GitLocation(repo)
    backend.init_repository(location)
    nested = repo / "nested"
    nested.mkdir()
    (nested / "tracked.txt").write_text("inside\n", encoding="utf-8")
    backend.stage_all(location)
    backend.commit(location, "base", identity=identity)
    shutil.rmtree(nested)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "tracked.txt"
    outside_file.write_text("outside\n", encoding="utf-8")
    nested.symlink_to(outside, target_is_directory=True)

    with pytest.raises(GitBackendError, match="symlink parent"):
        backend.stage_paths(location, ("nested/tracked.txt",))

    assert outside_file.read_text(encoding="utf-8") == "outside\n"


@pytest.mark.skipif(_TRUSTED_GIT is None, reason="trusted system git is unavailable")
def test_git_backend_pins_worktree_against_repo_core_worktree_redirect(tmp_path: Path) -> None:
    backend = GitBackend()
    identity = GitIdentity("Path Test", "path@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    location = GitLocation(repo)
    backend.init_repository(location)
    tracked = repo / "tracked.txt"
    tracked.write_text("inside\n", encoding="utf-8")
    backend.stage_all(location)
    snapshot = backend.commit(location, "base", identity=identity)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("must survive\n", encoding="utf-8")
    _raw_git(repo, "config", "core.worktree", str(outside))

    backend.restore_snapshot(location, snapshot, identity=identity)

    assert victim.read_text(encoding="utf-8") == "must survive\n"
    assert tracked.read_text(encoding="utf-8") == "inside\n"


def test_git_backend_named_repository_and_worktree_lifecycle(tmp_path: Path) -> None:
    backend = GitBackend()
    identity = GitIdentity("Test User", "test@example.invalid")
    repo = tmp_path / "repo"
    repo.mkdir()
    location = GitLocation(repo)

    backend.init_repository(location)
    (repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
    backend.stage_all(location)
    first = backend.commit(location, "initial", identity=identity)

    assert backend.resolve_revision(location, "HEAD") == first
    assert backend.commit_timestamp(location, first)
    log = backend.list_log(location)
    assert log == (
        GitLogEntry(
            sha=first, subject="initial", committed_at=backend.commit_timestamp(location, first)
        ),
    )

    (repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
    assert "-v1" in backend.diff(location, first)
    worktree = tmp_path / "worktree"
    backend.create_branch_worktree(
        location,
        worktree,
        branch="universe/test",
        start_ref=first,
    )
    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "v1\n"
    backend.remove_worktree(location, worktree, force=True)
    backend.delete_branch(location, "universe/test", force=True)
    assert not worktree.exists()


def test_git_backend_preserves_independent_git_dir_snapshot_semantics(tmp_path: Path) -> None:
    backend = GitBackend()
    identity = GitIdentity("Tianshu Shadow", "shadow@tianshu.local")
    work_tree = tmp_path / "work"
    work_tree.mkdir()
    git_dir = tmp_path / "shadow" / "gitdir"
    location = GitLocation(work_tree, git_dir=git_dir)

    backend.init_repository(location)
    (work_tree / "a.txt").write_text("v1", encoding="utf-8")
    backend.stage_all(location)
    first = backend.commit(location, "first", identity=identity, allow_empty=True)
    (work_tree / "a.txt").write_text("v2", encoding="utf-8")
    (work_tree / "later.txt").write_text("later", encoding="utf-8")
    backend.stage_all(location)
    backend.commit(location, "second", identity=identity, allow_empty=True)

    restored = backend.restore_snapshot(location, first, identity=identity)

    assert restored != first
    assert (work_tree / "a.txt").read_text(encoding="utf-8") == "v1"
    assert not (work_tree / "later.txt").exists()
    assert len(backend.list_log(location)) == 3
