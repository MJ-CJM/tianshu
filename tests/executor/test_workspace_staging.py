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
from tianshu.governance.decision_service import DecisionService
from tianshu.models.workspace import WorkspaceLeaseState

_GIT = shutil.which("git", path=os.defpath)


def _workspace_service(storage, git_backend, staging_root) -> WorkspaceService:
    return WorkspaceService(storage, git_backend, staging_root, DecisionService(storage))


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
        parent_run_id=None if run_id == "run-1" else "run-1",
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
    service = _workspace_service(
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
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")

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
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
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
async def test_same_run_replay_rejects_source_ref_drift_at_same_commit(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    request = _request(repo)
    await service.create_lease(request)
    _git(repo, "branch", "same-commit")
    _git(repo, "switch", "-q", "same-commit")

    with pytest.raises(WorkspaceSourceError, match="drift"):
        await service.create_lease(request)

    await service.shutdown()


@pytest.mark.asyncio
async def test_source_git_admin_swap_fails_capture_replay_and_close_closed(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    request = _request(repo)
    lease = await service.create_lease(request)
    git_dir = repo / ".git"
    swapped_git_dir = repo / ".git-swapped"
    git_dir.rename(swapped_git_dir)
    git_dir.symlink_to(swapped_git_dir, target_is_directory=True)

    with pytest.raises(WorkspaceConflict, match="staging.*identity"):
        await service.capture_change_set(lease.id, run_id="run-1")
    with pytest.raises(WorkspaceSourceError, match="drift"):
        await service.create_lease(request)
    with pytest.raises(WorkspaceSourceError, match="drift"):
        await service.close_lease(lease.id, run_id="run-1")
    failed = storage.get_workspace_lease(lease.id)
    assert failed is not None and failed.state is WorkspaceLeaseState.CLEANUP_FAILED

    git_dir.unlink()
    swapped_git_dir.rename(git_dir)
    closed = await service.close_lease(lease.id, run_id="run-1")
    assert closed.state is WorkspaceLeaseState.CLOSED


@pytest.mark.asyncio
async def test_governed_git_rejects_dirty_source_symlink_and_missing_base(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    (repo / "untracked.txt").write_text("dirty")
    with pytest.raises(WorkspaceSourceError, match="clean"):
        await service.create_lease(_request(repo))
    (repo / "untracked.txt").unlink()

    symlink = tmp_path / "source-link"
    symlink.symlink_to(repo, target_is_directory=True)
    with pytest.raises(WorkspaceSourceError, match="symlink"):
        await service.create_lease(_request(symlink))

    invalid = _request(repo).model_copy(update={"base_revision": None})
    with pytest.raises(ValueError, match="base revision"):
        await service.create_lease(invalid)
    assert storage.list_open_workspace_leases() == ()


@pytest.mark.asyncio
async def test_scratch_lease_is_none_apply_mode_and_has_no_restore_point(
    storage, tmp_path: Path
) -> None:
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
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
    service = _workspace_service(storage, backend, tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    real_remove = backend.remove_worktree
    attempts = 0

    def flaky_remove(
        location: GitLocation,
        destination: Path,
        *,
        force: bool = False,
        expected_git_dir: Path | None = None,
        expected_git_dir_identity: str | None = None,
    ) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected cleanup failure")
        real_remove(
            location,
            destination,
            force=force,
            expected_git_dir=expected_git_dir,
            expected_git_dir_identity=expected_git_dir_identity,
        )

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
    service = _workspace_service(storage, backend, tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    entered = threading.Event()
    release = threading.Event()
    real_remove = backend.remove_worktree

    def blocked_remove(
        location: GitLocation,
        destination: Path,
        *,
        force: bool = False,
        expected_git_dir: Path | None = None,
        expected_git_dir_identity: str | None = None,
    ) -> None:
        entered.set()
        assert release.wait(timeout=5)
        real_remove(
            location,
            destination,
            force=force,
            expected_git_dir=expected_git_dir,
            expected_git_dir_identity=expected_git_dir_identity,
        )

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
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = _workspace_service(storage, backend, tmp_path / "leases")
    lease = await service.create_lease(_request(repo))
    staging = Path(lease.staging_root)
    shutil.rmtree(staging)
    assert os.fsencode(staging) in _git(repo, "worktree", "list", "--porcelain", "-z")
    observed_authority: tuple[Path | None, str | None] | None = None
    real_remove = backend.remove_worktree

    def observe_remove(
        location: GitLocation,
        destination: Path,
        *,
        force: bool = False,
        expected_git_dir: Path | None = None,
        expected_git_dir_identity: str | None = None,
    ) -> None:
        nonlocal observed_authority
        observed_authority = (expected_git_dir, expected_git_dir_identity)
        real_remove(
            location,
            destination,
            force=force,
            expected_git_dir=expected_git_dir,
            expected_git_dir_identity=expected_git_dir_identity,
        )

    monkeypatch.setattr(backend, "remove_worktree", observe_remove)

    await service.close_lease(lease.id, run_id="run-1")

    assert observed_authority == (Path(lease.staging_git_dir or ""), lease.staging_git_dir_identity)
    assert os.fsencode(staging) not in _git(repo, "worktree", "list", "--porcelain", "-z")
    assert storage.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.asyncio
async def test_close_rejects_symlink_replacement_without_removing_another_lease(
    storage, tmp_path: Path
) -> None:
    repo = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    first = await service.create_lease(_request(repo, "run-1"))
    second = await service.create_lease(_request(repo, "run-2"))
    first_root = Path(first.staging_root)
    second_root = Path(second.staging_root)
    shutil.rmtree(first_root)
    first_root.symlink_to(second_root, target_is_directory=True)

    with pytest.raises(WorkspaceSourceError, match="symlink"):
        await service.close_lease(first.id, run_id="run-1")

    assert second_root.is_dir()
    assert storage.get_workspace_lease(first.id).state is WorkspaceLeaseState.CLEANUP_FAILED
    assert storage.get_workspace_lease(second.id).state is WorkspaceLeaseState.ACTIVE

    first_root.unlink()
    closed = await service.close_lease(first.id, run_id="run-1")
    assert closed.state is WorkspaceLeaseState.CLOSED
    assert second_root.is_dir()
    await service.shutdown()


@pytest.mark.asyncio
async def test_startup_failure_after_worktree_creation_is_cleaned_and_recorded(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = _workspace_service(storage, backend, tmp_path / "leases")
    real_create = backend.create_detached_worktree

    def fail_after_create(location: GitLocation, destination: Path, *, start_ref: str) -> None:
        starting = storage.get_workspace_lease_by_run("run-1")
        assert starting is not None
        assert starting.state is WorkspaceLeaseState.STARTING
        assert starting.staging_git_dir is None
        real_create(location, destination, start_ref=start_ref)
        raise RuntimeError("injected startup failure")

    monkeypatch.setattr(backend, "create_detached_worktree", fail_after_create)
    with pytest.raises(RuntimeError, match="startup failure"):
        await service.create_lease(_request(repo))

    lease = storage.get_workspace_lease_by_run("run-1")
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    assert not Path(lease.staging_root).exists()


@pytest.mark.asyncio
async def test_pre_authority_startup_cleanup_failure_is_readable_and_fails_closed(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = _workspace_service(storage, backend, tmp_path / "leases")
    real_inspect = backend.inspect_repository

    def fail_staging_inspection(location: GitLocation):
        if location.work_tree.parent == (tmp_path / "leases").resolve():
            raise RuntimeError("injected pre-authority inspection failure")
        return real_inspect(location)

    monkeypatch.setattr(backend, "inspect_repository", fail_staging_inspection)
    with pytest.raises(RuntimeError, match="pre-authority inspection failure"):
        await service.create_lease(_request(repo))

    failed = storage.get_workspace_lease_by_run("run-1")
    assert failed is not None
    assert failed.state is WorkspaceLeaseState.CLEANUP_FAILED
    assert failed.staging_git_dir is None
    assert storage.list_open_workspace_leases() == (failed,)

    staging = Path(failed.staging_root)
    backend.remove_worktree(GitLocation(repo), staging, force=True)
    victim = tmp_path / "other-lease"
    victim.mkdir()
    (victim / "proof.txt").write_text("owned elsewhere")
    staging.symlink_to(victim, target_is_directory=True)

    with pytest.raises(WorkspaceConflict, match="authority is unavailable"):
        await service.close_lease(failed.id, run_id="run-1")
    assert (victim / "proof.txt").read_text() == "owned elsewhere"

    staging.unlink()
    closed = await service.close_lease(failed.id, run_id="run-1")
    assert closed.state is WorkspaceLeaseState.CLOSED


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_start_and_waits_for_cleanup(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = _workspace_service(storage, backend, tmp_path / "leases")
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


@pytest.mark.asyncio
async def test_shutdown_cancels_same_run_replay_and_never_returns_stale_active_lease(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path / "source")
    backend = GitBackend()
    service = _workspace_service(storage, backend, tmp_path / "leases")
    request = _request(repo)
    lease = await service.create_lease(request)
    entered = threading.Event()
    release = threading.Event()
    real_inspect = backend.inspect_repository

    def blocked_replay_inspect(location: GitLocation):
        if location.work_tree == repo.resolve():
            entered.set()
            assert release.wait(timeout=5)
        return real_inspect(location)

    monkeypatch.setattr(backend, "inspect_repository", blocked_replay_inspect)
    replaying = asyncio.create_task(service.create_lease(request))
    assert await asyncio.to_thread(entered.wait, 2)
    shutdown = asyncio.create_task(service.shutdown())
    await asyncio.sleep(0.05)
    assert not shutdown.done()
    release.set()
    await shutdown

    with pytest.raises(asyncio.CancelledError):
        await replaying
    current = storage.get_workspace_lease(lease.id)
    assert current is not None and current.state is WorkspaceLeaseState.CLOSED
    assert not Path(lease.staging_root).exists()


@pytest.mark.asyncio
async def test_retry_requires_existing_contiguous_parent_lineage(storage, tmp_path: Path) -> None:
    repo = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    orphan = WorkspaceLeaseRequest(
        run_id="run-2",
        lineage_root_run_id="run-1",
        parent_run_id="missing-parent",
        attempt=1,
        source_root=repo,
        base_revision="HEAD",
        apply_mode="governed",
    )

    with pytest.raises(WorkspaceConflict, match="parent"):
        await service.create_lease(orphan)

    assert storage.get_workspace_lease_by_run("run-2") is None
