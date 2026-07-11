"""Detached staging leases for governed executions.

Git worktrees isolate source mutations for cooperating callers.  They are not a
strong sandbox: same-UID absolute-path writes, path-swap TOCTOU between checks,
and external side effects remain outside this G1.4a boundary.  Runtime lease
binding and any stronger dirfd/sandbox boundary belong to G1.4b.
"""

from __future__ import annotations

import asyncio
import shutil
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.executor.git_backend import GitBackend, GitLocation, GitRepositorySnapshot
from tianshu.models.workspace import (
    CanonicalChange,
    CanonicalChangeSet,
    RestorePoint,
    WorkspaceLease,
    WorkspaceLeaseState,
    WorkspaceStagingIdentity,
)

if TYPE_CHECKING:
    from tianshu.storage import Storage


class WorkspaceError(RuntimeError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


class WorkspaceSourceError(WorkspaceError):
    pass


class WorkspaceLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1, max_length=256)
    lineage_root_run_id: str = Field(min_length=1, max_length=256)
    parent_run_id: str | None = Field(default=None, max_length=256)
    attempt: int = Field(default=0, ge=0)
    source_root: Path | None
    base_revision: str | None
    apply_mode: Literal["governed", "none"]

    @model_validator(mode="after")
    def validate_source(self) -> WorkspaceLeaseRequest:
        if self.source_root is None:
            if self.base_revision is not None or self.apply_mode != "none":
                raise ValueError("scratch workspaces require apply_mode=none and no base revision")
        elif self.base_revision is None or self.apply_mode != "governed":
            raise ValueError("Git workspaces require governed mode and an explicit base revision")
        for value in (self.run_id, self.lineage_root_run_id, self.parent_run_id):
            if value is not None and any(char in value for char in "\x00\r\n"):
                raise ValueError("run lineage identifiers contain control characters")
        if self.attempt == 0:
            if self.parent_run_id is not None or self.lineage_root_run_id != self.run_id:
                raise ValueError("root workspace runs require no parent and self lineage")
        elif self.parent_run_id is None or self.parent_run_id == self.run_id:
            raise ValueError("retry workspace runs require a distinct parent")
        return self


@dataclass(frozen=True)
class _PathIdentity:
    lexical_path: Path
    resolved_path: Path
    device: int
    inode: int


