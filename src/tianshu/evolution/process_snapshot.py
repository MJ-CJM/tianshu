"""Startup governance for the process-level SystemSnapshot generation."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from tianshu.models.runtime_generation import (
    PROCESS_GENERATION_SCOPE,
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeGenerationV1,
)
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.generation_repo import (
    GenerationRepository,
    GenerationRepositoryDecodeError,
)
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

type ProcessSnapshotAction = Literal[
    "initialized",
    "unchanged",
    "advanced",
    "rolled_back",
]
type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]

_PROCESS_SNAPSHOT_ACTOR = hashlib.sha256(b"tianshu.process-snapshot-bootstrap").hexdigest()
_DRIFT_AUDIT_SAVEPOINT = "process_snapshot_drift_audit"


class _SnapshotResolver(Protocol):
    def resolve(self) -> SystemSnapshotV1: ...


class ProcessSnapshotStartupError(RuntimeError):
    """A stable, disclosure-safe process snapshot startup refusal."""

    reason_code: str


class ProcessSnapshotTargetUnavailable(ProcessSnapshotStartupError):
    reason_code = "target_snapshot_unavailable"

    def __init__(self, *, target_digest: str, last_good_digest: str | None) -> None:
        self.target_digest = target_digest
        self.last_good_digest = last_good_digest
        suffix = f" last_good={last_good_digest}" if last_good_digest is not None else ""
        super().__init__(f"{self.reason_code}: target={target_digest}{suffix}")


class ProcessSnapshotDriftError(ProcessSnapshotStartupError):
    reason_code = "system_snapshot_drift"

    def __init__(
        self,
        *,
        target_digest: str,
        actual_digest: str,
        last_good_digest: str | None,
        differing_components: tuple[str, ...] | None,
    ) -> None:
        self.target_digest = target_digest
        self.actual_digest = actual_digest
        self.last_good_digest = last_good_digest
        self.differing_components = differing_components
        last_good = last_good_digest or "none"
        components = (
            ",".join(differing_components) if differing_components is not None else "unavailable"
        )
        super().__init__(
            f"{self.reason_code}: target={target_digest} actual={actual_digest} "
            f"last_good={last_good} differing_components={components}"
        )


@dataclass(frozen=True, slots=True)
class ProcessSnapshotStartupReport:
    action: ProcessSnapshotAction
    snapshot_digest: str
    active_generation_id: str
    last_good_generation_id: str
    drifted: bool
    target_digest: str
    differing_components: tuple[str, ...] | None


class ProcessSnapshotBootstrap:
    """Atomically reconcile the current process snapshot with its V32 pointer."""

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        resolver: _SnapshotResolver,
        strict: bool,
        target_digest: str | None,
        repository: GenerationRepository | None = None,
        snapshot_repository: SystemSnapshotRepository | None = None,
        generation_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._resolver = resolver
        self._strict = strict
        self._target_digest = target_digest
        self._repository = repository or GenerationRepository()
        self._snapshot_repository = snapshot_repository or SystemSnapshotRepository()
        self._generation_id_factory = generation_id_factory or (lambda: f"rg-{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))

    def initialize(self) -> ProcessSnapshotStartupReport:
        resolved = self._resolver.resolve()
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            pointer = self._repository.get_pointer(
                connection,
                scope=PROCESS_GENERATION_SCOPE,
            )
            active_snapshot, last_good_snapshot = self._pointer_snapshots(connection, pointer)
            target_digest = (
                self._target_digest
                or (active_snapshot.digest if active_snapshot is not None else None)
                or resolved.digest
            )
            target_snapshot = self._known_snapshot(
                connection,
                target_digest=target_digest,
                resolved=resolved,
            )
            if self._target_digest is not None and target_snapshot is None:
                raise ProcessSnapshotTargetUnavailable(
                    target_digest=target_digest,
                    last_good_digest=(
                        last_good_snapshot.digest if last_good_snapshot is not None else None
                    ),
                )

            differing_components = self._component_diff(target_snapshot, resolved)
            drifted = target_digest != resolved.digest
            if drifted and self._strict:
                raise ProcessSnapshotDriftError(
                    target_digest=target_digest,
                    actual_digest=resolved.digest,
                    last_good_digest=(
                        last_good_snapshot.digest if last_good_snapshot is not None else None
                    ),
                    differing_components=differing_components,
                )

            if active_snapshot is not None and active_snapshot.digest == resolved.digest:
                if drifted:
                    self._record_drift(connection, resolved.digest)
                assert pointer is not None
                unit_of_work.commit()
                return self._report(
                    action="unchanged",
                    snapshot=resolved,
                    pointer=pointer,
                    drifted=drifted,
                    target_digest=target_digest,
                    differing_components=differing_components,
                )

            self._snapshot_repository.insert_snapshot(connection, resolved)
            self._repository.insert_process_release(connection, resolved)

            if (
                pointer is not None
                and active_snapshot is not None
                and last_good_snapshot is not None
                and active_snapshot.digest != resolved.digest
                and last_good_snapshot.digest == resolved.digest
            ):
                rollback = self._repository.rollback_to_last_good(
                    connection,
                    scope=PROCESS_GENERATION_SCOPE,
                    expected_pointer_version=pointer.version,
                    updated_at=self._now(),
                )
                self._repository.dispose_if_unreferenced(
                    connection,
                    scope=PROCESS_GENERATION_SCOPE,
                    generation_id=rollback.draining.generation_id,
                    expected_version=rollback.draining.version,
                    updated_at=self._now(),
                )
                current_pointer = rollback.pointer
                action: ProcessSnapshotAction = "rolled_back"
            else:
                previous_last_good_id = (
                    pointer.last_good_generation_id if pointer is not None else None
                )
                current_pointer = self._activate_snapshot(
                    connection,
                    snapshot=resolved,
                    pointer=pointer,
                )
                if (
                    previous_last_good_id is not None
                    and previous_last_good_id != current_pointer.last_good_generation_id
                ):
                    previous_last_good = self._repository.get_generation(
                        connection,
                        scope=PROCESS_GENERATION_SCOPE,
                        generation_id=previous_last_good_id,
                    )
                    if (
                        previous_last_good is not None
                        and previous_last_good.state is RuntimeGenerationState.DRAINING
                    ):
                        self._repository.dispose_if_unreferenced(
                            connection,
                            scope=PROCESS_GENERATION_SCOPE,
                            generation_id=previous_last_good.generation_id,
                            expected_version=previous_last_good.version,
                            updated_at=self._now(),
                        )
                action = "initialized" if pointer is None else "advanced"

            if drifted:
                self._record_drift(connection, resolved.digest)
            unit_of_work.commit()
            return self._report(
                action=action,
                snapshot=resolved,
                pointer=current_pointer,
                drifted=drifted,
                target_digest=target_digest,
                differing_components=differing_components,
            )

    def _pointer_snapshots(
        self,
        connection: sqlite3.Connection,
        pointer: GenerationPointerV1 | None,
    ) -> tuple[SystemSnapshotV1 | None, SystemSnapshotV1 | None]:
        if pointer is None:
            return None, None
        active = self._repository.get_generation(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
            generation_id=pointer.active_generation_id,
        )
        last_good = self._repository.get_generation(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
            generation_id=pointer.last_good_generation_id,
        )
        if active is None or last_good is None:
            raise GenerationRepositoryDecodeError(
                "process generation pointer roots are unavailable"
            )
        if active.state is not RuntimeGenerationState.ACTIVE:
            raise GenerationRepositoryDecodeError("process active generation is not active")
        expected_last_good_states = (
            {RuntimeGenerationState.ACTIVE}
            if active.generation_id == last_good.generation_id
            else {RuntimeGenerationState.DRAINING}
        )
        if last_good.state not in expected_last_good_states:
            raise GenerationRepositoryDecodeError("process last-good generation is not retained")
        self._repository.list_journal(
            connection,
            generation_id=active.generation_id,
        )
        if last_good.generation_id != active.generation_id:
            self._repository.list_journal(
                connection,
                generation_id=last_good.generation_id,
            )
        active_snapshot = self._repository.get_process_release(
            connection,
            release_digest=active.release_digest,
        )
        last_good_snapshot = self._repository.get_process_release(
            connection,
            release_digest=last_good.release_digest,
        )
        if active_snapshot is None or last_good_snapshot is None:
            raise GenerationRepositoryDecodeError("process generation release is unavailable")
        return active_snapshot, last_good_snapshot

    def _known_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        target_digest: str,
        resolved: SystemSnapshotV1,
    ) -> SystemSnapshotV1 | None:
        if target_digest == resolved.digest:
            return resolved
        process_release = self._repository.get_process_release(
            connection,
            release_digest=target_digest,
        )
        if process_release is not None:
            return process_release
        return self._snapshot_repository.get_snapshot(connection, target_digest)

    def _activate_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        snapshot: SystemSnapshotV1,
        pointer: GenerationPointerV1 | None,
    ) -> GenerationPointerV1:
        now = self._now()
        staged = self._repository.insert_staged(
            connection,
            RuntimeGenerationV1(
                generation_id=self._generation_id_factory(),
                scope=PROCESS_GENERATION_SCOPE,
                release_digest=snapshot.digest,
                state=RuntimeGenerationState.STAGED,
                version=1,
                created_at=now,
                updated_at=now,
            ),
        )
        warming = self._repository.transition_pre_activation(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
            generation_id=staged.generation_id,
            target_state=RuntimeGenerationState.WARMING,
            expected_version=staged.version,
            updated_at=self._now(),
        )
        ready = self._repository.transition_pre_activation(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
            generation_id=warming.generation_id,
            target_state=RuntimeGenerationState.READY,
            expected_version=warming.version,
            updated_at=self._now(),
        )
        return self._repository.activate(
            connection,
            scope=PROCESS_GENERATION_SCOPE,
            target_generation_id=ready.generation_id,
            expected_generation_version=ready.version,
            expected_pointer_version=(pointer.version if pointer is not None else None),
            updated_at=self._now(),
        ).pointer

    @staticmethod
    def _component_diff(
        target: SystemSnapshotV1 | None,
        actual: SystemSnapshotV1,
    ) -> tuple[str, ...] | None:
        if target is None:
            return None
        return tuple(
            sorted(
                key
                for key in set(target.components) | set(actual.components)
                if target.components.get(key) != actual.components.get(key)
            )
        )

    @staticmethod
    def _record_drift(connection: sqlite3.Connection, snapshot_digest: str) -> bool:
        try:
            connection.execute(f"SAVEPOINT {_DRIFT_AUDIT_SAVEPOINT}")
            _append_system_audit_unlocked(
                connection,
                AppendSystemAuditRequest(
                    correlation_id="process-snapshot",
                    actor_digest=_PROCESS_SNAPSHOT_ACTOR,
                    action="system_snapshot_drift",
                    outcome="succeeded",
                    reason_code="system_snapshot_drift",
                    subject_kind="process_snapshot",
                    subject_digest=snapshot_digest,
                ),
            )
            connection.execute(f"RELEASE SAVEPOINT {_DRIFT_AUDIT_SAVEPOINT}")
        except Exception:
            with suppress(Exception):
                connection.execute(f"ROLLBACK TO SAVEPOINT {_DRIFT_AUDIT_SAVEPOINT}")
                connection.execute(f"RELEASE SAVEPOINT {_DRIFT_AUDIT_SAVEPOINT}")
            return False
        return True

    @staticmethod
    def _report(
        *,
        action: ProcessSnapshotAction,
        snapshot: SystemSnapshotV1,
        pointer: GenerationPointerV1,
        drifted: bool,
        target_digest: str,
        differing_components: tuple[str, ...] | None,
    ) -> ProcessSnapshotStartupReport:
        return ProcessSnapshotStartupReport(
            action=action,
            snapshot_digest=snapshot.digest,
            active_generation_id=pointer.active_generation_id,
            last_good_generation_id=pointer.last_good_generation_id,
            drifted=drifted,
            target_digest=target_digest,
            differing_components=differing_components,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("process snapshot clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "ProcessSnapshotBootstrap",
    "ProcessSnapshotDriftError",
    "ProcessSnapshotStartupError",
    "ProcessSnapshotStartupReport",
    "ProcessSnapshotTargetUnavailable",
]
