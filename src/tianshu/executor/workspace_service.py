"""Detached staging leases for governed executions.

Git worktrees isolate source mutations for cooperating callers. Governed apply
adds a pinned root descriptor, no-follow relative traversal, published-root
checks, and exact postimage ownership journals. It is not an OS sandbox:
same-UID processes can still race the final leaf syscall, and external side
effects remain outside this boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tianshu.executor.git_backend import (
    GitBackend,
    GitBackendError,
    GitLocation,
    GitRepositorySnapshot,
)
from tianshu.executor.workspace_apply import (
    SourceBackup,
    WorkspaceApplyEngine,
    WorkspaceMaterializationError,
    WorkspacePreimageDrift,
)
from tianshu.models.canonical import JsonValue
from tianshu.models.decision import (
    DecisionKind,
    DecisionStatus,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.workspace import (
    ApplyDecision,
    ApplyReceipt,
    CanonicalChange,
    CanonicalChangeSet,
    RestorePoint,
    WorkspaceLease,
    WorkspaceLeaseState,
    WorkspaceRunStatus,
    WorkspaceStagingIdentity,
)
from tianshu.storage.workspace_repo import WorkspaceStateConflict

if TYPE_CHECKING:
    from tianshu.storage import Storage

from tianshu.governance.decision_service import (
    DecisionConflict,
    DecisionService,
    DecisionServiceError,
)

try:  # Unix process locks are part of the governed local-workspace boundary.
    import fcntl
except ImportError:  # pragma: no cover - supported deployments are Unix-like
    fcntl = None  # type: ignore[assignment]


class WorkspaceError(RuntimeError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


class WorkspaceSourceError(WorkspaceError):
    pass


class WorkspaceApplyError(WorkspaceError):
    """Stable, non-secret failure from the governed apply boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(detail)


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
        decision_service: DecisionService | None = None,
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
        self._decision_service = decision_service or DecisionService(storage)
        self._git = git_backend
        self._staging_root = resolved_root
        self._lifecycle_lock = asyncio.Lock()
        self._lease_locks: dict[str, asyncio.Lock] = {}
        self._source_apply_locks: dict[str, asyncio.Lock] = {}
        self._apply_failure_injector: Callable[[str], None] | None = None
        self._governed_apply_failure_injector: Callable[[str], None] | None = None
        self._apply_lock_root = (
            Path(tempfile.gettempdir()).resolve() / f"tianshu-governed-apply-{os.getuid()}"
        )
        self._starting: set[asyncio.Task[object]] = set()
        self._starting_runs: set[str] = set()
        self._closing = False

    @property
    def is_ready(self) -> bool:
        """进入 shutdown 流程后不再 ready（G1.5 readiness 契约）。"""
        return not self._closing

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
                expected.refs_hash != current.refs_hash,
                expected.object_database_hash != current.object_database_hash,
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

    async def get_run_status(self, run_id: str) -> WorkspaceRunStatus:
        lease = self._storage.get_workspace_lease_by_run(run_id)
        if lease is None:
            raise WorkspaceApplyError("run_not_found", "workspace run does not exist")
        if (
            lease.source_kind != "git"
            or lease.apply_mode != "governed"
            or lease.source_root is None
        ):
            raise WorkspaceApplyError(
                "apply_not_governed", "workspace run does not permit governed apply"
            )
        memorial = self._storage.get_memorial(run_id)
        effective = self._storage.get_effective_governance_contract(run_id)
        if memorial is None or effective is None:
            raise WorkspaceApplyError(
                "apply_not_governed", "workspace governance record is unavailable"
            )
        workspace = effective.workspace
        if (
            workspace.staging_mode != "isolated"
            or workspace.apply_mode != "governed"
            or not workspace.require_clean_source
            or effective.resolved_source_id is None
            or effective.resolved_base_revision != lease.base_revision
            or not effective.recovery.require_restore_point
        ):
            raise WorkspaceApplyError(
                "apply_not_governed", "effective workspace contract forbids apply"
            )
        restore = self._storage.get_restore_point_for_lease(lease.id)
        if restore is None:
            raise WorkspaceApplyError("binding_mismatch", "restore point is unavailable")
        host_crash_gap = self._storage.has_unreceipted_apply_claim_for_lease(lease.id)
        change_set = self._storage.get_latest_canonical_change_set_for_lease(lease.id)
        latest_decision = self._storage.get_latest_apply_decision_for_lease(lease.id)
        latest_receipt = (
            self._storage.get_apply_receipt_for_decision(latest_decision.id)
            if latest_decision is not None
            else None
        )
        if lease.state is WorkspaceLeaseState.ACTIVE:
            if not host_crash_gap:
                source_path = Path(lease.source_root)
                staging_path = Path(lease.staging_root)
                if source_path.is_symlink() or not source_path.is_dir():
                    raise WorkspaceApplyError("source_drift", "source workspace identity changed")
                if staging_path.is_symlink() or not staging_path.is_dir():
                    raise WorkspaceApplyError("staging_drift", "staging workspace identity changed")
                try:
                    source_snapshot, staging_snapshot = await asyncio.gather(
                        self._blocking(self._git.inspect_repository, GitLocation(source_path)),
                        self._blocking(self._git.inspect_repository, GitLocation(staging_path)),
                    )
                except Exception as exc:
                    raise WorkspaceApplyError(
                        "source_drift", "workspace repository identity is unavailable"
                    ) from exc
                if not self._valid_source_snapshot(lease, restore, source_snapshot):
                    raise WorkspaceApplyError("source_drift", "source workspace drifted")
                if not self._valid_staging_snapshot(lease, staging_snapshot):
                    raise WorkspaceApplyError("staging_drift", "staging workspace drifted")
        elif lease.state not in {
            WorkspaceLeaseState.CLOSING,
            WorkspaceLeaseState.CLEANUP_FAILED,
            WorkspaceLeaseState.CLOSED,
        }:
            raise WorkspaceApplyError("lease_not_active", "workspace lease is not observable")
        return WorkspaceRunStatus(
            run_id=run_id,
            memorial_status=memorial.status.value,
            effective_contract_hash=effective.content_hash,
            lease=lease,
            restore_point=restore,
            change_set=change_set,
            latest_decision=latest_decision,
            latest_receipt=latest_receipt,
            host_crash_gap=host_crash_gap,
        )

    async def get_run_changes(self, run_id: str) -> CanonicalChangeSet:
        status = await self.get_run_status(run_id)
        if status.change_set is None:
            raise WorkspaceApplyError(
                "changes_unavailable", "canonical workspace changes are unavailable"
            )
        return status.change_set

    async def issue_apply_decision(
        self,
        run_id: str,
        auth: AuthContext,
        reason: str,
        ttl: timedelta,
    ) -> tuple[ApplyDecision, str]:
        principal = auth.principal
        self._require_apply_scope(principal)
        self._require_apply_capability(run_id)
        if ttl <= timedelta(0) or ttl > timedelta(hours=24):
            raise WorkspaceApplyError("decision_expired", "apply decision TTL is invalid")
        reason = reason.strip()
        if not reason or len(reason) > 2_000 or any(char in reason for char in "\x00\r\n"):
            raise WorkspaceApplyError("binding_mismatch", "apply decision reason is invalid")
        existing_lease = self._storage.get_workspace_lease_by_run(run_id)
        existing_decision = (
            self._storage.get_latest_apply_decision_for_lease(existing_lease.id)
            if existing_lease is not None
            else None
        )
        if existing_lease is not None and existing_decision is not None:
            host_crash_gap = self._storage.has_unreceipted_apply_claim_for_lease(existing_lease.id)
            existing_receipt = self._storage.get_apply_receipt_for_decision(existing_decision.id)
            if host_crash_gap:
                raise WorkspaceApplyError(
                    "apply_claim_lost", "an earlier apply claim has no durable receipt"
                )
            existing_lease, existing_decision, _ = await self._reconcile_terminal_authority(
                existing_lease,
                existing_decision,
                existing_receipt,
            )
            if existing_lease.state is not WorkspaceLeaseState.ACTIVE:
                raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
            if existing_decision.state == "expired":
                raise WorkspaceApplyError("decision_expired", "apply decision expired")
            raise WorkspaceApplyError("decision_not_pending", "apply authority was already issued")
        status = await self.get_run_status(run_id)
        if status.lease.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
        if status.host_crash_gap:
            raise WorkspaceApplyError(
                "apply_claim_lost", "an earlier apply claim has no durable receipt"
            )
        if status.memorial_status != "completed":
            raise WorkspaceApplyError("apply_not_governed", "workspace run is not completed")
        try:
            change_set = await self.capture_change_set(status.lease.id, run_id=run_id)
        except WorkspaceSourceError as exc:
            raise WorkspaceApplyError("source_drift", "source workspace drifted") from exc
        except WorkspaceConflict as exc:
            raise WorkspaceApplyError("staging_drift", "staging workspace drifted") from exc
        except GitBackendError as exc:
            raise WorkspaceApplyError("staging_drift", "staging workspace drifted") from exc
        if not change_set.changes:
            raise WorkspaceApplyError(
                "changes_unavailable", "canonical workspace changes are empty"
            )
        status = await self.get_run_status(run_id)
        if status.change_set != change_set:
            raise WorkspaceApplyError("change_set_stale", "canonical workspace changes changed")
        lease = status.lease
        restore = status.restore_point
        if (
            lease.source_repository_id is None
            or lease.source_root is None
            or lease.source_git_dir_identity is None
            or lease.staging_git_dir_identity is None
        ):
            raise WorkspaceApplyError("binding_mismatch", "workspace authority is incomplete")
        payload: dict[str, JsonValue] = {
            "schema_version": 1,
            "run_id": run_id,
            "lease_id": lease.id,
            "restore_point_id": restore.id,
            "restore_point_hash": restore.content_hash,
            "change_set_id": change_set.id,
            "change_set_hash": change_set.content_hash,
            "source_repository_id": lease.source_repository_id,
            "source_root": lease.source_root,
            "source_git_dir_identity": lease.source_git_dir_identity,
            "base_revision": restore.base_revision,
            "source_head_revision": restore.source_head_revision,
            "source_head_ref": restore.source_head_ref,
            "source_index_tree": restore.source_index_tree,
            "source_status_hash": restore.source_status_hash,
            "staging_root": lease.staging_root,
            "staging_git_dir_identity": lease.staging_git_dir_identity,
            "apply_scope": "workspace",
            "reason": reason,
        }
        try:
            request = self._decision_service.request(
                RequestDecisionCommand(
                    kind=DecisionKind.GOVERNED_APPLY,
                    edict_id=self._memorial_edict_id(run_id),
                    memorial_id=run_id,
                    request_key=f"workspace-apply:{lease.id}",
                    payload=payload,
                    expires_at=self._now() + ttl,
                ),
                auth=auth,
            )
        except DecisionServiceError as exc:
            code = (
                "decision_expired" if exc.code == "invalid_decision_expiry" else "binding_mismatch"
            )
            raise WorkspaceApplyError(
                code, "apply decision request conflicts with durable authority"
            ) from exc
        self._inject_governed_apply_failure("after_request")
        record = self._decision_service.get(request.decision_request_id)
        if record is None:
            raise WorkspaceApplyError("binding_mismatch", "apply decision request disappeared")
        if record.request.status is DecisionStatus.PENDING:
            try:
                self._decision_service.resolve(
                    request.decision_request_id,
                    ResolveDecisionCommand(
                        action="approve",
                        reason=reason,
                        payload={"schema_version": 1},
                        expected_version=request.version,
                    ),
                    auth=auth,
                )
            except DecisionConflict:
                pass
            except DecisionServiceError as exc:
                code = (
                    "scope_denied"
                    if exc.code == "workspace_apply_scope_required"
                    else "binding_mismatch"
                )
                raise WorkspaceApplyError(
                    code, "apply decision could not be resolved safely"
                ) from exc
        self._inject_governed_apply_failure("after_resolve_before_projection")
        decision = self.project_governed_apply_decision(request.decision_request_id)
        if decision is None:
            record = self._decision_service.get(request.decision_request_id)
            if record is not None and record.request.status is DecisionStatus.EXPIRED:
                raise WorkspaceApplyError("decision_expired", "apply decision expired")
            raise WorkspaceApplyError("decision_not_pending", "apply authority was not approved")
        return decision, request.decision_request_id

    async def handle_decision_resolved(self, event: EventEnvelope) -> None:
        """Project one durable governed-apply approval; never execute file effects."""

        if event.event_type != "decision.resolved":
            return
        decision_id = event.payload.get("decision_request_id")
        if event.payload.get("kind") != DecisionKind.GOVERNED_APPLY.value or not isinstance(
            decision_id, str
        ):
            return
        self.project_governed_apply_decision(decision_id)

    def project_governed_apply_decision(self, decision_request_id: str) -> ApplyDecision | None:
        record = self._decision_service.get(decision_request_id)
        if (
            record is None
            or record.request.kind is not DecisionKind.GOVERNED_APPLY
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution is None
            or record.resolution.action != "approve"
        ):
            return None
        payload = dict(record.request.payload)
        candidate = ApplyDecision.model_validate(
            {
                **payload,
                "schema_version": "1",
                "id": record.request.decision_request_id,
                "decision_request_id": record.request.decision_request_id,
                "token_hash": hashlib.sha256(
                    record.request.decision_request_id.encode("utf-8")
                ).hexdigest(),
                "principal_digest": self._actor_digest(record.resolution.actor_principal_id),
                "decision_hash": "0" * 64,
                "expires_at": record.request.expires_at,
                "created_at": record.request.created_at,
                "state": "pending",
                "state_version": 1,
            }
        )
        decision_hash = self._canonical_digest(
            candidate.model_dump(mode="json", exclude={"decision_hash", "state", "state_version"})
        )
        decision = candidate.model_copy(update={"decision_hash": decision_hash})
        try:
            self._storage.save_apply_decision(decision)
        except WorkspaceStateConflict as exc:
            raise WorkspaceApplyError(
                "decision_not_pending", "apply authority was already issued"
            ) from exc
        except Exception as exc:
            raise WorkspaceApplyError(
                "binding_mismatch", "apply decision could not be persisted"
            ) from exc
        persisted = self._storage.get_apply_decision(decision.id)
        if persisted is None:
            raise WorkspaceApplyError("binding_mismatch", "apply projection disappeared")
        return persisted

    async def apply(
        self,
        run_id: str,
        decision_id: str,
        token: str,
        auth: AuthContext,
    ) -> ApplyReceipt:
        principal = auth.principal
        self._require_apply_scope(principal)
        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        self._authenticate_generic_decision(decision, run_id, token, principal)
        self._authenticate_decision(decision, run_id, token, principal)
        source_key = self._canonical_digest(
            {
                "repository": decision.source_repository_id,
                "root": decision.source_root,
            }
        )
        source_lock = self._source_apply_locks.setdefault(source_key, asyncio.Lock())
        async with source_lock, self._process_source_lock(source_key):
            lease_lock = self._lease_locks.setdefault(decision.lease_id, asyncio.Lock())
            async with lease_lock:
                return await self._apply_locked(
                    run_id,
                    decision_id,
                    token,
                    principal,
                )

    async def deny_apply_decision(
        self,
        run_id: str,
        decision_id: str,
        auth: AuthContext,
        reason: str,
    ) -> ApplyReceipt:
        """Deny pending authority without accepting or exposing its token."""

        reason = reason.strip()
        if not reason or len(reason) > 2_000 or any(char in reason for char in "\x00\r\n"):
            raise WorkspaceApplyError("binding_mismatch", "denial reason is invalid")
        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        source_key = self._canonical_digest(
            {"repository": decision.source_repository_id, "root": decision.source_root}
        )
        source_lock = self._source_apply_locks.setdefault(source_key, asyncio.Lock())
        async with source_lock, self._process_source_lock(source_key):
            lease_lock = self._lease_locks.setdefault(decision.lease_id, asyncio.Lock())
            async with lease_lock:
                return await self._deny_apply_decision_locked(
                    run_id,
                    decision_id,
                    auth.principal,
                    reason,
                )

    async def _deny_apply_decision_locked(
        self,
        run_id: str,
        decision_id: str,
        principal: Principal,
        reason: str,
    ) -> ApplyReceipt:
        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        self._authenticate_decision_principal(decision, run_id, principal)
        self._require_apply_capability(run_id)
        if decision.state != "pending":
            raise WorkspaceApplyError("decision_not_pending", "apply decision is already terminal")
        lease = self._storage.get_workspace_lease(decision.lease_id)
        restore = self._storage.get_restore_point(decision.restore_point_id)
        if (
            lease is None
            or restore is None
            or lease.run_id != run_id
            or lease.state is not WorkspaceLeaseState.ACTIVE
            or lease.source_root is None
        ):
            raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
        if self._now() >= decision.expires_at:
            lease, decision, _ = await self._reconcile_terminal_authority(
                lease,
                decision,
                None,
            )
            raise WorkspaceApplyError("decision_expired", "apply decision expired")
        assert lease.source_root is not None
        try:
            source = await self._blocking(
                self._git.inspect_repository,
                GitLocation(Path(lease.source_root)),
            )
        except Exception as exc:
            raise WorkspaceApplyError(
                "source_drift", "workspace source identity is unavailable"
            ) from exc
        receipt_id = uuid4().hex
        try:
            decision = self._storage.claim_apply_decision(
                decision.id,
                expected_version=decision.state_version,
                receipt_id=receipt_id,
                created_at=self._now(),
            )
        except WorkspaceStateConflict as exc:
            raise WorkspaceApplyError(
                "decision_not_pending", "apply decision is already terminal"
            ) from exc
        receipt = ApplyReceipt(
            id=receipt_id,
            decision_id=decision.id,
            decision_hash=decision.decision_hash,
            lease_id=decision.lease_id,
            change_set_id=decision.change_set_id,
            change_set_hash=decision.change_set_hash,
            outcome="denied",
            detail="governed workspace apply was denied",
            pre_source_head=restore.source_head_revision,
            pre_source_status_hash=restore.source_status_hash,
            post_source_head=source.head_revision,
            post_source_status_hash=source.status_hash,
            rollback_status="not_attempted",
            failure_code="apply_denied",
            evidence=(
                "source was not materialized",
                f"denial reason digest {hashlib.sha256(reason.encode()).hexdigest()}",
            ),
            created_at=self._now(),
        )
        try:
            self._storage.save_apply_receipt(receipt)
        except Exception as exc:
            try:
                persisted = self._storage.get_apply_receipt_for_decision(decision.id)
            except Exception as lookup_exc:
                raise WorkspaceApplyError(
                    "apply_claim_lost", "denial receipt state is indeterminate"
                ) from lookup_exc
            if persisted != receipt:
                raise WorkspaceApplyError(
                    "apply_claim_lost", "denial receipt state is indeterminate"
                ) from exc
        lease, _, _ = await self._reconcile_terminal_authority(lease, decision, receipt)
        if lease.state is WorkspaceLeaseState.CLEANUP_FAILED:
            raise WorkspaceApplyError("cleanup_failed", "denied workspace staging cleanup failed")
        return receipt

    async def revoke_apply_decision(
        self,
        run_id: str,
        decision_id: str,
        auth: AuthContext,
    ) -> ApplyDecision:
        """Revoke pending authority and idempotently close its staging lease."""

        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        source_key = self._canonical_digest(
            {"repository": decision.source_repository_id, "root": decision.source_root}
        )
        source_lock = self._source_apply_locks.setdefault(source_key, asyncio.Lock())
        async with source_lock, self._process_source_lock(source_key):
            lease_lock = self._lease_locks.setdefault(decision.lease_id, asyncio.Lock())
            async with lease_lock:
                decision = self._storage.get_apply_decision(decision_id)
                if decision is None:
                    raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
                self._authenticate_decision_principal(decision, run_id, auth.principal)
                self._require_apply_capability(run_id)
                if decision.state != "pending":
                    raise WorkspaceApplyError(
                        "decision_not_pending", "apply decision is already terminal"
                    )
                lease = self._storage.get_workspace_lease(decision.lease_id)
                if lease is None or lease.run_id != run_id:
                    raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
                try:
                    decision = self._storage.transition_apply_decision(
                        decision.id,
                        expected_version=decision.state_version,
                        new_state="revoked",
                        created_at=self._now(),
                    )
                except WorkspaceStateConflict as exc:
                    raise WorkspaceApplyError(
                        "decision_not_pending", "apply decision is already terminal"
                    ) from exc
                lease, decision, _ = await self._reconcile_terminal_authority(
                    lease,
                    decision,
                    None,
                )
                if lease.state is WorkspaceLeaseState.CLEANUP_FAILED:
                    raise WorkspaceApplyError(
                        "cleanup_failed", "revoked workspace staging cleanup failed"
                    )
                return decision

    async def _apply_locked(
        self,
        run_id: str,
        decision_id: str,
        token: str,
        principal: Principal,
    ) -> ApplyReceipt:
        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        self._authenticate_decision(decision, run_id, token, principal)
        self._authenticate_generic_decision(decision, run_id, token, principal)
        self._require_apply_capability(run_id)
        if decision.state != "pending":
            raise WorkspaceApplyError("decision_not_pending", "apply decision is already terminal")
        if self._now() >= decision.expires_at:
            try:
                decision = self._storage.transition_apply_decision(
                    decision.id,
                    expected_version=decision.state_version,
                    new_state="expired",
                    created_at=self._now(),
                )
            except WorkspaceStateConflict:
                refreshed = self._storage.get_apply_decision(decision.id)
                if refreshed is not None:
                    decision = refreshed
            lease = self._storage.get_workspace_lease(decision.lease_id)
            if lease is not None:
                with suppress(WorkspaceApplyError):
                    await self._reconcile_terminal_authority(lease, decision, None)
            raise WorkspaceApplyError("decision_expired", "apply decision expired")
        status = await self.get_run_status(run_id)
        if status.lease.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
        if status.host_crash_gap:
            raise WorkspaceApplyError(
                "apply_claim_lost", "an earlier apply claim has no durable receipt"
            )
        if not self._decision_matches_status(decision, status):
            raise WorkspaceApplyError("binding_mismatch", "apply decision binding changed")
        try:
            recomputed = await self._capture_change_set(status.lease.id, run_id=run_id)
        except WorkspaceSourceError as exc:
            raise WorkspaceApplyError("source_drift", "source workspace drifted") from exc
        except WorkspaceConflict as exc:
            raise WorkspaceApplyError("staging_drift", "staging workspace drifted") from exc
        except GitBackendError as exc:
            raise WorkspaceApplyError("staging_drift", "staging workspace drifted") from exc
        if (
            recomputed.id != decision.change_set_id
            or recomputed.content_hash != decision.change_set_hash
        ):
            raise WorkspaceApplyError("change_set_stale", "approved canonical changes are stale")
        decision = self._storage.get_apply_decision(decision_id)
        if decision is None:
            raise WorkspaceApplyError("decision_not_found", "apply decision does not exist")
        self._authenticate_decision(decision, run_id, token, principal)
        if decision.state != "pending":
            raise WorkspaceApplyError("decision_not_pending", "apply decision is already terminal")
        lease = self._storage.get_workspace_lease(decision.lease_id)
        restore = self._storage.get_restore_point(decision.restore_point_id)
        if (
            lease is None
            or restore is None
            or lease.state is not WorkspaceLeaseState.ACTIVE
            or lease.run_id != run_id
            or lease.source_root is None
        ):
            raise WorkspaceApplyError("lease_not_active", "workspace lease is not active")
        source_location = GitLocation(Path(lease.source_root))
        staging_location = GitLocation(Path(lease.staging_root))
        try:
            pre_source = await self._blocking(self._git.inspect_repository, source_location)
            staging_before = await self._blocking(self._git.inspect_repository, staging_location)
        except Exception as exc:
            raise WorkspaceApplyError(
                "source_drift", "workspace repository identity is unavailable"
            ) from exc
        if not self._valid_source_snapshot(lease, restore, pre_source):
            raise WorkspaceApplyError("source_drift", "source workspace drifted")
        if not self._valid_staging_snapshot(lease, staging_before):
            raise WorkspaceApplyError("staging_drift", "staging workspace drifted")
        engine = WorkspaceApplyEngine(self._git, self._inject_apply_failure)
        try:
            plan = await self._blocking(
                engine.preflight,
                source_location,
                staging_location,
                recomputed.changes,
            )
            source_after_preflight = await self._blocking(
                self._git.inspect_repository, source_location
            )
            staging_after_preflight = await self._blocking(
                self._git.inspect_repository, staging_location
            )
        except WorkspaceMaterializationError as exc:
            raise WorkspaceApplyError(
                "staging_drift", "canonical path identity validation failed"
            ) from exc
        if self._source_drifted(pre_source, source_after_preflight):
            raise WorkspaceApplyError("source_drift", "source workspace changed")
        if not self._valid_staging_snapshot(lease, staging_after_preflight):
            raise WorkspaceApplyError("staging_drift", "staging workspace changed")
        try:
            self._inject_apply_failure("after_validate")
        except Exception as exc:
            raise WorkspaceApplyError(
                "materialization_failed", "governed apply validation did not complete"
            ) from exc
        receipt_id = uuid4().hex
        claimed_at = self._now()
        try:
            decision = self._storage.claim_apply_decision(
                decision.id,
                expected_version=decision.state_version,
                receipt_id=receipt_id,
                created_at=claimed_at,
            )
        except WorkspaceStateConflict as exc:
            current_decision = self._storage.get_apply_decision(decision.id)
            current_lease = self._storage.get_workspace_lease(decision.lease_id)
            if (
                current_decision is not None
                and current_decision.state == "pending"
                and claimed_at >= current_decision.expires_at
            ):
                try:
                    current_decision = self._storage.transition_apply_decision(
                        current_decision.id,
                        expected_version=current_decision.state_version,
                        new_state="expired",
                        created_at=claimed_at,
                    )
                except WorkspaceStateConflict:
                    current_decision = self._storage.get_apply_decision(decision.id)
                if current_lease is not None and current_decision is not None:
                    with suppress(WorkspaceApplyError):
                        await self._reconcile_terminal_authority(
                            current_lease,
                            current_decision,
                            None,
                        )
                if current_decision is not None and current_decision.state == "expired":
                    raise WorkspaceApplyError("decision_expired", "apply decision expired") from exc
            if current_lease is None or current_lease.state is not WorkspaceLeaseState.ACTIVE:
                raise WorkspaceApplyError(
                    "lease_not_active", "workspace lease is not active"
                ) from exc
            raise WorkspaceApplyError(
                "apply_claim_lost", "another apply claimed this decision"
            ) from exc

        backup: SourceBackup | None = None
        receipt: ApplyReceipt | None = None
        try:
            self._inject_apply_failure("after_claim")
            backup = await self._blocking(engine.backup, source_location.work_tree, plan)
            source_after_backup = await self._blocking(
                self._git.inspect_repository, source_location
            )
            if self._source_drifted(pre_source, source_after_backup):
                raise WorkspaceMaterializationError("source changed during backup")
            self._inject_apply_failure("before_materialize")
            source_final, staging_final = await asyncio.gather(
                self._blocking(self._git.inspect_repository, source_location),
                self._blocking(self._git.inspect_repository, staging_location),
            )
            if self._source_drifted(pre_source, source_final):
                raise WorkspacePreimageDrift("source Git authority changed before materialization")
            if not self._valid_staging_snapshot(lease, staging_final):
                raise WorkspaceMaterializationError(
                    "staging Git authority changed before materialization"
                )
            await self._blocking(engine.materialize, backup, plan)
            await self._blocking(engine.verify_materialized, backup, plan)
            post_source = await self._blocking(self._git.inspect_repository, source_location)
            if (
                not self._valid_applied_authority(decision, post_source)
                or post_source.refs_hash != pre_source.refs_hash
                or post_source.object_database_hash != pre_source.object_database_hash
            ):
                raise WorkspaceMaterializationError("source authority changed during apply")
            applied = await self._blocking(
                self._git.capture_staged_changes,
                source_location,
                decision.base_revision,
            )
            source_after_capture = await self._blocking(
                self._git.inspect_repository, source_location
            )
            if self._source_drifted(post_source, source_after_capture):
                raise WorkspaceMaterializationError(
                    "source authority changed during canonical verification"
                )
            if self._normalized_changes(applied) != self._normalized_changes(recomputed.changes):
                raise WorkspaceMaterializationError("materialized canonical set differs")
            self._inject_apply_failure("before_success_receipt")
            receipt = ApplyReceipt(
                id=receipt_id,
                decision_id=decision.id,
                decision_hash=decision.decision_hash,
                lease_id=decision.lease_id,
                change_set_id=decision.change_set_id,
                change_set_hash=decision.change_set_hash,
                outcome="succeeded",
                detail="approved canonical workspace changes were applied",
                pre_source_head=pre_source.head_revision,
                pre_source_status_hash=pre_source.status_hash,
                post_source_head=post_source.head_revision,
                post_source_status_hash=post_source.status_hash,
                rollback_status="not_required",
                evidence=(
                    "canonical change set verified",
                    "source HEAD, refs, index, and object database unchanged",
                ),
                created_at=self._now(),
            )
            self._storage.save_apply_receipt(receipt)
        except BaseException as exc:
            persisted_receipt: ApplyReceipt | None = None
            receipt_lookup_failed = False
            try:
                persisted_receipt = self._storage.get_apply_receipt_for_decision(decision.id)
            except Exception:
                receipt_lookup_failed = True
            if receipt_lookup_failed:
                engine.release(backup)
                await self._mark_cleanup_failed(
                    lease, "workspace apply receipt state is indeterminate"
                )
                raise WorkspaceApplyError(
                    "apply_claim_lost", "workspace apply receipt state is indeterminate"
                ) from exc
            if persisted_receipt is not None:
                if receipt is None or persisted_receipt != receipt:
                    engine.release(backup)
                    await self._mark_cleanup_failed(
                        lease, "workspace apply receipt binding is inconsistent"
                    )
                    raise WorkspaceApplyError(
                        "binding_mismatch", "workspace apply receipt binding is inconsistent"
                    ) from exc
                receipt = persisted_receipt
            else:
                await self._handle_claimed_apply_failure(
                    engine=engine,
                    backup=backup,
                    pre_source=pre_source,
                    source_location=source_location,
                    lease=lease,
                    decision=decision,
                    receipt_id=receipt_id,
                    cause=exc,
                )

        engine.release(backup)
        assert receipt is not None
        try:
            await self._close_applied_lease(lease, post_source)
        except Exception as exc:
            raise WorkspaceApplyError(
                "cleanup_failed", "source was applied but staging cleanup failed"
            ) from exc
        return receipt

    async def _handle_claimed_apply_failure(
        self,
        *,
        engine: WorkspaceApplyEngine,
        backup: SourceBackup | None,
        pre_source: GitRepositorySnapshot,
        source_location: GitLocation,
        lease: WorkspaceLease,
        decision: ApplyDecision,
        receipt_id: str,
        cause: BaseException,
    ) -> NoReturn:
        try:
            await self._handle_claimed_apply_failure_inner(
                engine=engine,
                backup=backup,
                pre_source=pre_source,
                source_location=source_location,
                lease=lease,
                decision=decision,
                receipt_id=receipt_id,
                cause=cause,
            )
        finally:
            engine.release(backup)

    async def _handle_claimed_apply_failure_inner(
        self,
        *,
        engine: WorkspaceApplyEngine,
        backup: SourceBackup | None,
        pre_source: GitRepositorySnapshot,
        source_location: GitLocation,
        lease: WorkspaceLease,
        decision: ApplyDecision,
        receipt_id: str,
        cause: BaseException,
    ) -> NoReturn:
        failure_code = "materialization_failed"
        rollback_status: Literal["not_required", "not_attempted", "succeeded", "failed"] = (
            "not_required"
        )
        evidence = ["source Git authority was not mutated"]

        try:
            current_source = await self._blocking(self._git.inspect_repository, source_location)
        except Exception as exc:
            await self._mark_cleanup_failed(lease, "workspace apply source state is indeterminate")
            raise WorkspaceApplyError(
                "apply_claim_lost", "workspace apply source state is indeterminate"
            ) from exc

        preimage_drifted = isinstance(cause, WorkspacePreimageDrift)
        if backup is None or not backup.mutation_started:
            if backup is not None:
                try:
                    await self._blocking(engine.verify_preimage, backup)
                except WorkspacePreimageDrift:
                    preimage_drifted = True
            if self._source_drifted(pre_source, current_source):
                preimage_drifted = True

        if preimage_drifted:
            failure_code = "source_drift"
            rollback_status = "not_attempted"
            evidence.append("external source change was preserved; rollback was not attempted")
            await self._mark_cleanup_failed(lease, "source changed outside the governed apply lock")
        elif backup is not None and backup.mutation_started:
            try:
                await self._blocking(engine.restore, backup)
                current_source = await self._blocking(self._git.inspect_repository, source_location)
            except BaseException:
                failure_code = "rollback_failed"
                rollback_status = "failed"
                await self._mark_cleanup_failed(
                    lease, "workspace apply rollback could not be proven"
                )
                try:
                    current_source = await self._blocking(
                        self._git.inspect_repository, source_location
                    )
                except Exception as exc:
                    raise WorkspaceApplyError(
                        "apply_claim_lost", "workspace apply source state is indeterminate"
                    ) from exc
            else:
                rollback_status = "succeeded"
                if self._source_drifted(pre_source, current_source):
                    failure_code = "source_drift"
                    evidence.append(
                        "governed paths restored; independent source authority change preserved"
                    )
                    await self._mark_cleanup_failed(
                        lease, "source changed outside the governed apply lock"
                    )
                else:
                    evidence.append(
                        "source bytes, modes, links, HEAD, refs, index, and object database restored"
                    )
        elif self._source_drifted(pre_source, current_source):
            failure_code = "source_drift"
            rollback_status = "not_attempted"
            evidence.append("external source change was preserved; rollback was not attempted")
            await self._mark_cleanup_failed(lease, "source changed outside the governed apply lock")
        else:
            evidence.append("source preimage remained exact; no filesystem rollback was needed")

        failed_receipt = ApplyReceipt(
            id=receipt_id,
            decision_id=decision.id,
            decision_hash=decision.decision_hash,
            lease_id=decision.lease_id,
            change_set_id=decision.change_set_id,
            change_set_hash=decision.change_set_hash,
            outcome="failed",
            detail="governed workspace apply failed safely",
            pre_source_head=pre_source.head_revision,
            pre_source_status_hash=pre_source.status_hash,
            post_source_head=current_source.head_revision,
            post_source_status_hash=current_source.status_hash,
            rollback_status=rollback_status,
            failure_code=failure_code,
            evidence=tuple(evidence),
            created_at=self._now(),
        )
        try:
            self._storage.save_apply_receipt(failed_receipt)
        except Exception as exc:
            try:
                persisted = self._storage.get_apply_receipt_for_decision(decision.id)
            except Exception as lookup_exc:
                await self._mark_cleanup_failed(
                    lease, "workspace apply receipt state is indeterminate"
                )
                raise WorkspaceApplyError(
                    "apply_claim_lost", "workspace apply receipt state is indeterminate"
                ) from lookup_exc
            if persisted != failed_receipt:
                await self._mark_cleanup_failed(
                    lease, "workspace apply receipt state is inconsistent"
                )
                raise WorkspaceApplyError(
                    "apply_claim_lost", "workspace apply receipt state is inconsistent"
                ) from exc

        terminal_decision = self._storage.get_apply_decision(decision.id)
        if terminal_decision is not None:
            with suppress(WorkspaceApplyError):
                await self._reconcile_terminal_authority(
                    lease,
                    terminal_decision,
                    failed_receipt,
                )

        raise WorkspaceApplyError(
            failure_code,
            (
                "source changed before governed materialization"
                if failure_code == "source_drift"
                else "workspace apply rollback could not be proven"
                if failure_code == "rollback_failed"
                else "workspace apply failed and the source was restored"
            ),
        ) from cause

    async def _close_applied_lease(
        self,
        lease: WorkspaceLease,
        expected_source: GitRepositorySnapshot,
    ) -> WorkspaceLease:
        closing = (
            lease
            if lease.state is WorkspaceLeaseState.CLOSING
            else self._storage.transition_workspace_lease(
                lease.id,
                expected_version=lease.state_version,
                expected_state=lease.state,
                new_state=WorkspaceLeaseState.CLOSING,
                created_at=self._now(),
            )
        )
        try:
            assert lease.source_root is not None
            await self._cleanup_path(
                Path(lease.staging_root),
                GitLocation(Path(lease.source_root)),
                staging_git_dir=(
                    Path(lease.staging_git_dir) if lease.staging_git_dir is not None else None
                ),
                staging_git_dir_identity=lease.staging_git_dir_identity,
            )
            current_source = await self._blocking(
                self._git.inspect_repository,
                GitLocation(Path(lease.source_root)),
            )
            if self._source_drifted(expected_source, current_source):
                raise WorkspaceSourceError("source changed during staging cleanup")
        except BaseException as exc:
            self._finish_closing_lease(
                closing,
                WorkspaceLeaseState.CLEANUP_FAILED,
                detail="governed apply staging cleanup failed",
            )
            raise WorkspaceSourceError("governed apply staging cleanup failed") from exc
        closed = self._finish_closing_lease(
            closing,
            WorkspaceLeaseState.CLOSED,
            detail="governed apply staging cleanup completed",
        )
        if closed.state is not WorkspaceLeaseState.CLOSED:
            raise WorkspaceSourceError("governed apply terminal state is indeterminate")
        return closed

    def _finish_closing_lease(
        self,
        closing: WorkspaceLease,
        terminal_state: WorkspaceLeaseState,
        *,
        detail: str,
    ) -> WorkspaceLease:
        """Commit a terminal lease state and disambiguate post-commit errors."""

        try:
            return self._storage.transition_workspace_lease(
                closing.id,
                expected_version=closing.state_version,
                expected_state=WorkspaceLeaseState.CLOSING,
                new_state=terminal_state,
                created_at=self._now(),
                detail=detail,
            )
        except Exception as exc:
            current = self._storage.get_workspace_lease(closing.id)
            if current is not None and current.state is terminal_state:
                return current
            if current is not None and current.state is WorkspaceLeaseState.CLOSING:
                try:
                    return self._storage.transition_workspace_lease(
                        current.id,
                        expected_version=current.state_version,
                        expected_state=WorkspaceLeaseState.CLOSING,
                        new_state=WorkspaceLeaseState.CLEANUP_FAILED,
                        created_at=self._now(),
                        detail="workspace terminal state commit is indeterminate",
                    )
                except Exception:
                    final = self._storage.get_workspace_lease(current.id)
                    if final is not None and final.state is WorkspaceLeaseState.CLEANUP_FAILED:
                        return final
            raise WorkspaceApplyError(
                "cleanup_failed", "workspace terminal state is indeterminate"
            ) from exc

    async def _mark_cleanup_failed(self, lease: WorkspaceLease, detail: str) -> None:
        current = self._storage.get_workspace_lease(lease.id)
        if current is None or current.state is not WorkspaceLeaseState.ACTIVE:
            return
        try:
            closing = self._storage.transition_workspace_lease(
                current.id,
                expected_version=current.state_version,
                expected_state=WorkspaceLeaseState.ACTIVE,
                new_state=WorkspaceLeaseState.CLOSING,
                created_at=self._now(),
                detail=detail,
            )
            self._storage.transition_workspace_lease(
                current.id,
                expected_version=closing.state_version,
                expected_state=WorkspaceLeaseState.CLOSING,
                new_state=WorkspaceLeaseState.CLEANUP_FAILED,
                created_at=self._now(),
                detail=detail,
            )
        except WorkspaceStateConflict:
            return

    async def _finalize_terminal_staging(
        self,
        lease: WorkspaceLease,
        *,
        detail: str,
        preserve_failure: bool = False,
    ) -> WorkspaceLease:
        """Remove terminal staging without rewriting source Git authority."""

        current = self._storage.get_workspace_lease(lease.id)
        if current is None:
            raise WorkspaceApplyError("binding_mismatch", "workspace lease is unavailable")
        if current.state is WorkspaceLeaseState.CLOSED:
            return current
        preserve_failure = preserve_failure or current.state is WorkspaceLeaseState.CLEANUP_FAILED
        if preserve_failure and current.state is WorkspaceLeaseState.CLEANUP_FAILED:
            return current
        if current.state in {
            WorkspaceLeaseState.ACTIVE,
            WorkspaceLeaseState.CLEANUP_FAILED,
        }:
            try:
                closing = self._storage.transition_workspace_lease(
                    current.id,
                    expected_version=current.state_version,
                    expected_state=current.state,
                    new_state=WorkspaceLeaseState.CLOSING,
                    created_at=self._now(),
                    detail=detail,
                )
            except WorkspaceStateConflict:
                refreshed = self._storage.get_workspace_lease(current.id)
                if refreshed is None:
                    raise WorkspaceApplyError(
                        "binding_mismatch", "workspace lease is unavailable"
                    ) from None
                if refreshed.state is WorkspaceLeaseState.CLOSED:
                    return refreshed
                if refreshed.state is not WorkspaceLeaseState.CLOSING:
                    return refreshed
                closing = refreshed
        elif current.state is WorkspaceLeaseState.CLOSING:
            closing = current
        else:
            return current

        if preserve_failure:
            return self._finish_closing_lease(
                closing,
                WorkspaceLeaseState.CLEANUP_FAILED,
                detail=detail,
            )

        try:
            source_location = (
                GitLocation(Path(closing.source_root)) if closing.source_root is not None else None
            )
            await self._cleanup_path(
                Path(closing.staging_root),
                source_location,
                staging_git_dir=(
                    Path(closing.staging_git_dir) if closing.staging_git_dir is not None else None
                ),
                staging_git_dir_identity=closing.staging_git_dir_identity,
            )
        except BaseException:
            terminal_state = WorkspaceLeaseState.CLEANUP_FAILED
            terminal_detail = "terminal workspace staging cleanup failed"
        else:
            terminal_state = (
                WorkspaceLeaseState.CLEANUP_FAILED
                if preserve_failure
                else WorkspaceLeaseState.CLOSED
            )
            terminal_detail = detail

        return self._finish_closing_lease(
            closing,
            terminal_state,
            detail=terminal_detail,
        )

    async def _reconcile_terminal_authority(
        self,
        lease: WorkspaceLease,
        decision: ApplyDecision,
        receipt: ApplyReceipt | None,
    ) -> tuple[WorkspaceLease, ApplyDecision, ApplyReceipt | None]:
        """Finalize terminal authority from an authenticated mutation path."""

        if decision.state == "pending" and self._now() >= decision.expires_at:
            try:
                decision = self._storage.transition_apply_decision(
                    decision.id,
                    expected_version=decision.state_version,
                    new_state="expired",
                    created_at=self._now(),
                )
            except WorkspaceStateConflict:
                refreshed = self._storage.get_apply_decision(decision.id)
                if refreshed is None:
                    raise WorkspaceApplyError(
                        "decision_not_found", "apply decision does not exist"
                    ) from None
                decision = refreshed
            receipt = self._storage.get_apply_receipt_for_decision(decision.id)
        if decision.state == "pending":
            return lease, decision, receipt
        if self._storage.has_unreceipted_apply_claim_for_lease(lease.id):
            return lease, decision, receipt

        if receipt is not None and receipt.outcome == "succeeded":
            try:
                recovered = await self._recover_applied_cleanup(lease)
            except Exception:
                lease = await self._finalize_terminal_staging(
                    lease,
                    detail="applied source requires cleanup review",
                    preserve_failure=True,
                )
            else:
                if recovered is not None:
                    lease = recovered
        else:
            preserve_failure = lease.state is WorkspaceLeaseState.CLEANUP_FAILED or (
                receipt is not None
                and receipt.outcome == "failed"
                and receipt.failure_code in {"source_drift", "rollback_failed"}
            )
            lease = await self._finalize_terminal_staging(
                lease,
                detail="terminal apply authority staging cleanup",
                preserve_failure=preserve_failure,
            )
        return lease, decision, receipt

    async def _recover_applied_cleanup(
        self,
        lease: WorkspaceLease,
    ) -> WorkspaceLease | None:
        decision = self._storage.get_latest_apply_decision_for_lease(lease.id)
        receipt = (
            self._storage.get_apply_receipt_for_decision(decision.id)
            if decision is not None
            else None
        )
        if (
            decision is None
            or receipt is None
            or receipt.outcome != "succeeded"
            or decision.lease_id != lease.id
            or receipt.lease_id != lease.id
            or lease.source_root is None
        ):
            return None
        try:
            source = await self._blocking(
                self._git.inspect_repository,
                GitLocation(Path(lease.source_root)),
            )
        except Exception as exc:
            raise WorkspaceSourceError("applied source identity is unavailable") from exc
        if (
            not self._valid_applied_authority(decision, source)
            or source.head_revision != receipt.post_source_head
            or source.status_hash != receipt.post_source_status_hash
        ):
            raise WorkspaceSourceError("applied source drifted before cleanup recovery")
        return await self._close_applied_lease(lease, source)

    def _authenticate_decision(
        self,
        decision: ApplyDecision,
        run_id: str,
        token: str,
        principal: Principal,
    ) -> None:
        self._authenticate_decision_principal(decision, run_id, principal)
        try:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        except (AttributeError, UnicodeError) as exc:
            raise WorkspaceApplyError("token_invalid", "apply token is invalid") from exc
        if not hmac.compare_digest(decision.token_hash, token_hash):
            raise WorkspaceApplyError("token_invalid", "apply token is invalid")

    def _authenticate_generic_decision(
        self,
        decision: ApplyDecision,
        run_id: str,
        token: str,
        principal: Principal,
    ) -> None:
        self._require_apply_scope(principal)
        if decision.decision_request_id is None:
            raise WorkspaceApplyError("binding_mismatch", "apply decision lacks durable authority")
        record = self._decision_service.get(decision.decision_request_id)
        if (
            record is None
            or record.request.kind is not DecisionKind.GOVERNED_APPLY
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution is None
            or record.resolution.action != "approve"
            or record.resolution.actor_principal_id != principal.id
            or record.request.decision_request_id != decision.id
            or record.request.memorial_id != run_id
            or record.request.expires_at != decision.expires_at
            or record.request.created_at != decision.created_at
            or record.request.payload != self._governed_apply_payload(decision)
        ):
            raise WorkspaceApplyError("binding_mismatch", "generic apply authority changed")
        if token != record.request.decision_request_id:
            raise WorkspaceApplyError("token_invalid", "apply token is invalid")

    @staticmethod
    def _governed_apply_payload(decision: ApplyDecision) -> dict[str, object]:
        return {
            "schema_version": 1,
            "run_id": decision.run_id,
            "lease_id": decision.lease_id,
            "restore_point_id": decision.restore_point_id,
            "restore_point_hash": decision.restore_point_hash,
            "change_set_id": decision.change_set_id,
            "change_set_hash": decision.change_set_hash,
            "source_repository_id": decision.source_repository_id,
            "source_root": decision.source_root,
            "source_git_dir_identity": decision.source_git_dir_identity,
            "base_revision": decision.base_revision,
            "source_head_revision": decision.source_head_revision,
            "source_head_ref": decision.source_head_ref,
            "source_index_tree": decision.source_index_tree,
            "source_status_hash": decision.source_status_hash,
            "staging_root": decision.staging_root,
            "staging_git_dir_identity": decision.staging_git_dir_identity,
            "apply_scope": decision.apply_scope,
            "reason": decision.reason,
        }

    def _memorial_edict_id(self, memorial_id: str) -> str:
        memorial = self._storage.get_memorial(memorial_id)
        if memorial is None:
            raise WorkspaceApplyError("run_not_found", "workspace run does not exist")
        return memorial.edict_id

    def _inject_governed_apply_failure(self, stage: str) -> None:
        if self._governed_apply_failure_injector is not None:
            self._governed_apply_failure_injector(stage)

    def _authenticate_decision_principal(
        self,
        decision: ApplyDecision,
        run_id: str,
        principal: Principal,
    ) -> None:
        self._require_apply_scope(principal)
        if decision.run_id != run_id:
            raise WorkspaceApplyError("binding_mismatch", "apply decision belongs to another run")
        principal_digest = self._actor_digest(principal.id)
        if not hmac.compare_digest(decision.principal_digest, principal_digest):
            raise WorkspaceApplyError(
                "principal_mismatch", "apply decision belongs to another principal"
            )
        payload = decision.model_dump(
            mode="json",
            exclude={"decision_hash", "state", "state_version"},
        )
        if not hmac.compare_digest(decision.decision_hash, self._canonical_digest(payload)):
            raise WorkspaceApplyError("binding_mismatch", "apply decision hash is invalid")

    @staticmethod
    def _require_apply_scope(principal: Principal) -> None:
        if "workspace:apply" not in principal.scopes:
            raise WorkspaceApplyError(
                "scope_denied", "principal cannot approve or apply workspace changes"
            )

    def _require_apply_capability(self, run_id: str) -> None:
        effective = self._storage.get_effective_governance_contract(run_id)
        if effective is None or effective.state("governed_apply_merge") != "enforced":
            raise WorkspaceApplyError(
                "capability_not_enforced",
                "effective executor does not enforce governed apply",
            )

    @staticmethod
    def _actor_digest(principal_id: str) -> str:
        return hashlib.sha256(principal_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_digest(payload: dict[str, object]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _decision_matches_status(
        decision: ApplyDecision,
        status: WorkspaceRunStatus,
    ) -> bool:
        lease = status.lease
        restore = status.restore_point
        changes = status.change_set
        return (
            changes is not None
            and decision.run_id == status.run_id
            and decision.lease_id == lease.id
            and decision.restore_point_id == restore.id
            and decision.restore_point_hash == restore.content_hash
            and decision.change_set_id == changes.id
            and decision.change_set_hash == changes.content_hash
            and decision.source_repository_id == restore.source_repository_id
            and decision.source_root == restore.source_root
            and decision.source_git_dir_identity == restore.source_git_dir_identity
            and decision.base_revision == restore.base_revision
            and decision.source_head_revision == restore.source_head_revision
            and decision.source_head_ref == restore.source_head_ref
            and decision.source_index_tree == restore.source_index_tree
            and decision.source_status_hash == restore.source_status_hash
            and decision.staging_root == lease.staging_root
            and decision.staging_git_dir_identity == lease.staging_git_dir_identity
        )

    @staticmethod
    def _valid_applied_authority(
        decision: ApplyDecision,
        snapshot: GitRepositorySnapshot,
    ) -> bool:
        return (
            snapshot.work_tree == Path(decision.source_root)
            and snapshot.repository_id == decision.source_repository_id
            and snapshot.git_dir_identity == decision.source_git_dir_identity
            and snapshot.head_revision == decision.source_head_revision
            and snapshot.head_ref == decision.source_head_ref
            and snapshot.index_tree == decision.source_index_tree
        )

    @staticmethod
    def _normalized_changes(changes) -> tuple[tuple[object, ...], ...]:
        return tuple(
            sorted(
                (
                    "add" if change.kind == "untracked" else change.kind,
                    change.old_path,
                    change.new_path,
                    change.old_oid,
                    change.new_oid,
                    change.old_mode,
                    change.new_mode,
                    change.old_size,
                    change.new_size,
                    change.binary,
                )
                for change in changes
            )
        )

    def _inject_apply_failure(self, stage: str) -> None:
        if self._apply_failure_injector is not None:
            self._apply_failure_injector(stage)

    @asynccontextmanager
    async def _process_source_lock(self, source_key: str):
        if fcntl is None:
            raise WorkspaceApplyError(
                "apply_claim_lost", "process-level workspace locking is unavailable"
            )
        try:
            if self._apply_lock_root.is_symlink():
                raise OSError("workspace process lock root is a symlink")
            self._apply_lock_root.mkdir(mode=0o700, exist_ok=True)
            if self._apply_lock_root.is_symlink() or not self._apply_lock_root.is_dir():
                raise OSError("workspace process lock root is invalid")
            self._apply_lock_root.chmod(0o700)
        except OSError as exc:
            raise WorkspaceApplyError(
                "apply_claim_lost", "workspace process lock root is unavailable"
            ) from exc
        lock_path = self._apply_lock_root / f"{source_key}.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("workspace process lock is not a regular file")
            os.fchmod(descriptor, 0o600)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise WorkspaceApplyError(
                "apply_claim_lost", "workspace process lock is unavailable"
            ) from exc
        assert descriptor is not None
        try:
            await self._blocking(fcntl.flock, descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                await self._blocking(fcntl.flock, descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    async def close_lease(self, lease_id: str, *, run_id: str) -> WorkspaceLease:
        lease = self._owned_lease(lease_id, run_id)
        if (
            lease.source_kind != "git"
            or lease.source_repository_id is None
            or lease.source_root is None
        ):
            return await self._close_lease_under_source_lock(lease_id, run_id=run_id)
        source_key = self._canonical_digest(
            {"repository": lease.source_repository_id, "root": lease.source_root}
        )
        source_lock = self._source_apply_locks.setdefault(source_key, asyncio.Lock())
        async with source_lock, self._process_source_lock(source_key):
            return await self._close_lease_under_source_lock(lease_id, run_id=run_id)

    async def _close_lease_under_source_lock(
        self,
        lease_id: str,
        *,
        run_id: str,
    ) -> WorkspaceLease:
        lock = self._lease_locks.setdefault(lease_id, asyncio.Lock())
        async with lock:
            lease = self._owned_lease(lease_id, run_id)
            if lease.state is WorkspaceLeaseState.CLOSED:
                return lease
            if lease.state not in {
                WorkspaceLeaseState.ACTIVE,
                WorkspaceLeaseState.CLOSING,
                WorkspaceLeaseState.CLEANUP_FAILED,
            }:
                raise WorkspaceConflict(f"workspace lease cannot close from {lease.state.value}")
            decision = self._storage.get_latest_apply_decision_for_lease(lease.id)
            if decision is not None and decision.state == "pending":
                if self._now() < decision.expires_at:
                    raise WorkspaceConflict("workspace apply decision is pending")
                try:
                    decision = self._storage.transition_apply_decision(
                        decision.id,
                        expected_version=decision.state_version,
                        new_state="expired",
                        created_at=self._now(),
                    )
                except WorkspaceStateConflict:
                    refreshed = self._storage.get_apply_decision(decision.id)
                    if refreshed is None:
                        raise WorkspaceConflict("workspace apply decision disappeared") from None
                    decision = refreshed
                lease, _, _ = await self._reconcile_terminal_authority(
                    lease,
                    decision,
                    None,
                )
                return lease
            if self._storage.has_unreceipted_apply_claim_for_lease(lease.id):
                raise WorkspaceConflict("workspace apply claim has no durable receipt")
            if lease.state in {
                WorkspaceLeaseState.CLOSING,
                WorkspaceLeaseState.CLEANUP_FAILED,
            }:
                applied = await self._recover_applied_cleanup(lease)
                if applied is not None:
                    return applied
            if (
                lease.source_kind == "git"
                and lease.state is WorkspaceLeaseState.CLEANUP_FAILED
                and lease.staging_git_dir is None
            ):
                return await self._close_pre_authority_failure(lease)
            closing = (
                lease
                if lease.state is WorkspaceLeaseState.CLOSING
                else self._storage.transition_workspace_lease(
                    lease.id,
                    expected_version=lease.state_version,
                    expected_state=lease.state,
                    new_state=WorkspaceLeaseState.CLOSING,
                    created_at=self._now(),
                )
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
                self._finish_closing_lease(
                    closing,
                    terminal_state,
                    detail="workspace cleanup was cancelled",
                )
                raise
            except BaseException:
                self._finish_closing_lease(
                    closing,
                    WorkspaceLeaseState.CLEANUP_FAILED,
                    detail="workspace staging cleanup failed",
                )
                raise
            return self._finish_closing_lease(
                closing,
                WorkspaceLeaseState.CLOSED,
                detail="workspace staging cleanup completed",
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
            if lease.state
            in {
                WorkspaceLeaseState.ACTIVE,
                WorkspaceLeaseState.CLOSING,
                WorkspaceLeaseState.CLEANUP_FAILED,
            }
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


__all__ = [
    "WorkspaceApplyError",
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceLeaseRequest",
    "WorkspaceService",
    "WorkspaceSourceError",
]