class WorkspaceService:
    def __init__(
        self,
        storage: Storage,
        git_backend: GitBackend,
        staging_root: Path,
    ) -> None:
        raw_root = Path(staging_root).expanduser()
        if raw_root.is_symlink():
            raise WorkspaceSourceError("staging root must not be a symlink")
        raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        resolved_root = raw_root.resolve()
        if not resolved_root.is_dir():
            raise WorkspaceSourceError("staging root must be a directory")
        resolved_root.chmod(0o700)
        self._storage = storage
        self._git = git_backend
        self._staging_root = resolved_root
        self._lifecycle_lock = asyncio.Lock()
        self._lease_locks: dict[str, asyncio.Lock] = {}
        self._starting: set[asyncio.Task[object]] = set()
        self._starting_runs: set[str] = set()
        self._closing = False

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _path_identity(raw_path: Path) -> _PathIdentity:
        lexical = Path(raw_path).expanduser().absolute()
        try:
            metadata = lexical.lstat()
        except OSError as exc:
            raise WorkspaceSourceError(f"source path is unavailable: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise WorkspaceSourceError("source workspace must not be a symlink")
        resolved = lexical.resolve()
        if not resolved.is_dir():
            raise WorkspaceSourceError("source workspace must be a directory")
        current = resolved.stat()
        return _PathIdentity(lexical, resolved, current.st_dev, current.st_ino)

    @staticmethod
    def _revalidate_path(identity: _PathIdentity) -> None:
        try:
            metadata = identity.lexical_path.lstat()
            resolved = identity.lexical_path.resolve()
            current = resolved.stat()
        except OSError as exc:
            raise WorkspaceSourceError(f"source workspace identity changed: {exc}") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or resolved != identity.resolved_path
            or (current.st_dev, current.st_ino) != (identity.device, identity.inode)
        ):
            raise WorkspaceSourceError("source workspace identity changed")

    @staticmethod
    async def _blocking(callable_, /, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(callable_, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
            raise

    async def create_lease(self, request: WorkspaceLeaseRequest) -> WorkspaceLease:
        request = WorkspaceLeaseRequest.model_validate(request.model_dump())
        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise WorkspaceError("workspace creation requires an asyncio task")
        async with self._lifecycle_lock:
            if self._closing:
                raise WorkspaceConflict("workspace service is shutting down")
            if request.run_id in self._starting_runs:
                raise WorkspaceConflict("run id already has an in-flight lease creation")
            self._starting.add(current)
            self._starting_runs.add(request.run_id)
        try:
            existing = self._storage.get_workspace_lease_by_run(request.run_id)
            if existing is not None:
                lock = self._lease_locks.setdefault(existing.id, asyncio.Lock())
                async with lock:
                    return await self._replay_existing(existing.id, request)
            self._validate_lineage(request)
            return await self._create_lease(request)
        finally:
            async with self._lifecycle_lock:
                self._starting.discard(current)
                self._starting_runs.discard(request.run_id)

    def _validate_lineage(self, request: WorkspaceLeaseRequest) -> None:
        if request.attempt == 0:
            return
        assert request.parent_run_id is not None
        parent = self._storage.get_workspace_lease_by_run(request.parent_run_id)
        if (
            parent is None
            or parent.lineage_root_run_id != request.lineage_root_run_id
            or parent.attempt != request.attempt - 1
        ):
            raise WorkspaceConflict("retry parent must exist in the same contiguous lineage")

    def _active_lease(self, lease_id: str) -> WorkspaceLease:
        lease = self._storage.get_workspace_lease(lease_id)
        if lease is None or lease.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceConflict("run id no longer has an active workspace lease")
        return lease

    async def _replay_existing(
        self, lease_id: str, request: WorkspaceLeaseRequest
    ) -> WorkspaceLease:
        existing = self._active_lease(lease_id)
        if existing.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceConflict("run id already has a terminal or incomplete lease")
        common_identity = (
            existing.run_id == request.run_id
            and existing.lineage_root_run_id == request.lineage_root_run_id
            and existing.parent_run_id == request.parent_run_id
            and existing.attempt == request.attempt
            and existing.apply_mode == request.apply_mode
        )
        if not common_identity:
            raise WorkspaceConflict("run id replay does not match the original lease request")
        if request.source_root is None:
            if existing.source_kind != "scratch":
                raise WorkspaceConflict("run id replay changed workspace source kind")
            return self._active_lease(lease_id)
        identity = self._path_identity(request.source_root)
        if existing.source_root != str(identity.resolved_path):
            raise WorkspaceConflict("run id replay changed source workspace")
        location = GitLocation(identity.resolved_path)
        snapshot = await self._blocking(self._git.inspect_repository, location)
        existing = self._active_lease(lease_id)
        assert request.base_revision is not None
        requested_base = await self._blocking(
            self._git.resolve_commit, location, request.base_revision
        )
        existing = self._active_lease(lease_id)
        if requested_base != existing.base_revision:
            raise WorkspaceConflict("run id replay changed base revision")
        restore = self._storage.get_restore_point_for_lease(existing.id)
        if restore is None or not self._valid_source_snapshot(existing, restore, snapshot):
            raise WorkspaceSourceError("source workspace drifted from the existing lease")
        staging = Path(existing.staging_root)
        if staging.is_symlink() or not staging.is_dir():
            raise WorkspaceConflict("staging workspace identity changed")
        staging_snapshot = await self._blocking(self._git.inspect_repository, GitLocation(staging))
        existing = self._active_lease(lease_id)
        if not self._valid_staging_snapshot(existing, staging_snapshot):
            raise WorkspaceConflict("staging workspace identity changed")
        self._revalidate_path(identity)
        current = self._active_lease(lease_id)
        if current.state_version != existing.state_version:
            raise WorkspaceConflict("workspace lease changed during replay")
        return current

    async def _create_lease(self, request: WorkspaceLeaseRequest) -> WorkspaceLease:
        lease_id = uuid4().hex
        destination = self._staging_root / lease_id
        created_at = self._now()
        persisted = False
        source_location: GitLocation | None = None
        starting_lease: WorkspaceLease | None = None
        staging_snapshot: GitRepositorySnapshot | None = None
        try:
            if request.source_root is None:
                lease = WorkspaceLease(
                    id=lease_id,
                    run_id=request.run_id,
                    lineage_root_run_id=request.lineage_root_run_id,
                    parent_run_id=request.parent_run_id,
                    attempt=request.attempt,
                    source_kind="scratch",
                    apply_mode="none",
                    source_root=None,
                    source_repository_id=None,
                    base_revision=None,
                    staging_root=str(destination),
                    state=WorkspaceLeaseState.STARTING,
                    state_version=1,
                    created_at=created_at,
                )
                self._storage.create_workspace_foundation(lease, None)
                starting_lease = lease
                persisted = True
                destination.mkdir(mode=0o700)
            else:
                identity = self._path_identity(request.source_root)
                source_location = GitLocation(identity.resolved_path)
                snapshot = await self._blocking(self._git.inspect_repository, source_location)
                if snapshot.work_tree != identity.resolved_path:
                    raise WorkspaceSourceError("source path must be the repository root")
                if not snapshot.clean:
                    raise WorkspaceSourceError("governed Git source must be clean")
                assert request.base_revision is not None
                base_revision = await self._blocking(
                    self._git.resolve_commit, source_location, request.base_revision
                )
                self._revalidate_path(identity)
                lease = WorkspaceLease(
                    id=lease_id,
                    run_id=request.run_id,
                    lineage_root_run_id=request.lineage_root_run_id,
                    parent_run_id=request.parent_run_id,
                    attempt=request.attempt,
                    source_kind="git",
                    apply_mode="governed",
                    source_root=str(identity.resolved_path),
                    source_repository_id=snapshot.repository_id,
                    source_git_dir=str(snapshot.git_dir),
                    source_git_dir_identity=snapshot.git_dir_identity,
                    base_revision=base_revision,
                    staging_root=str(destination),
                    state=WorkspaceLeaseState.STARTING,
                    state_version=1,
                    created_at=created_at,
                )
                restore = self._restore_point(lease, snapshot, created_at)
                self._storage.create_workspace_foundation(lease, restore)
                starting_lease = lease
                persisted = True
                await self._blocking(
                    self._git.create_detached_worktree,
                    source_location,
                    destination,
                    start_ref=base_revision,
                )
                self._revalidate_path(identity)
                candidate_snapshot = await self._blocking(
                    self._git.inspect_repository, GitLocation(destination)
                )
                if not self._valid_new_staging_snapshot(lease, candidate_snapshot):
                    raise WorkspaceSourceError("detached staging worktree does not match its base")
                self._storage.save_workspace_staging_identity(
                    self._staging_identity(lease, candidate_snapshot)
                )
                staging_snapshot = candidate_snapshot
                current_source = await self._blocking(self._git.inspect_repository, source_location)
                if self._source_drifted(snapshot, current_source):
                    raise WorkspaceSourceError("source workspace changed during lease creation")
            return self._storage.transition_workspace_lease(
                lease_id,
                expected_version=1,
                expected_state=WorkspaceLeaseState.STARTING,
                new_state=WorkspaceLeaseState.ACTIVE,
                created_at=self._now(),
            )
        except BaseException:
            cleanup_error: BaseException | None = None
            if (
                source_location is not None
                and starting_lease is not None
                and staging_snapshot is None
                and not destination.is_symlink()
                and destination.is_dir()
            ):
                try:
                    recovered = await self._blocking(
                        self._git.inspect_repository, GitLocation(destination)
                    )
                    if not self._valid_new_staging_snapshot(starting_lease, recovered):
                        raise WorkspaceSourceError(
                            "failed staging worktree does not match its durable lease"
                        )
                    self._storage.save_workspace_staging_identity(
                        self._staging_identity(starting_lease, recovered)
                    )
                    staging_snapshot = recovered
                except BaseException as exc:
                    cleanup_error = exc
            try:
                if cleanup_error is None:
                    await self._cleanup_path(
                        destination,
                        source_location,
                        staging_git_dir=(
                            staging_snapshot.git_dir if staging_snapshot is not None else None
                        ),
                        staging_git_dir_identity=(
                            staging_snapshot.git_dir_identity
                            if staging_snapshot is not None
                            else None
                        ),
                    )
            except BaseException as exc:
                cleanup_error = exc
            if persisted:
                self._storage.transition_workspace_lease(
                    lease_id,
                    expected_version=1,
                    expected_state=WorkspaceLeaseState.STARTING,
                    new_state=(
                        WorkspaceLeaseState.CLEANUP_FAILED
                        if cleanup_error is not None
                        else WorkspaceLeaseState.CLOSED
                    ),
                    created_at=self._now(),
                    detail=str(cleanup_error) if cleanup_error is not None else None,
                )
            raise

    @staticmethod
    def _restore_point(
        lease: WorkspaceLease,
        snapshot: GitRepositorySnapshot,
        created_at: datetime,
    ) -> RestorePoint:
        assert lease.source_repository_id is not None
        assert lease.source_root is not None
        assert lease.base_revision is not None
        return RestorePoint(
            id=uuid4().hex,
            lease_id=lease.id,
            source_repository_id=lease.source_repository_id,
            source_root=lease.source_root,
            source_git_dir=str(snapshot.git_dir),
            source_git_dir_identity=snapshot.git_dir_identity,
            base_revision=lease.base_revision,
            source_head_revision=snapshot.head_revision,
            source_head_ref=snapshot.head_ref,
            source_index_tree=snapshot.index_tree,
            source_status_hash=snapshot.status_hash,
            created_at=created_at,
        )

    @staticmethod
    def _source_drifted(expected: GitRepositorySnapshot, current: GitRepositorySnapshot) -> bool:
        return any(
            (
                expected.work_tree != current.work_tree,
                expected.common_git_dir != current.common_git_dir,
                expected.git_dir != current.git_dir,
                expected.git_dir_identity != current.git_dir_identity,
                expected.repository_id != current.repository_id,
                expected.head_revision != current.head_revision,
                expected.head_ref != current.head_ref,
                expected.index_tree != current.index_tree,
                expected.status_hash != current.status_hash,
            )
        )

    @staticmethod
    def _staging_identity(
        lease: WorkspaceLease, snapshot: GitRepositorySnapshot
    ) -> WorkspaceStagingIdentity:
        assert lease.source_repository_id is not None
        assert lease.base_revision is not None
        return WorkspaceStagingIdentity(
            lease_id=lease.id,
            staging_root=lease.staging_root,
            git_dir=str(snapshot.git_dir),
            git_dir_identity=snapshot.git_dir_identity,
            source_repository_id=lease.source_repository_id,
            base_revision=lease.base_revision,
            created_at=lease.created_at,
        )

    @staticmethod
    def _valid_new_staging_snapshot(lease: WorkspaceLease, snapshot: GitRepositorySnapshot) -> bool:
        return (
            snapshot.work_tree == Path(lease.staging_root).absolute()
            and snapshot.repository_id == lease.source_repository_id
            and snapshot.head_revision == lease.base_revision
            and snapshot.head_ref is None
            and snapshot.clean
        )

    async def _cleanup_path(
        self,
        destination: Path,
        source_location: GitLocation | None,
        *,
        staging_git_dir: Path | None = None,
        staging_git_dir_identity: str | None = None,
    ) -> None:
        if destination.parent != self._staging_root or self._staging_root.is_symlink():
            raise WorkspaceConflict("staging root identity changed")
        if source_location is not None:
            if destination.is_symlink():
                raise WorkspaceSourceError("staging workspace must not be a symlink")
            if (staging_git_dir is None) != (staging_git_dir_identity is None):
                raise WorkspaceConflict("staging authority is incomplete")
            if staging_git_dir is None and destination.exists():
                raise WorkspaceConflict("staging authority is unavailable; cleanup failed closed")
            kwargs = (
                {
                    "expected_git_dir": staging_git_dir,
                    "expected_git_dir_identity": staging_git_dir_identity,
                }
                if staging_git_dir is not None
                else {}
            )
            await self._blocking(
                self._git.remove_worktree,
                source_location,
                destination,
                force=True,
                **kwargs,
            )
        elif destination.is_symlink() or destination.is_file():
            destination.unlink(missing_ok=True)
        elif destination.is_dir():
            await self._blocking(shutil.rmtree, destination)

    def _owned_lease(self, lease_id: str, run_id: str) -> WorkspaceLease:
        lease = self._storage.get_workspace_lease(lease_id)
        if lease is None:
            raise WorkspaceConflict("workspace lease does not exist")
        if lease.run_id != run_id:
            raise WorkspaceConflict("workspace lease belongs to another run")
        return lease

    async def capture_change_set(self, lease_id: str, *, run_id: str) -> CanonicalChangeSet:
        lock = self._lease_locks.setdefault(lease_id, asyncio.Lock())
        async with lock:
            return await self._capture_change_set(lease_id, run_id=run_id)

    async def _capture_change_set(self, lease_id: str, *, run_id: str) -> CanonicalChangeSet:
        lease = self._owned_lease(lease_id, run_id)
        if lease.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceConflict("canonical changes require an active lease")
        if lease.source_kind != "git" or lease.source_root is None or lease.base_revision is None:
            raise WorkspaceConflict("scratch leases do not produce canonical Git changes")
        staging = Path(lease.staging_root)
        if staging.is_symlink() or not staging.is_dir():
            raise WorkspaceConflict("staging workspace identity changed")
        source = Path(lease.source_root)
        if source.is_symlink() or not source.is_dir():
            raise WorkspaceSourceError("source workspace identity changed")
        restore = self._storage.get_restore_point_for_lease(lease.id)
        if restore is None:
            raise WorkspaceConflict("workspace lease has no restore point")
        source_location = GitLocation(source)
        staging_location = GitLocation(staging)
        staging_before = await self._blocking(self._git.inspect_repository, staging_location)
        if not self._valid_staging_snapshot(lease, staging_before):
            raise WorkspaceConflict("staging workspace identity changed")
        before = await self._blocking(self._git.inspect_repository, source_location)
        if not self._valid_source_snapshot(lease, restore, before):
            raise WorkspaceSourceError("source workspace drifted from its restore point")
        previous = self._storage.get_latest_canonical_change_set_for_lease(lease.id)
        known_untracked = (
            {
                change.new_path
                for change in previous.changes
                if change.kind == "untracked" and change.new_path is not None
            }
            if previous is not None
            else set()
        )
        captured = await self._blocking(
            self._git.capture_staged_changes,
            staging_location,
            lease.base_revision,
        )
        staging_after = await self._blocking(self._git.inspect_repository, staging_location)
        if not self._valid_staging_snapshot(lease, staging_after):
            raise WorkspaceConflict("staging workspace identity changed during capture")
        after = await self._blocking(self._git.inspect_repository, source_location)
        if self._source_drifted(before, after):
            raise WorkspaceSourceError("source workspace changed during change capture")
        current = self._storage.get_workspace_lease(lease.id)
        current_restore = self._storage.get_restore_point_for_lease(lease.id)
        if (
            current is None
            or current.state is not WorkspaceLeaseState.ACTIVE
            or current.state_version != lease.state_version
            or current_restore != restore
            or not self._valid_staging_snapshot(current, staging_after)
            or not self._valid_source_snapshot(current, current_restore, after)
        ):
            raise WorkspaceConflict("workspace binding changed before canonical capture")
        sequence = self._storage.next_change_set_sequence(lease.id)
        change_set = CanonicalChangeSet(
            id=uuid4().hex,
            lease_id=lease.id,
            restore_point_id=restore.id,
            source_repository_id=restore.source_repository_id,
            base_revision=lease.base_revision,
            sequence=sequence,
            changes=tuple(
                CanonicalChange(
                    kind=(
                        "untracked"
                        if item.kind == "add" and item.new_path in known_untracked
                        else item.kind
                    ),
                    old_path=item.old_path,
                    new_path=item.new_path,
                    old_oid=item.old_oid,
                    new_oid=item.new_oid,
                    old_mode=item.old_mode,
                    new_mode=item.new_mode,
                    old_size=item.old_size,
                    new_size=item.new_size,
                    binary=item.binary,
                )
                for item in captured
            ),
            created_at=self._now(),
        )
        return self._storage.save_canonical_change_set(change_set)

    @staticmethod
    def _valid_staging_snapshot(lease: WorkspaceLease, snapshot: GitRepositorySnapshot) -> bool:
        return (
            lease.staging_git_dir is not None
            and lease.staging_git_dir_identity is not None
            and snapshot.work_tree == Path(lease.staging_root).absolute()
            and snapshot.repository_id == lease.source_repository_id
            and snapshot.git_dir == Path(lease.staging_git_dir)
            and snapshot.git_dir_identity == lease.staging_git_dir_identity
            and snapshot.head_revision == lease.base_revision
            and snapshot.head_ref is None
        )

    @staticmethod
    def _valid_source_snapshot(
        lease: WorkspaceLease,
        restore: RestorePoint,
        snapshot: GitRepositorySnapshot,
    ) -> bool:
        return (
            lease.source_root is not None
            and lease.source_git_dir is not None
            and lease.source_git_dir_identity is not None
            and snapshot.work_tree == Path(lease.source_root)
            and snapshot.repository_id == lease.source_repository_id
            and snapshot.git_dir == Path(lease.source_git_dir)
            and snapshot.git_dir_identity == lease.source_git_dir_identity
            and restore.source_root == lease.source_root
            and restore.source_repository_id == lease.source_repository_id
            and restore.source_git_dir == lease.source_git_dir
            and restore.source_git_dir_identity == lease.source_git_dir_identity
            and restore.base_revision == lease.base_revision
            and snapshot.head_revision == restore.source_head_revision
            and snapshot.head_ref == restore.source_head_ref
            and snapshot.index_tree == restore.source_index_tree
            and snapshot.status_hash == restore.source_status_hash
        )

    async def close_lease(self, lease_id: str, *, run_id: str) -> WorkspaceLease:
        lock = self._lease_locks.setdefault(lease_id, asyncio.Lock())
        async with lock:
            lease = self._owned_lease(lease_id, run_id)
            if lease.state is WorkspaceLeaseState.CLOSED:
                return lease
            if lease.state not in {
                WorkspaceLeaseState.ACTIVE,
                WorkspaceLeaseState.CLEANUP_FAILED,
            }:
                raise WorkspaceConflict(f"workspace lease cannot close from {lease.state.value}")
            if (
                lease.source_kind == "git"
                and lease.state is WorkspaceLeaseState.CLEANUP_FAILED
                and lease.staging_git_dir is None
            ):
                return await self._close_pre_authority_failure(lease)
            closing = self._storage.transition_workspace_lease(
                lease.id,
                expected_version=lease.state_version,
                expected_state=lease.state,
                new_state=WorkspaceLeaseState.CLOSING,
                created_at=self._now(),
            )
            source_location = (
                GitLocation(Path(lease.source_root)) if lease.source_root is not None else None
            )
            try:
                if source_location is not None:
                    source = Path(lease.source_root or "")
                    if source.is_symlink() or not source.is_dir():
                        raise WorkspaceSourceError("source workspace identity changed")
                    source_snapshot = await self._blocking(
                        self._git.inspect_repository, source_location
                    )
                    restore = self._storage.get_restore_point_for_lease(lease.id)
                    if restore is None or not self._valid_source_snapshot(
                        lease, restore, source_snapshot
                    ):
                        raise WorkspaceSourceError(
                            "source workspace drifted from its restore point"
                        )
                    staging_path = Path(lease.staging_root)
                    staging_snapshot = None
                    if not staging_path.is_symlink() and staging_path.is_dir():
                        staging_snapshot = await self._blocking(
                            self._git.inspect_repository, GitLocation(staging_path)
                        )
                        if not self._valid_staging_snapshot(lease, staging_snapshot):
                            raise WorkspaceConflict("staging workspace identity changed")
                    elif staging_path.is_symlink():
                        raise WorkspaceSourceError("staging workspace must not be a symlink")
                else:
                    staging_snapshot = None
                await self._cleanup_path(
                    Path(lease.staging_root),
                    source_location,
                    staging_git_dir=(
                        Path(lease.staging_git_dir) if lease.staging_git_dir is not None else None
                    ),
                    staging_git_dir_identity=lease.staging_git_dir_identity,
                )
            except asyncio.CancelledError:
                destination = Path(lease.staging_root)
                terminal_state = (
                    WorkspaceLeaseState.CLEANUP_FAILED
                    if destination.exists() or destination.is_symlink()
                    else WorkspaceLeaseState.CLOSED
                )
                self._storage.transition_workspace_lease(
                    lease.id,
                    expected_version=closing.state_version,
                    expected_state=WorkspaceLeaseState.CLOSING,
                    new_state=terminal_state,
                    created_at=self._now(),
                )
                raise
            except BaseException as exc:
                self._storage.transition_workspace_lease(
                    lease.id,
                    expected_version=closing.state_version,
                    expected_state=WorkspaceLeaseState.CLOSING,
                    new_state=WorkspaceLeaseState.CLEANUP_FAILED,
                    created_at=self._now(),
                    detail=str(exc),
                )
                raise
            return self._storage.transition_workspace_lease(
                lease.id,
                expected_version=closing.state_version,
                expected_state=WorkspaceLeaseState.CLOSING,
                new_state=WorkspaceLeaseState.CLOSED,
                created_at=self._now(),
            )

    async def _close_pre_authority_failure(self, lease: WorkspaceLease) -> WorkspaceLease:
        destination = Path(lease.staging_root)
        if destination.exists() or destination.is_symlink():
            raise WorkspaceConflict(
                "staging authority is unavailable; remove the orphan path before retrying cleanup"
            )
        assert lease.source_root is not None
        source = Path(lease.source_root)
        if source.is_symlink() or not source.is_dir():
            raise WorkspaceSourceError("source workspace identity changed")
        source_location = GitLocation(source)
        source_snapshot = await self._blocking(self._git.inspect_repository, source_location)
        restore = self._storage.get_restore_point_for_lease(lease.id)
        if restore is None or not self._valid_source_snapshot(lease, restore, source_snapshot):
            raise WorkspaceSourceError("source workspace drifted from its restore point")
        await self._cleanup_path(destination, source_location)
        return self._storage.transition_workspace_lease(
            lease.id,
            expected_version=lease.state_version,
            expected_state=WorkspaceLeaseState.CLEANUP_FAILED,
            new_state=WorkspaceLeaseState.CLOSED,
            created_at=self._now(),
        )

    async def shutdown(self) -> None:
        current = asyncio.current_task()
        async with self._lifecycle_lock:
            self._closing = True
            starting = tuple(task for task in self._starting if task is not current)
        for task in starting:
            task.cancel()
        if starting:
            await asyncio.gather(*starting, return_exceptions=True)
        open_leases = self._storage.list_open_workspace_leases()
        close_tasks = [
            self.close_lease(lease.id, run_id=lease.run_id)
            for lease in open_leases
            if lease.state in {WorkspaceLeaseState.ACTIVE, WorkspaceLeaseState.CLEANUP_FAILED}
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


__all__ = [
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceLeaseRequest",
    "WorkspaceService",
    "WorkspaceSourceError",
]
