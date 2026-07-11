"""Canonical server-side change capture from a staging lease."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from tianshu.executor.git_backend import GitBackend, GitBackendError
from tianshu.executor.workspace_service import (
    WorkspaceConflict,
    WorkspaceLeaseRequest,
    WorkspaceService,
)

_GIT = shutil.which("git", path=os.defpath)


def _git(repo: Path, *args: str) -> bytes:
    assert _GIT is not None
    return subprocess.run([_GIT, *args], cwd=repo, check=True, capture_output=True).stdout


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    files = {
        "modify.txt": b"before\n",
        "delete.txt": b"delete\n",
        "rename.txt": b"rename exact\n",
        "copy-source.txt": b"copy exact\n",
        "mode.txt": b"mode\n",
        "binary.bin": b"old\x00binary",
        "nested/file.txt": b"nested\n",
    }
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if hasattr(os, "symlink"):
        (path / "link").symlink_to("modify.txt")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return path


def _loose_objects(repo: Path) -> set[str]:
    objects = repo / ".git" / "objects"
    return {
        path.relative_to(objects).as_posix()
        for path in objects.rglob("*")
        if path.is_file() and len(path.parent.name) == 2
    }


async def _lease(storage, tmp_path: Path):
    repo = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    lease = await service.create_lease(
        WorkspaceLeaseRequest(
            run_id="run-1",
            lineage_root_run_id="run-1",
            source_root=repo,
            base_revision="HEAD",
            apply_mode="governed",
        )
    )
    return repo, service, lease


@pytest.mark.asyncio
async def test_canonical_changes_cover_stable_git_kinds_and_metadata(
    storage, tmp_path: Path
) -> None:
    repo, service, lease = await _lease(storage, tmp_path)
    staging = Path(lease.staging_root)
    source_before = _git(repo, "status", "--porcelain=v1", "-z")
    (staging / "modify.txt").write_text("after\n")
    (staging / "delete.txt").unlink()
    (staging / "rename.txt").rename(staging / "renamed.txt")
    shutil.copyfile(staging / "copy-source.txt", staging / "copied.txt")
    (staging / "mode.txt").chmod(0o755)
    (staging / "binary.bin").write_bytes(b"new\x00binary")
    if (staging / "link").is_symlink():
        (staging / "link").unlink()
        (staging / "link").symlink_to("mode.txt")
    (staging / "added.txt").write_text("already staged\n")
    _git(staging, "add", "added.txt")
    (staging / "untracked.txt").write_text("captured by service\n")

    change_set = await service.capture_change_set(lease.id, run_id="run-1")

    by_path = {change.new_path or change.old_path: change for change in change_set.changes}
    assert by_path["added.txt"].kind == "add"
    assert by_path["untracked.txt"].kind == "untracked"
    assert by_path["modify.txt"].kind == "modify"
    assert by_path["delete.txt"].kind == "delete"
    assert by_path["renamed.txt"].kind == "rename"
    assert by_path["renamed.txt"].old_path == "rename.txt"
    assert by_path["copied.txt"].kind == "copy"
    assert by_path["copied.txt"].old_path == "copy-source.txt"
    assert by_path["mode.txt"].kind == "mode"
    assert by_path["mode.txt"].old_oid == by_path["mode.txt"].new_oid
    assert by_path["binary.bin"].binary is True
    if "link" in by_path:
        assert by_path["link"].new_mode == "120000"
    assert all(change.old_oid or change.new_oid for change in change_set.changes)
    assert tuple(change.sort_key() for change in change_set.changes) == tuple(
        sorted(change.sort_key() for change in change_set.changes)
    )
    assert len(change_set.content_hash) == 64
    assert storage.get_canonical_change_set(change_set.id) == change_set
    assert _git(repo, "status", "--porcelain=v1", "-z") == source_before
    await service.shutdown()


@pytest.mark.asyncio
async def test_empty_and_recomputed_change_sets_have_one_stable_hash(
    storage, tmp_path: Path
) -> None:
    _repo, service, lease = await _lease(storage, tmp_path)

    first = await service.capture_change_set(lease.id, run_id="run-1")
    second = await service.capture_change_set(lease.id, run_id="run-1")

    assert first.changes == ()
    assert second.id == first.id
    assert second.content_hash == first.content_hash
    await service.shutdown()


@pytest.mark.asyncio
async def test_change_capture_keeps_secret_blobs_out_of_source_object_database(
    storage, tmp_path: Path
) -> None:
    repo, service, lease = await _lease(storage, tmp_path)
    secret = b"unique secret that must stay inside the staging lease\n"
    (Path(lease.staging_root) / "secret.txt").write_bytes(secret)
    assert _GIT is not None
    oid = (
        subprocess.run(
            [_GIT, "hash-object", "--stdin"],
            cwd=repo,
            input=secret,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    before = _loose_objects(repo)
    assert (
        subprocess.run([_GIT, "cat-file", "-e", oid], cwd=repo, capture_output=True).returncode != 0
    )

    change_set = await service.capture_change_set(lease.id, run_id="run-1")

    assert change_set.changes[0].new_oid == oid
    assert _loose_objects(repo) == before
    assert (
        subprocess.run([_GIT, "cat-file", "-e", oid], cwd=repo, capture_output=True).returncode != 0
    )
    await service.shutdown()
    assert _loose_objects(repo) == before
    assert (
        subprocess.run([_GIT, "cat-file", "-e", oid], cwd=repo, capture_output=True).returncode != 0
    )
    fsck = subprocess.run(
        [_GIT, "fsck", "--no-reflogs", "--unreachable"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    assert oid.encode() not in fsck.stdout


@pytest.mark.asyncio
async def test_change_capture_rejects_symlink_parent_escape(storage, tmp_path: Path) -> None:
    _repo, service, lease = await _lease(storage, tmp_path)
    staging = Path(lease.staging_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.rmtree(staging / "nested")
    (staging / "nested").symlink_to(outside, target_is_directory=True)
    (outside / "file.txt").write_text("escape")

    with pytest.raises(GitBackendError, match="symlink parent"):
        await service.capture_change_set(lease.id, run_id="run-1")

    assert (outside / "file.txt").read_text() == "escape"
    (staging / "nested").unlink()
    await service.shutdown()


@pytest.mark.asyncio
async def test_change_capture_rejects_replaced_staging_repository(storage, tmp_path: Path) -> None:
    repo, service, lease = await _lease(storage, tmp_path)
    staging = Path(lease.staging_root)
    shutil.rmtree(staging)
    _git(repo, "worktree", "prune", "--expire", "now")
    _repository(staging)

    with pytest.raises(WorkspaceConflict, match="staging.*identity"):
        await service.capture_change_set(lease.id, run_id="run-1")

    shutil.rmtree(staging)
    await service.close_lease(lease.id, run_id="run-1")


@pytest.mark.asyncio
async def test_change_capture_rejects_same_repository_worktree_admin_swap(
    storage, tmp_path: Path
) -> None:
    repo, service, first = await _lease(storage, tmp_path)
    second = await service.create_lease(
        WorkspaceLeaseRequest(
            run_id="run-2",
            lineage_root_run_id="run-1",
            parent_run_id="run-1",
            attempt=1,
            source_root=repo,
            base_revision="HEAD",
            apply_mode="governed",
        )
    )
    first_git = Path(first.staging_root) / ".git"
    second_git = Path(second.staging_root) / ".git"
    first_authority = first_git.read_bytes()
    second_authority = second_git.read_bytes()
    first_git.write_bytes(second_authority)
    second_git.write_bytes(first_authority)

    with pytest.raises(WorkspaceConflict, match="staging.*identity"):
        await service.capture_change_set(first.id, run_id="run-1")

    first_git.write_bytes(first_authority)
    second_git.write_bytes(second_authority)
    await service.shutdown()


@pytest.mark.asyncio
async def test_capture_serializes_with_close_and_concurrent_recompute(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, service, lease = await _lease(storage, tmp_path)
    staging = Path(lease.staging_root)
    (staging / "new.txt").write_text("new")
    backend = service._git  # noqa: SLF001 - lifecycle serialization fixture
    real_capture = backend.capture_staged_changes
    real_remove = backend.remove_worktree
    capture_entered = threading.Event()
    capture_release = threading.Event()
    remove_entered = threading.Event()

    def blocked_capture(location, base_revision):
        capture_entered.set()
        assert capture_release.wait(timeout=5)
        return real_capture(location, base_revision)

    def observed_remove(
        location,
        destination,
        *,
        force=False,
        expected_git_dir=None,
        expected_git_dir_identity=None,
    ):
        remove_entered.set()
        return real_remove(
            location,
            destination,
            force=force,
            expected_git_dir=expected_git_dir,
            expected_git_dir_identity=expected_git_dir_identity,
        )

    monkeypatch.setattr(backend, "capture_staged_changes", blocked_capture)
    monkeypatch.setattr(backend, "remove_worktree", observed_remove)
    capturing = asyncio.create_task(service.capture_change_set(lease.id, run_id="run-1"))
    assert await asyncio.to_thread(capture_entered.wait, 2)
    closing = asyncio.create_task(service.close_lease(lease.id, run_id="run-1"))
    await asyncio.sleep(0.05)
    assert not remove_entered.is_set()
    capture_release.set()
    first = await capturing
    await closing
    assert first.changes

    # A fresh lease proves same-content concurrent recompute is idempotent.
    second_request = WorkspaceLeaseRequest(
        run_id="run-2",
        lineage_root_run_id="run-1",
        parent_run_id="run-1",
        attempt=1,
        source_root=Path(lease.source_root),
        base_revision="HEAD",
        apply_mode="governed",
    )
    second_lease = await service.create_lease(second_request)
    second_staging = Path(second_lease.staging_root)
    (second_staging / "same.txt").write_text("same")
    monkeypatch.setattr(backend, "capture_staged_changes", real_capture)
    results = await asyncio.gather(
        service.capture_change_set(second_lease.id, run_id="run-2"),
        service.capture_change_set(second_lease.id, run_id="run-2"),
    )
    assert results[0].id == results[1].id
    assert results[0].content_hash == results[1].content_hash
    await service.shutdown()


@pytest.mark.asyncio
async def test_non_utf8_untracked_name_is_canonical_when_platform_supports_it(
    storage, tmp_path: Path
) -> None:
    _repo, service, lease = await _lease(storage, tmp_path)
    staging = Path(lease.staging_root)
    raw_name = b"non-utf8-\xff.txt"
    raw_path = os.fsencode(staging) + b"/" + raw_name
    try:
        descriptor = os.open(raw_path, os.O_CREAT | os.O_WRONLY, 0o600)
    except (OSError, UnicodeError):
        pytest.skip("filesystem does not support non-UTF8 fixture names")
    os.write(descriptor, b"raw")
    os.close(descriptor)

    change_set = await service.capture_change_set(lease.id, run_id="run-1")

    assert len(change_set.changes) == 1
    assert os.fsencode(change_set.changes[0].new_path) == raw_name
    assert "\\udcff" in change_set.canonical_json()
    await service.shutdown()
