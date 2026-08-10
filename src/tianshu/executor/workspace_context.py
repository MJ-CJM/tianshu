"""Task-local binding for one durable staging workspace lease."""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.workspace import WorkspaceLease, WorkspaceLeaseState


class WorkspaceBindingError(RuntimeError):
    """The current run has no matching active workspace authority."""


def requires_workspace_binding(contract: EffectiveGovernanceContractV1) -> bool:
    workspace = contract.workspace
    return (
        workspace.staging_mode != "legacy_shared"
        or workspace.apply_mode == "governed"
        or workspace.require_clean_source
        or contract.recovery.require_restore_point
    )


@dataclass(frozen=True)
class BoundWorkspace:
    lease: WorkspaceLease
    effective_contract: EffectiveGovernanceContractV1
    tool_lock: asyncio.Lock = field(default_factory=asyncio.Lock, compare=False, repr=False)
    _lexical_root: Path = field(init=False, compare=False, repr=False)
    _resolved_root: Path = field(init=False, compare=False, repr=False)
    _device: int = field(init=False, compare=False, repr=False)
    _inode: int = field(init=False, compare=False, repr=False)
    _authorized_run_ids: set[str] = field(
        default_factory=set,
        init=False,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.lease.state is not WorkspaceLeaseState.ACTIVE:
            raise WorkspaceBindingError("workspace lease must be active")
        root = Path(self.lease.staging_root).expanduser().absolute()
        try:
            metadata = root.lstat()
            resolved = root.resolve()
            resolved_metadata = resolved.stat()
        except OSError as exc:
            raise WorkspaceBindingError("workspace staging root is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(resolved_metadata.st_mode):
            raise WorkspaceBindingError("workspace staging root is unavailable or replaced")
        object.__setattr__(self, "_lexical_root", root)
        object.__setattr__(self, "_resolved_root", resolved)
        object.__setattr__(self, "_device", resolved_metadata.st_dev)
        object.__setattr__(self, "_inode", resolved_metadata.st_ino)
        self._authorized_run_ids.add(self.lease.run_id)
        expected_base = (
            self.effective_contract.resolved_base_revision
            or self.effective_contract.workspace.base_revision
        )
        if expected_base is not None and expected_base != self.lease.base_revision:
            raise WorkspaceBindingError("workspace lease base revision does not match contract")
        if self.effective_contract.workspace.apply_mode != self.lease.apply_mode:
            raise WorkspaceBindingError("workspace lease apply mode does not match contract")

    @property
    def root(self) -> Path:
        self.validate_identity()
        return self._resolved_root

    def validate_identity(self) -> None:
        try:
            lexical_metadata = self._lexical_root.lstat()
            resolved = self._lexical_root.resolve()
            current = resolved.stat()
        except OSError as exc:
            raise WorkspaceBindingError("bound workspace root identity changed") from exc
        if (
            stat.S_ISLNK(lexical_metadata.st_mode)
            or resolved != self._resolved_root
            or (current.st_dev, current.st_ino) != (self._device, self._inode)
        ):
            raise WorkspaceBindingError("bound workspace root identity changed")

    def authorize_run(self, run_id: str) -> None:
        """Authorize one known child correlation to share this lease."""
        if not run_id:
            raise ValueError("authorized run id must not be empty")
        self._authorized_run_ids.add(run_id)

    def is_run_authorized(self, run_id: str) -> bool:
        return run_id in self._authorized_run_ids


_current_workspace: ContextVar[BoundWorkspace | None] = ContextVar(
    "current_workspace",
    default=None,
)

#: 官员专属工作区根（issue #33）。与 lease 绑定是两回事：lease 是治理 apply 的
#: 隔离暂存（含 source authority 校验），本 override 只改默认沙箱边界。
#: 解析顺序见 resolve_workspace_root：lease > persona override > 进程默认。
_workspace_root_override: ContextVar[Path | None] = ContextVar(
    "workspace_root_override",
    default=None,
)


@contextmanager
def bind_workspace_root_override(root: Path) -> Iterator[None]:
    token = _workspace_root_override.set(Path(root).expanduser().resolve())
    try:
        yield
    finally:
        _workspace_root_override.reset(token)


@contextmanager
def bind_workspace(workspace: BoundWorkspace) -> Iterator[None]:
    token = _current_workspace.set(workspace)
    try:
        yield
    finally:
        _current_workspace.reset(token)


def get_bound_workspace() -> BoundWorkspace | None:
    return _current_workspace.get()


def require_bound_workspace(
    *,
    run_id: str | None = None,
    lease_id: str | None = None,
    effective_contract_hash: str | None = None,
    root: Path | None = None,
) -> BoundWorkspace:
    workspace = get_bound_workspace()
    if workspace is None:
        raise WorkspaceBindingError("governed execution requires a bound workspace lease")
    workspace.validate_identity()
    if workspace.lease.state is not WorkspaceLeaseState.ACTIVE:
        raise WorkspaceBindingError("bound workspace lease is not active")
    if run_id is not None and not workspace.is_run_authorized(run_id):
        raise WorkspaceBindingError("bound workspace belongs to a different run")
    if lease_id is not None and workspace.lease.id != lease_id:
        raise WorkspaceBindingError("bound workspace lease id does not match")
    if (
        effective_contract_hash is not None
        and workspace.effective_contract.content_hash != effective_contract_hash
    ):
        raise WorkspaceBindingError("bound workspace contract does not match")
    if root is not None and workspace.root != Path(root).resolve():
        raise WorkspaceBindingError("bound workspace root does not match")
    return workspace


def validate_current_workspace_binding() -> BoundWorkspace | None:
    """Validate the workspace ContextVar against the active execution, if any."""
    # Local import keeps the process gateway free to validate this module without
    # creating an import cycle at module initialization time.
    from tianshu.executor.execution_gateway import get_execution_context

    context = get_execution_context()
    workspace = get_bound_workspace()
    if context is None:
        if workspace is not None:
            workspace.validate_identity()
        return workspace
    if workspace is None and not requires_workspace_binding(context.effective_contract):
        return None
    if context.workspace_lease_id is None:
        raise WorkspaceBindingError("bound workspace lease id is missing from execution context")
    return require_bound_workspace(
        run_id=context.correlation_id,
        lease_id=context.workspace_lease_id,
        effective_contract_hash=context.effective_contract.content_hash,
    )


def resolve_workspace_root(default_root: Path) -> Path:
    """lease 绑定 > 官员工作区 override > 进程默认。

    lease 必须最先：治理 apply 的执行必须落在隔离暂存里，官员 override
    若能遮蔽 lease，staging 里的改动会写回错误的位置。
    """
    workspace = validate_current_workspace_binding()
    if workspace is not None:
        return workspace.root
    override = _workspace_root_override.get()
    if override is not None:
        return override
    return Path(default_root).resolve()


__all__ = [
    "BoundWorkspace",
    "WorkspaceBindingError",
    "bind_workspace",
    "bind_workspace_root_override",
    "get_bound_workspace",
    "require_bound_workspace",
    "requires_workspace_binding",
    "resolve_workspace_root",
    "validate_current_workspace_binding",
]
