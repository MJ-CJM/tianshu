"""Detached staging lease lifecycle and source-workspace invariants."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tianshu.executor.git_backend import GitBackend, GitLocation
from tianshu.executor.workspace_service import (
    WorkspaceConflict,
    WorkspaceLeaseRequest,
    WorkspaceService,
    WorkspaceSourceError,
)
from tianshu.models.workspace import WorkspaceLeaseState

_GIT = shutil.which("git", path=os.defpath)


def _git(repo: Path, *args: str) -> bytes:
    assert _GIT is not None
    return subprocess.run(
        [_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    (path / "tracked.txt").write_text("base\n")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-qm", "base")
    return path


def _source_snapshot(
    repo: Path,
) -> tuple[bytes, bytes, bytes, bytes, tuple[tuple[str, int, bytes], ...]]:
    worktree: list[tuple[str, int, bytes]] = []
    for path in sorted(
        (candidate for candidate in repo.rglob("*") if ".git" not in candidate.parts),
        key=lambda candidate: os.fsencode(candidate.relative_to(repo)),
    ):
        relative = path.relative_to(repo).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            payload = os.fsencode(os.readlink(path))
        elif path.is_file():
            payload = path.read_bytes()
        else:
            payload = b""
        worktree.append((relative, metadata.st_mode, payload))
    return (
        _git(repo, "rev-parse", "HEAD"),
        _git(repo, "for-each-ref", "--format=%(refname)%00%(objectname)"),
        (repo / ".git" / "index").read_bytes(),
        _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        tuple(worktree),
    )


def _request(repo: Path, run_id: str = "run-1") -> WorkspaceLeaseRequest:
    return WorkspaceLeaseRequest(
        run_id=run_id,
        lineage_root_run_id="run-1",
        attempt=0 if run_id == "run-1" else 1,
        source_root=repo,
        base_revision="HEAD",
        apply_mode="governed",
    )


@pytest.mark.asyncio
async def test_git_lease_uses_detached_worktree_and_preserves_source(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    before = _source_snapshot(repo)
    service = WorkspaceService(
        storage=storage,
        git_backend=GitBackend(),
        staging_root=tmp_path / "leases",
    )

    lease = await service.create_lease(_request(repo))

    staging = Path(lease.staging_root)
    assert lease.state is WorkspaceLeaseState.ACTIVE
    assert staging.is_dir()
    assert _git(staging, "rev-parse", "--abbrev-ref", "HEAD").strip() == b"HEAD"
    assert _git(staging, "rev-parse", "HEAD").decode().strip() == lease.base_revision
    assert (staging / "tracked.txt").read_text() == "base\n"
    restore = storage.get_restore_point_for_lease(lease.id)
    assert restore is not None
    assert restore.base_revision == lease.base_revision
    assert _source_snapshot(repo) == before

    await service.close_lease(lease.id, run_id="run-1")
    assert not staging.exists()
    assert storage.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED
    assert _source_snapshot(repo) == before
    await service.close_lease(lease.id, run_id="run-1")


@pytest.mark.asyncio
async def test_each_run_gets_a_distinct_lease_and_same_run_replay_is_idempotent(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")

    first = await service.create_lease(_request(repo, "run-1"))
    replay = await service.create_lease(_request(repo, "run-1"))
    retry = await service.create_lease(_request(repo, "run-2"))

    assert replay == first
    assert retry.id != first.id
    assert retry.staging_root != first.staging_root
    with pytest.raises(WorkspaceConflict, match="belongs to another run"):
        await service.capture_change_set(first.id, run_id="run-2")
    await service.shutdown()


@pytest.mark.asyncio
async def test_same_run_replay_binds_base_and_rejects_source_symlink_replacement(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    first_base = _git(repo, "rev-parse", "HEAD").decode().strip()
    (repo / "tracked.txt").write_text("second\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "second")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    original = _request(repo).model_copy(update={"base_revision": first_base})
    await service.create_lease(original)

    with pytest.raises(WorkspaceConflict, match="base"):
        await service.create_lease(_request(repo))

    real_repo = tmp_path / "source-real"
    repo.rename(real_repo)
    repo.symlink_to(real_repo, target_is_directory=True)
    with pytest.raises(WorkspaceSourceError, match="symlink"):
        await service.create_lease(original)
    repo.unlink()
    real_repo.rename(repo)
    await service.shutdown()


@pytest.mark.asyncio
async def test_governed_git_rejects_dirty_source_symlink_and_missing_base(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    (repo / "untracked.txt").write_text("dirty")
    with pytest.raises(WorkspaceSourceError, match="clean"):
        await service.create_lease(_request(repo))
    (repo / "untracked.txt").unlink()

    symlink = tmp_path / "source-link"
    symlink.symlink_to(repo, target_is_directory=True)
    with pytest.raises(WorkspaceSourceError, match="symlink"):
        await service.create_lease(_request(symlink, "run-2"))

    invalid = _request(repo, "run-3").model_copy(update={"base_revision": None})
    with pytest.raises(ValueError, match="base revision"):
        await service.create_lease(invalid)
    assert storage.list_open_workspace_leases() == ()


@pytest.mark.asyncio
async def test_scratch_lease_is_none_apply_mode_and_has_no_restore_point(
    storage, tmp_path: Path
) -> None:
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    request = WorkspaceLeaseRequest(
        run_id="scratch-run",
        lineage_root_run_id="scratch-run",
        source_root=None,
        base_revision=None,
        apply_mode="none",
    )

    lease = await service.create_lease(request)

    assert lease.source_kind == "scratch"
    assert Path(lease.staging_root).is_dir()
    assert storage.get_restore_point_for_lease(lease.id) is None
    await service.close_lease(lease.id, run_id="scratch-run")


@pytest.mark.asyncio
async def test_cleanup_failure_is_retryable_and_does_not_forget_active_lease(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = WorkspaceService(storage, backend, tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    real_remove = backend.remove_worktree
    attempts = 0

    def flaky_remove(location: GitLocation, destination: Path, *, force: bool = False) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected cleanup failure")
        real_remove(location, destination, force=force)

    monkeypatch.setattr(backend, "remove_worktree", flaky_remove)
    with pytest.raises(OSError, match="cleanup failure"):
        await service.close_lease(lease.id, run_id="run-1")
    failed = storage.get_workspace_lease(lease.id)
    assert failed is not None and failed.state is WorkspaceLeaseState.CLEANUP_FAILED

    await service.close_lease(lease.id, run_id="run-1")
    assert attempts == 2
    assert storage.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.asyncio
async def test_close_cancellation_after_remove_records_closed_state(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = WorkspaceService(storage, backend, tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    entered = threading.Event()
    release = threading.Event()
    real_remove = backend.remove_worktree

    def blocked_remove(location: GitLocation, destination: Path, *, force: bool = False) -> None:
        entered.set()
        assert release.wait(timeout=5)
        real_remove(location, destination, force=force)

    monkeypatch.setattr(backend, "remove_worktree", blocked_remove)
    closing = asyncio.create_task(service.close_lease(lease.id, run_id="run-1"))
    assert await asyncio.to_thread(entered.wait, 2)
    closing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing

    assert storage.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED
    assert not Path(lease.staging_root).exists()
    await service.close_lease(lease.id, run_id="run-1")


@pytest.mark.asyncio
async def test_close_prunes_git_metadata_when_worktree_path_was_deleted(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    staging = Path(lease.staging_root)
    shutil.rmtree(staging)
    assert os.fsencode(staging) in _git(repo, "worktree", "list", "--porcelain", "-z")

    await service.close_lease(lease.id, run_id="run-1")

    assert os.fsencode(staging) not in _git(repo, "worktree", "list", "--porcelain", "-z")
    assert storage.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.asyncio
async def test_startup_failure_after_worktree_creation_is_cleaned_and_recorded(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = WorkspaceService(storage, backend, tmp_path / "leases")
    real_create = backend.create_detached_worktree

    def fail_after_create(location: GitLocation, destination: Path, *, start_ref: str) -> None:
        real_create(location, destination, start_ref=start_ref)
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(backend, "create_detached_worktree", fail_after_create)
    with pytest.raises(RuntimeError, match="startup failure"):
        await service.create_lease(_request(repo))

    lease = storage.get_workspace_lease_by_run("run-1")
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    assert not Path(lease.staging_root).exists()


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_start_and_waits_for_cleanup(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = WorkspaceService(storage, backend, tmp_path / "leases")
    entered = threading.Event()
    release = threading.Event()
    real_create = backend.create_detached_worktree

    def blocked_create(location: GitLocation, destination: Path, *, start_ref: str) -> None:
        entered.set()
        assert release.wait(timeout=5)
        real_create(location, destination, start_ref=start_ref)

    monkeypatch.setattr(backend, "create_detached_worktree", blocked_create)
    starting = asyncio.create_task(service.create_lease(_request(repo)))
    assert await asyncio.to_thread(entered.wait, 2)
    shutdown = asyncio.create_task(service.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    await shutdown

    with pytest.raises(asyncio.CancelledError):
        await starting
    lease = storage.get_workspace_lease_by_run("run-1")
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    assert not Path(lease.staging_root).exists()
