"""Persist-once challenger routing and verified per-run overlay binding."""

from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from tianshu.evolution.runtime_context import (
    EvolutionRuntimeContext,
    RunBindingContextV1,
    bind_evolution_runtime,
    bind_run_binding,
)
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.canonical import JsonValue, canonical_json_bytes
from tianshu.models.evolution_candidate import CandidateVersionRefV1
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    EvolutionRunEvidenceV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
)
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryDecodeError,
)
from tianshu.storage.system_snapshot_repo import (
    SystemBinding,
    SystemBindingWriteResult,
    SystemSnapshotRepository,
    SystemSnapshotRepositoryDecodeError,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork

logger = logging.getLogger(__name__)


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


class _GenerationSelection(Protocol):
    generation_ids: tuple[str, ...]
    by_scope: Mapping[str, str]
    executor_manifest_digests: Mapping[str, str]


class _GenerationController(Protocol):
    def resolve_for_binding_current(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...] = (),
        inherit_pinned: bool = False,
        allow_ready: bool = False,
    ) -> _GenerationSelection: ...

    def release_binding(self, attempt_id: str) -> bool: ...


BucketCalculator = Callable[[str, str, bytes], int]
PayloadResolver = Callable[
    [sqlite3.Connection, CandidateVersionRefV1, EffectiveEvolutionOverlayV1],
    dict[str, JsonValue],
]
Assignment = RunAssignmentV1 | LegacyRunAssignmentV1
BeforeInsert = Callable[[Assignment], None]


class EvolutionRuntimeUnavailable(ValueError):
    """A selected governed payload cannot be bound for execution."""


class RunAssignmentUnavailable(EvolutionRuntimeUnavailable):
    """A durable assignment deterministically failed integrity decoding."""


class GenerationBindingUnavailable(EvolutionRuntimeUnavailable):
    """A new runtime-generation binding could not be established safely."""


class GenerationRetired(EvolutionRuntimeUnavailable):
    """A continuity-pinned runtime generation is no longer usable."""


