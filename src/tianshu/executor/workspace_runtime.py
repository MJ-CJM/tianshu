"""Executor-owned workspace lease preparation and terminal disposition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from tianshu.executor.workspace_context import BoundWorkspace
from tianshu.executor.workspace_policy import (
    WORKSPACE_MAIN_SOURCE_ID,
    WorkspacePolicyValidationError,
    validate_workspace_policy,
)
from tianshu.executor.workspace_service import WorkspaceLeaseRequest, WorkspaceService
from tianshu.models.common import TaskStatus
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.memorial import Memorial
from tianshu.models.workspace import CanonicalChangeSet, WorkspaceLeaseState
from tianshu.storage import Storage

_MAX_PARENT_DEPTH = 64


async def complete_workspace_lifecycle[T](
    awaitable: Awaitable[T],
) -> tuple[T, asyncio.CancelledError | None]:
    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(task), cancellation
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            if task.done():
                return task.result(), cancellation


async def shield_workspace_lifecycle[T](awaitable: Awaitable[T]) -> T:
    result, cancellation = await complete_workspace_lifecycle(awaitable)
    if cancellation is not None:
        raise cancellation
    return result


class WorkspaceContractError(RuntimeError):
    """The effective workspace policy cannot be materialized safely."""


@dataclass(frozen=True)
class WorkspacePreparation:
    effective: EffectiveGovernanceContractV1
    bound: BoundWorkspace | None


@dataclass(frozen=True)
class WorkspaceTerminalEvidence:
    lease_id: str | None = None
    change_set: CanonicalChangeSet | None = None
    lease_state: WorkspaceLeaseState | None = None
    capture_error: str | None = None
    cleanup_error: str | None = None

    def event_payload(self) -> dict[str, object]:
        return {
            "workspace_lease_id": self.lease_id,
            "workspace_change_set_id": self.change_set.id if self.change_set else None,
            "workspace_change_count": (
                len(self.change_set.changes) if self.change_set is not None else None
            ),
            "workspace_lease_state": self.lease_state.value if self.lease_state else None,
            "workspace_capture_error": self.capture_error,
            "workspace_cleanup_error": self.cleanup_error,
        }


class WorkspaceRuntime:
    def __init__(
        self,
        *,
        storage: Storage,
        service: WorkspaceService | None,
        workspace_sources: Mapping[str, Path] | None,
    ) -> None:
        sources = {
            source_id: Path(source_root).expanduser().resolve()
            for source_id, source_root in (workspace_sources or {}).items()
        }
        if service is not None and set(sources) != {WORKSPACE_MAIN_SOURCE_ID}:
            raise ValueError("workspace service requires exactly the workspace-main source map")
        if service is None and sources:
            raise ValueError("workspace sources require a workspace service")
        self._storage = storage
        self._service = service
        self._sources = sources

    async def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        memorial: Memorial,
    ) -> WorkspacePreparation:
        workspace = effective.workspace
        recovery = effective.recovery
        try:
            validate_workspace_policy(
                workspace,
                recovery,
                resolved_source_id=effective.resolved_source_id,
                resolved_base_revision=effective.resolved_base_revision,
            )
        except WorkspacePolicyValidationError as exc:
            raise WorkspaceContractError(str(exc)) from exc

        if workspace.staging_mode == "legacy_shared":
            return WorkspacePreparation(effective=effective, bound=None)

        if self._service is None:
            raise WorkspaceContractError("isolated execution requires a workspace service")

        lineage_root_run_id, parent_run_id, attempt = self._lineage(memorial)
        if workspace.staging_mode == "ephemeral":
            request = WorkspaceLeaseRequest(
                run_id=memorial.id,
                lineage_root_run_id=lineage_root_run_id,
                parent_run_id=parent_run_id,
                attempt=attempt,
                source_root=None,
                base_revision=None,
                apply_mode="none",
            )
        elif workspace.staging_mode == "isolated":
            request = WorkspaceLeaseRequest(
                run_id=memorial.id,
                lineage_root_run_id=lineage_root_run_id,
                parent_run_id=parent_run_id,
                attempt=attempt,
                source_root=self._sources[WORKSPACE_MAIN_SOURCE_ID],
                base_revision=effective.resolved_base_revision or workspace.base_revision,
                apply_mode="governed",
            )
        else:  # pragma: no cover - Pydantic constrains the literal
            raise WorkspaceContractError(f"unsupported workspace mode: {workspace.staging_mode}")

        try:
            lease = await self._service.create_lease(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise WorkspaceContractError(f"workspace lease creation failed: {exc}") from exc
        resolved_effective = effective
        if lease.base_revision is not None:
            resolved_effective = effective.model_copy(
                update={"resolved_base_revision": lease.base_revision}
            )
        try:
            bound = BoundWorkspace(
                lease=lease,
                effective_contract=resolved_effective,
            )
        except BaseException as exc:
            with suppress(BaseException):
                await shield_workspace_lifecycle(
                    self._service.close_lease(
                        lease.id,
                        run_id=lease.run_id,
                    )
                )
            if not isinstance(exc, Exception):
                raise
            raise WorkspaceContractError(f"workspace binding failed: {exc}") from exc
        return WorkspacePreparation(
            effective=resolved_effective,
            bound=bound,
        )

    async def finalize(
        self,
        bound: BoundWorkspace | None,
        status: TaskStatus,
    ) -> WorkspaceTerminalEvidence:
        if bound is None or self._service is None:
            return WorkspaceTerminalEvidence()
        return await shield_workspace_lifecycle(self._finalize(bound, status))

    async def _finalize(
        self,
        bound: BoundWorkspace,
        status: TaskStatus,
    ) -> WorkspaceTerminalEvidence:
        assert self._service is not None
        lease = bound.lease
        change_set: CanonicalChangeSet | None = None
        capture_error: str | None = None
        capture_cancelled: asyncio.CancelledError | None = None
        if lease.source_kind == "git":
            try:
                change_set = await self._service.capture_change_set(
                    lease.id,
                    run_id=lease.run_id,
                )
            except asyncio.CancelledError as exc:
                capture_cancelled = exc
                capture_error = "workspace change capture was cancelled"
            except Exception as exc:
                capture_error = str(exc)

        keep_active = status is TaskStatus.COMPLETED and (
            capture_error is not None or (change_set is not None and bool(change_set.changes))
        )
        cleanup_error: str | None = None
        if not keep_active:
            try:
                await shield_workspace_lifecycle(
                    self._service.close_lease(
                        lease.id,
                        run_id=lease.run_id,
                    )
                )
            except Exception as exc:
                cleanup_error = str(exc)

        current = self._storage.get_workspace_lease(lease.id)
        evidence = WorkspaceTerminalEvidence(
            lease_id=lease.id,
            change_set=change_set,
            lease_state=current.state if current is not None else None,
            capture_error=capture_error,
            cleanup_error=cleanup_error,
        )
        if capture_cancelled is not None:
            raise capture_cancelled
        return evidence

    def _lineage(self, memorial: Memorial) -> tuple[str, str | None, int]:
        parent_id = memorial.parent_memorial_id
        if parent_id is None:
            return memorial.id, None, 0

        visited = {memorial.id}
        for _ in range(_MAX_PARENT_DEPTH):
            if parent_id in visited:
                raise WorkspaceContractError("workspace memorial lineage contains a cycle")
            visited.add(parent_id)
            parent = self._storage.get_memorial(parent_id)
            if parent is None:
                raise WorkspaceContractError("workspace memorial lineage has a missing parent")
            if parent.edict_id != memorial.edict_id:
                raise WorkspaceContractError("workspace memorial lineage crosses edicts")
            parent_lease = self._storage.get_workspace_lease_by_run(parent.id)
            if parent_lease is not None:
                return (
                    parent_lease.lineage_root_run_id,
                    parent_lease.run_id,
                    parent_lease.attempt + 1,
                )
            parent_id = parent.parent_memorial_id
            if parent_id is None:
                return memorial.id, None, 0
        raise WorkspaceContractError("workspace memorial lineage exceeds the depth limit")


__all__ = [
    "WORKSPACE_MAIN_SOURCE_ID",
    "WorkspaceContractError",
    "WorkspacePreparation",
    "WorkspaceRuntime",
    "WorkspaceTerminalEvidence",
    "complete_workspace_lifecycle",
    "shield_workspace_lifecycle",
]