def allocation_bucket(memorial_id: str, allocation_seed_id: str, secret: bytes) -> int:
    """Return the frozen HMAC-SHA256 bucket in the inclusive range 0..9999."""

    if not memorial_id.strip() or not allocation_seed_id.strip():
        raise ValueError("routing identities must be non-blank")
    if not secret:
        raise ValueError("allocation secret must not be empty")
    identity = canonical_json_bytes(
        {
            "allocation_seed_id": allocation_seed_id,
            "memorial_id": memorial_id,
            "schema_version": 2,
        }
    )
    digest = hmac.new(secret, identity, hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % 10_000


def selects_challenger(*, bucket: int, allocation_basis_points: int) -> bool:
    if type(bucket) is not int or not 0 <= bucket < 10_000:
        raise ValueError("bucket must be in the range 0..9999")
    if type(allocation_basis_points) is not int or not 0 <= allocation_basis_points <= 10_000:
        raise ValueError("allocation_basis_points must be in the range 0..10000")
    return allocation_basis_points > 0 and bucket < allocation_basis_points


class ChallengerRouter:
    """Load an existing assignment first; otherwise route and insert in the caller UoW."""

    def __init__(
        self,
        storage: _Storage,
        *,
        allocation_secret: bytes | None = None,
        bucket_calculator: BucketCalculator = allocation_bucket,
        payload_resolver: PayloadResolver | None = None,
        snapshot_resolver: Callable[[], SystemSnapshotResolver | None] | None = None,
        generation_controller: Callable[[], _GenerationController | None] | None = None,
        before_insert: BeforeInsert | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._allocation_secret = allocation_secret
        self._bucket_calculator = bucket_calculator
        self._payload_resolver = payload_resolver
        self._snapshot_resolver = snapshot_resolver
        self._generation_controller = generation_controller
        self._before_insert = before_insert
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = EvolutionRepository()
        self._snapshot_repository = SystemSnapshotRepository()

    def assign(self, memorial_id: str) -> Assignment:
        with self._storage.unit_of_work() as unit_of_work:
            assignment = self.assign_current(
                unit_of_work,
                memorial_id=memorial_id,
            )
            unit_of_work.commit()
            return assignment

    def assign_current(
        self,
        unit_of_work: SqliteUnitOfWork,
        *,
        memorial_id: str,
        created_at: datetime | None = None,
        inherit_from_memorial_id: str | None = None,
    ) -> Assignment:
        if not memorial_id.strip():
            raise ValueError("memorial_id must be non-blank")
        connection = unit_of_work.connection
        existing = self._repository.get_assignment(connection, memorial_id)
        if existing is not None:
            return existing[0]
        if inherit_from_memorial_id is not None:
            return self._inherit_assignment(
                connection,
                memorial_id=memorial_id,
                inherit_from_memorial_id=inherit_from_memorial_id,
                created_at=created_at,
            )
        candidate = self._repository.get_routable_candidate(connection)
        if candidate is None:
            assigned_at = (created_at or self._clock()).astimezone(UTC)
            legacy = LegacyRunAssignmentV1(
                assignment_id=self._assignment_id(memorial_id),
                memorial_id=memorial_id,
                created_at=assigned_at,
            )
            if self._before_insert is not None:
                self._before_insert(legacy)
            return self._repository.insert_legacy_assignment(connection, legacy)
        routing = candidate.routing
        assert routing is not None
        bucket = self._bucket(memorial_id, routing.allocation_seed_id)
        selected_challenger = selects_challenger(
            bucket=bucket,
            allocation_basis_points=routing.allocation_basis_points,
        )
        selected_ref = candidate.candidate if selected_challenger else candidate.base
        assigned_at = (created_at or self._clock()).astimezone(UTC)
        assignment_id = self._assignment_id(memorial_id)
        assignment = RunAssignmentV1(
            assignment_id=assignment_id,
            memorial_id=memorial_id,
            candidate_id=candidate.candidate_id,
            champion_ref=candidate.base,
            selected_ref=selected_ref,
            routing_version=routing.routing_version,
            bucket=bucket,
            created_at=assigned_at,
        )
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id=assignment_id,
            kind=candidate.kind,
            subject_key=candidate.subject_key,
            artifact_digest=selected_ref.artifact_digest,
            canonical_digest=selected_ref.canonical_digest,
        )
        self._resolve_payload(connection, selected_ref, overlay)
        if self._before_insert is not None:
            self._before_insert(assignment)
        return self._repository.insert_assignment(connection, assignment, overlay)

    def _inherit_assignment(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        inherit_from_memorial_id: str,
        created_at: datetime | None,
    ) -> Assignment:
        if not inherit_from_memorial_id.strip():
            raise ValueError("inherit_from_memorial_id must be non-blank")
        inherited = self._repository.get_assignment(connection, inherit_from_memorial_id)
        if inherited is None:
            parent_exists = connection.execute(
                "SELECT 1 FROM memorials WHERE id = ?",
                (inherit_from_memorial_id,),
            ).fetchone()
            if parent_exists is None:
                raise LookupError("parent run assignment not found")
            assigned_at = (created_at or self._clock()).astimezone(UTC)
            legacy_assignment = LegacyRunAssignmentV1(
                assignment_id=self._assignment_id(memorial_id),
                memorial_id=memorial_id,
                created_at=assigned_at,
            )
            if self._before_insert is not None:
                self._before_insert(legacy_assignment)
            return self._repository.insert_legacy_assignment(connection, legacy_assignment)
        parent, parent_overlay = inherited
        assignment_id = self._assignment_id(memorial_id)
        assigned_at = (created_at or self._clock()).astimezone(UTC)
        if isinstance(parent, LegacyRunAssignmentV1):
            legacy_assignment = parent.model_copy(
                update={
                    "assignment_id": assignment_id,
                    "memorial_id": memorial_id,
                    "created_at": assigned_at,
                }
            )
            if self._before_insert is not None:
                self._before_insert(legacy_assignment)
            return self._repository.insert_legacy_assignment(connection, legacy_assignment)
        assert parent_overlay is not None
        governed_assignment = parent.model_copy(
            update={
                "assignment_id": assignment_id,
                "memorial_id": memorial_id,
                "created_at": assigned_at,
            }
        )
        overlay = parent_overlay.model_copy(update={"assignment_id": assignment_id})
        self._resolve_payload(connection, governed_assignment.selected_ref, overlay)
        if self._before_insert is not None:
            self._before_insert(governed_assignment)
        return self._repository.insert_assignment(connection, governed_assignment, overlay)

    def get(self, memorial_id: str) -> Assignment | None:
        loaded = self._load(memorial_id)
        return loaded[0] if loaded is not None else None

    def overlay_for(self, memorial_id: str) -> EffectiveEvolutionOverlayV1 | None:
        loaded = self._load(memorial_id)
        return loaded[1] if loaded is not None else None

    def prebind_runtime_current(
        self,
        unit_of_work: SqliteUnitOfWork,
        *,
        memorial_id: str,
        attempt_id: str,
    ) -> RunBindingContextV1 | None:
        """Persist a trigger-time attempt binding in the caller-owned transaction.

        The temporary process-local generation lease is released while the SQLite
        write transaction is still held.  Once the caller commits, durable
        retention protects the exact binding until dispatch reserves it again.
        """

        if not memorial_id.strip() or not attempt_id.strip():
            raise ValueError("runtime binding identities must be non-blank")
        connection = unit_of_work.connection
        try:
            existing = self._snapshot_repository.get_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
            existing_generation_binding = self._snapshot_repository.get_generation_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
        except SystemSnapshotRepositoryDecodeError as exc:
            raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
        if existing is not None:
            if existing_generation_binding is None:
                try:
                    self._snapshot_repository.insert_generation_binding(
                        connection,
                        memorial_id=memorial_id,
                        attempt_id=attempt_id,
                        generation_ids=existing.generation_ids,
                    )
                except Exception as exc:
                    raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
            elif (
                not existing_generation_binding.resolved
                or existing_generation_binding.generation_ids != existing.generation_ids
            ):
                raise GenerationBindingUnavailable("generation_binding_unavailable")
            return self._binding_context(existing)

        try:
            loaded = self._repository.get_assignment(connection, memorial_id)
        except EvolutionRepositoryDecodeError as exc:
            raise RunAssignmentUnavailable("run_assignment_unavailable") from exc
        if loaded is None:
            raise LookupError("run assignment not found")
        assignment, overlay = loaded
        if isinstance(assignment, RunAssignmentV1):
            assert overlay is not None
            self._resolve_payload(connection, assignment.selected_ref, overlay)
        else:
            overlay = None

        controller = self._get_generation_controller()
        try:
            return self._bind_system_snapshot(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                assignment=assignment,
                overlay=overlay,
                persist_generation_selection=True,
            )
        finally:
            if controller is not None:
                controller.release_binding(attempt_id)

    def evidence_for(self, memorial_id: str) -> EvolutionRunEvidenceV1:
        loaded = self._load(memorial_id)
        if loaded is None:
            raise LookupError("run assignment not found")
        assignment, overlay = loaded
        if not isinstance(assignment, RunAssignmentV1) or overlay is None:
            raise LookupError("run has no governed evolution assignment")
        return EvolutionRunEvidenceV1(
            assignment=assignment,
            overlay=overlay,
            candidate_id=assignment.candidate_id,
            routing_version=assignment.routing_version,
        )

    @contextmanager
    def bind_runtime(
        self,
        memorial_id: str,
        *,
        attempt_id: str | None = None,
    ) -> Iterator[EvolutionRuntimeContext | None]:
        legacy = False
        run_binding: RunBindingContextV1 | None = None
        with self._storage.unit_of_work() as unit_of_work:
            try:
                loaded = self._repository.get_assignment(
                    unit_of_work.connection,
                    memorial_id,
                )
            except EvolutionRepositoryDecodeError as exc:
                raise RunAssignmentUnavailable("run_assignment_unavailable") from exc
            if loaded is None:
                raise LookupError("run assignment not found")
            assignment, overlay = loaded
            if isinstance(assignment, LegacyRunAssignmentV1):
                legacy = True
                run_binding = self._bind_system_snapshot(
                    unit_of_work.connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    assignment=assignment,
                    overlay=None,
                )
                unit_of_work.commit()
            else:
                assert overlay is not None
                payload = self._resolve_payload(
                    unit_of_work.connection,
                    assignment.selected_ref,
                    overlay,
                )
                run_binding = self._bind_system_snapshot(
                    unit_of_work.connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    assignment=assignment,
                    overlay=overlay,
                )
                unit_of_work.commit()
        if legacy:
            if run_binding is None:
                yield None
            else:
                with bind_run_binding(run_binding):
                    yield None
            return
        assert isinstance(assignment, RunAssignmentV1)
        assert overlay is not None
        context = EvolutionRuntimeContext(
            assignment=assignment,
            overlay=overlay,
            selected_payload=payload,
            system_snapshot=(run_binding.system_snapshot if run_binding is not None else None),
        )
        if run_binding is None:
            with bind_evolution_runtime(context):
                yield context
        else:
            with bind_run_binding(run_binding), bind_evolution_runtime(context):
                yield context

    def _bind_system_snapshot(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str | None,
        assignment: Assignment,
        overlay: EffectiveEvolutionOverlayV1 | None,
        persist_generation_selection: bool = False,
    ) -> RunBindingContextV1 | None:
        if attempt_id is None:
            return None
        try:
            existing = self._snapshot_repository.get_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
            exact_generation_binding = self._snapshot_repository.get_generation_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
        except SystemSnapshotRepositoryDecodeError as exc:
            raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
        if exact_generation_binding is not None and not exact_generation_binding.resolved:
            raise GenerationRetired("generation_retired")
        if existing is not None:
            if (
                exact_generation_binding is not None
                and exact_generation_binding.generation_ids != existing.generation_ids
            ):
                raise GenerationRetired("generation_retired")
            if existing.generation_ids:
                controller = self._get_generation_controller()
                if controller is None:
                    raise GenerationRetired("generation_retired")
                exact_selection = self._resolve_generation_selection(
                    controller,
                    connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    pinned_ids=existing.generation_ids,
                    inherited=True,
                    inherit_pinned=False,
                )
                self._validate_pinned_executor_snapshot(existing, exact_selection)
            return self._binding_context(existing)

        exact_generation_ids = (
            exact_generation_binding.generation_ids
            if exact_generation_binding is not None
            else None
        )
        pinned_ids = (
            exact_generation_ids
            if exact_generation_ids is not None
            else self._continuity_generation_ids(connection, memorial_id)
        )
        selection: _GenerationSelection | None = None
        controller = self._get_generation_controller()
        if exact_generation_ids == ():
            selection = None
        elif controller is not None:
            selection = self._resolve_generation_selection(
                controller,
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                pinned_ids=pinned_ids,
                inherited=bool(pinned_ids),
                inherit_pinned=(exact_generation_ids is None and bool(pinned_ids)),
            )
        elif pinned_ids:
            raise GenerationRetired("generation_retired")

        if persist_generation_selection and exact_generation_binding is None:
            generation_ids = selection.generation_ids if selection is not None else ()
            try:
                exact_generation_binding = self._snapshot_repository.insert_generation_binding(
                    connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    generation_ids=generation_ids,
                )
            except Exception as exc:
                raise GenerationBindingUnavailable("generation_binding_unavailable") from exc

        generation_context = (
            RunBindingContextV1(
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                system_snapshot=None,
                generation_ids=exact_generation_binding.generation_ids or (),
            )
            if exact_generation_binding is not None
            else None
        )

        if self._snapshot_resolver is None:
            if selection is not None and selection.generation_ids:
                raise GenerationBindingUnavailable("generation_binding_unavailable")
            return generation_context
        try:
            resolver = self._snapshot_resolver()
            if resolver is None:
                if selection is not None and selection.generation_ids:
                    raise GenerationBindingUnavailable("generation_binding_unavailable")
                return generation_context
            snapshot = resolver.resolve_for_run(
                assignment,
                overlay,
                executor_digests=(
                    selection.executor_manifest_digests if selection is not None else None
                ),
            )
            generation_ids = selection.generation_ids if selection is not None else ()
            result: SystemBindingWriteResult | None
            if generation_ids:
                result = self._snapshot_repository.insert_binding(
                    connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    snapshot=snapshot,
                    generation_ids=generation_ids,
                )
            else:
                result = self._snapshot_repository.try_insert_binding(
                    connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    snapshot=snapshot,
                    generation_ids=(),
                )
        except GenerationBindingUnavailable:
            raise
        except Exception:
            recorded = self._snapshot_repository.record_event(
                connection,
                action="system_snapshot_binding_failed",
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
            if not recorded:
                logger.warning(
                    "system snapshot resolver failure could not be audited",
                )
            if selection is not None and selection.generation_ids:
                raise GenerationBindingUnavailable("generation_binding_unavailable") from None
            return generation_context
        if result is None:
            logger.warning("system snapshot binding was not persisted")
            return generation_context
        return self._binding_context(result.binding)

    def _get_generation_controller(self) -> _GenerationController | None:
        if self._generation_controller is None:
            return None
        return self._generation_controller()

    @staticmethod
    def _resolve_generation_selection(
        controller: _GenerationController,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
        pinned_ids: tuple[str, ...],
        inherited: bool,
        inherit_pinned: bool,
    ) -> _GenerationSelection:
        try:
            selection = controller.resolve_for_binding_current(
                connection,
                memorial_id,
                attempt_id,
                pinned_ids=pinned_ids,
                inherit_pinned=inherit_pinned,
            )
        except Exception as exc:
            if inherited:
                raise GenerationRetired("generation_retired") from exc
            raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
        if pinned_ids and not inherit_pinned and selection.generation_ids != pinned_ids:
            raise GenerationRetired("generation_retired")
        return selection

    def _continuity_generation_ids(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[str, ...]:
        try:
            generation_ids = self._snapshot_repository.get_continuity_generation_ids(
                connection,
                memorial_id,
            )
        except SystemSnapshotRepositoryDecodeError as exc:
            raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
        return generation_ids or ()

    @staticmethod
    def _validate_pinned_executor_snapshot(
        binding: SystemBinding,
        selection: _GenerationSelection,
    ) -> None:
        persisted = {
            component.removeprefix("executor:"): digest
            for component, digest in binding.snapshot.components.items()
            if component.startswith("executor:")
        }
        expected: dict[str, str] = {}
        for scope in selection.by_scope:
            if not scope.startswith("executor:"):
                continue
            adapter_id = scope.removeprefix("executor:")
            digest = selection.executor_manifest_digests.get(adapter_id)
            if digest is None:
                raise GenerationRetired("generation_retired")
            expected[adapter_id] = digest
        if any(persisted.get(adapter_id) != digest for adapter_id, digest in expected.items()):
            raise GenerationRetired("generation_retired")

    @staticmethod
    def _binding_context(binding: SystemBinding) -> RunBindingContextV1:
        return RunBindingContextV1(
            memorial_id=binding.memorial_id,
            attempt_id=binding.attempt_id,
            system_snapshot=binding.snapshot,
            generation_ids=binding.generation_ids,
        )

    def _load(
        self, memorial_id: str
    ) -> tuple[Assignment, EffectiveEvolutionOverlayV1 | None] | None:
        with self._storage.unit_of_work() as unit_of_work:
            loaded = self._repository.get_assignment(unit_of_work.connection, memorial_id)
            unit_of_work.commit()
            return loaded

    def _bucket(self, memorial_id: str, seed_id: str) -> int:
        secret = self._allocation_secret or b""
        bucket = self._bucket_calculator(memorial_id, seed_id, secret)
        if type(bucket) is not int or not 0 <= bucket < 10_000:
            raise ValueError("bucket calculator returned an out-of-range value")
        return bucket

    @staticmethod
    def _assignment_id(memorial_id: str) -> str:
        return "assignment:" + hashlib.sha256(memorial_id.encode()).hexdigest()

    def _resolve_payload(
        self,
        connection: sqlite3.Connection,
        selected_ref: CandidateVersionRefV1,
        overlay: EffectiveEvolutionOverlayV1,
    ) -> dict[str, JsonValue]:
        if self._payload_resolver is None:
            raise EvolutionRuntimeUnavailable("candidate_overlay_unavailable")
        try:
            return self._payload_resolver(connection, selected_ref, overlay)
        except (LookupError, RuntimeError, TypeError, ValueError) as exc:
            raise EvolutionRuntimeUnavailable("candidate_overlay_unavailable") from exc


__all__ = [
    "ChallengerRouter",
    "EvolutionRuntimeUnavailable",
    "GenerationBindingUnavailable",
    "GenerationRetired",
    "RunAssignmentUnavailable",
    "allocation_bucket",
    "selects_challenger",
]
