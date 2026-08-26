"""Persist-once challenger routing and verified per-run overlay binding."""

from __future__ import annotations

import hashlib
import hmac
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from tianshu.evolution.runtime_context import (
    EvolutionRuntimeContext,
    RunBindingContextV1,
    bind_evolution_runtime,
    bind_run_binding,
    runtime_subject_key,
)
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.events import make_event
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
)
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityStatus,
    ExecutorGenerationAuthorityV1,
)
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    EvolutionRunEvidenceV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.evolution_repo import (
    EvolutionAssignmentConflict,
    EvolutionRepository,
    EvolutionRepositoryDecodeError,
)
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
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
    bundles: Mapping[str, _GenerationBundle]


class _GenerationBundle(Protocol):
    release_digest: str


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


class _ExecutorGenerationAuthorityResolver(Protocol):
    def get_current(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None: ...

    def get_by_generation(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None: ...


BucketCalculator = Callable[[str, str, bytes], int]
PayloadResolver = Callable[
    [sqlite3.Connection, CandidateVersionRefV1, EffectiveEvolutionOverlayV1],
    dict[str, JsonValue],
]
Assignment = RunAssignmentV1 | LegacyRunAssignmentV1
SubjectAssignmentRecord = tuple[SubjectRunAssignmentV1, EffectiveEvolutionOverlayV1]
BeforeInsert = Callable[[Assignment], None]


@dataclass(frozen=True, slots=True)
class _ExecutorSubjectSelection:
    candidate_id: str
    subject_key: str
    champion_ref: CandidateVersionRefV1
    selected_ref: CandidateVersionRefV1


class EvolutionRuntimeUnavailable(ValueError):
    """A selected governed payload cannot be bound for execution."""


class RunAssignmentUnavailable(EvolutionRuntimeUnavailable):
    """A durable assignment deterministically failed integrity decoding."""


class GenerationBindingUnavailable(EvolutionRuntimeUnavailable):
    """A new runtime-generation binding could not be established safely."""


class GenerationRetired(EvolutionRuntimeUnavailable):
    """A continuity-pinned runtime generation is no longer usable."""


class _ExecutorAuthorityUnavailable(ValueError):
    """The selected executor candidate has no unambiguous live authority."""


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
        routing_enabled: bool = True,
        allocation_secret: bytes | None = None,
        bucket_calculator: BucketCalculator = allocation_bucket,
        payload_resolver: PayloadResolver | None = None,
        snapshot_resolver: Callable[[], SystemSnapshotResolver | None] | None = None,
        generation_controller: Callable[[], _GenerationController | None] | None = None,
        executor_generation_authority_resolver: Callable[
            [], _ExecutorGenerationAuthorityResolver | None
        ]
        | None = None,
        before_insert: BeforeInsert | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(routing_enabled) is not bool:
            raise TypeError("routing_enabled must be a bool")
        self._storage = storage
        self._routing_enabled = routing_enabled
        self._allocation_secret = allocation_secret
        self._bucket_calculator = bucket_calculator
        self._payload_resolver = payload_resolver
        self._snapshot_resolver = snapshot_resolver
        self._generation_controller = generation_controller
        self._executor_generation_authority_resolver = executor_generation_authority_resolver
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
        assignment_set = self._repository.get_assignment_set(connection, memorial_id)
        self._repository.validate_assignment_projection(existing, assignment_set)
        if existing is not None:
            return existing[0]
        if inherit_from_memorial_id is not None:
            return self._inherit_assignment(
                connection,
                memorial_id=memorial_id,
                inherit_from_memorial_id=inherit_from_memorial_id,
                created_at=created_at,
            )
        if not self._routing_enabled:
            return self._insert_legacy(
                connection,
                memorial_id=memorial_id,
                created_at=created_at,
            )
        candidates = self._repository.get_routable_candidates(connection)
        if not candidates:
            return self._insert_legacy(
                connection,
                memorial_id=memorial_id,
                created_at=created_at,
            )
        if len(candidates) > 64:
            raise EvolutionAssignmentConflict("run assignment set exceeds 64 subjects")
        assigned_at = (created_at or self._clock()).astimezone(UTC)
        records = tuple(
            self._route_subject(
                connection,
                memorial_id=memorial_id,
                candidate=candidate,
                created_at=assigned_at,
            )
            for candidate in candidates
        )
        assignment_set = self._assignment_set(memorial_id, records)
        overlay: EffectiveEvolutionOverlayV1 | None = None
        if len(records) == 1:
            subject_assignment, _subject_overlay = records[0]
            if subject_assignment.candidate_id is None:
                raise EvolutionAssignmentConflict("governed subject assignment has no candidate")
            assignment_id = self._assignment_id(memorial_id)
            governed_assignment = RunAssignmentV1(
                assignment_id=assignment_id,
                memorial_id=memorial_id,
                candidate_id=subject_assignment.candidate_id,
                champion_ref=subject_assignment.champion_ref,
                selected_ref=subject_assignment.selected_ref,
                routing_version=subject_assignment.routing_version,
                bucket=subject_assignment.bucket,
                created_at=assigned_at,
            )
            overlay = self._overlay_for_legacy_assignment(
                governed_assignment,
                records[0][1],
            )
            assignment: Assignment = governed_assignment
        else:
            assignment = LegacyRunAssignmentV1(
                assignment_id=self._assignment_id(memorial_id),
                memorial_id=memorial_id,
                created_at=assigned_at,
            )
        if self._before_insert is not None:
            self._before_insert(assignment)
        return self._insert_assignment_bundle(
            connection,
            assignment=assignment,
            overlay=overlay,
            assignment_set=assignment_set,
            records=records,
        )

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
        inherited_set = self._repository.get_assignment_set(
            connection,
            inherit_from_memorial_id,
        )
        self._repository.validate_assignment_projection(inherited, inherited_set)
        if inherited is None:
            parent_exists = connection.execute(
                "SELECT 1 FROM memorials WHERE id = ?",
                (inherit_from_memorial_id,),
            ).fetchone()
            if parent_exists is None:
                raise LookupError("parent run assignment not found")
            return self._insert_legacy(
                connection,
                memorial_id=memorial_id,
                created_at=created_at,
            )
        parent, parent_overlay = inherited
        assignment_id = self._assignment_id(memorial_id)
        assigned_at = (created_at or self._clock()).astimezone(UTC)
        if isinstance(parent, LegacyRunAssignmentV1):
            if inherited_set is not None:
                records, converged = self._inherit_subject_records(
                    connection,
                    memorial_id=memorial_id,
                    assignment_set=inherited_set,
                    created_at=assigned_at,
                )
                legacy_assignment = parent.model_copy(
                    update={
                        "assignment_id": assignment_id,
                        "memorial_id": memorial_id,
                        "created_at": assigned_at,
                    }
                )
                if self._before_insert is not None:
                    self._before_insert(legacy_assignment)
                legacy_durable = self._insert_assignment_bundle(
                    connection,
                    assignment=legacy_assignment,
                    overlay=None,
                    assignment_set=self._assignment_set(memorial_id, records),
                    records=records,
                )
                self._record_continuity_convergence(
                    connection,
                    memorial_id=memorial_id,
                    assignments=converged,
                )
                return legacy_durable
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
        candidate = self._repository.get_candidate(connection, parent.candidate_id)
        if candidate is None:
            raise EvolutionAssignmentConflict("parent assignment candidate is unavailable")
        selected_ref = self._continuity_selected_ref(
            connection,
            candidate=candidate,
            parent_selected_ref=parent.selected_ref,
        )
        governed_assignment = parent.model_copy(
            update={
                "assignment_id": assignment_id,
                "memorial_id": memorial_id,
                "selected_ref": selected_ref,
                "created_at": assigned_at,
            }
        )
        overlay = parent_overlay.model_copy(
            update={
                "assignment_id": assignment_id,
                "artifact_digest": selected_ref.artifact_digest,
                "canonical_digest": selected_ref.canonical_digest,
            }
        )
        self._resolve_payload(connection, governed_assignment.selected_ref, overlay)
        subject_assignment = SubjectRunAssignmentV1(
            assignment_id=self._subject_assignment_id(
                memorial_id,
                candidate.kind,
                candidate.subject_key,
            ),
            memorial_id=memorial_id,
            kind=candidate.kind,
            subject_key=candidate.subject_key,
            candidate_id=governed_assignment.candidate_id,
            champion_ref=governed_assignment.champion_ref,
            selected_ref=governed_assignment.selected_ref,
            routing_version=governed_assignment.routing_version,
            bucket=governed_assignment.bucket,
            created_at=assigned_at,
        )
        subject_overlay = EffectiveEvolutionOverlayV1(
            assignment_id=subject_assignment.assignment_id,
            kind=subject_assignment.kind,
            subject_key=subject_assignment.subject_key,
            artifact_digest=subject_assignment.selected_ref.artifact_digest,
            canonical_digest=subject_assignment.selected_ref.canonical_digest,
        )
        if self._before_insert is not None:
            self._before_insert(governed_assignment)
        records = ((subject_assignment, subject_overlay),)
        governed_durable = self._insert_assignment_bundle(
            connection,
            assignment=governed_assignment,
            overlay=overlay,
            assignment_set=self._assignment_set(memorial_id, records),
            records=records,
        )
        if selected_ref != parent.selected_ref:
            self._record_continuity_convergence(
                connection,
                memorial_id=memorial_id,
                assignments=(subject_assignment,),
            )
        return governed_durable

    def _insert_legacy(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        created_at: datetime | None,
    ) -> LegacyRunAssignmentV1:
        assignment = LegacyRunAssignmentV1(
            assignment_id=self._assignment_id(memorial_id),
            memorial_id=memorial_id,
            created_at=(created_at or self._clock()).astimezone(UTC),
        )
        if self._before_insert is not None:
            self._before_insert(assignment)
        return self._repository.insert_legacy_assignment(connection, assignment)

    def _route_subject(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        candidate: EvolutionCandidateV1,
        created_at: datetime,
    ) -> SubjectAssignmentRecord:
        routing = candidate.routing
        assert routing is not None
        bucket = self._bucket(memorial_id, routing.allocation_seed_id)
        selected_ref = (
            candidate.candidate
            if selects_challenger(
                bucket=bucket,
                allocation_basis_points=routing.allocation_basis_points,
            )
            else candidate.base
        )
        assignment = SubjectRunAssignmentV1(
            assignment_id=self._subject_assignment_id(
                memorial_id,
                candidate.kind,
                candidate.subject_key,
            ),
            memorial_id=memorial_id,
            kind=candidate.kind,
            subject_key=candidate.subject_key,
            candidate_id=candidate.candidate_id,
            champion_ref=candidate.base,
            selected_ref=selected_ref,
            routing_version=routing.routing_version,
            bucket=bucket,
            created_at=created_at,
        )
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id=assignment.assignment_id,
            kind=assignment.kind,
            subject_key=assignment.subject_key,
            artifact_digest=assignment.selected_ref.artifact_digest,
            canonical_digest=assignment.selected_ref.canonical_digest,
        )
        self._resolve_payload(connection, assignment.selected_ref, overlay)
        return assignment, overlay

    @staticmethod
    def _overlay_for_legacy_assignment(
        assignment: RunAssignmentV1,
        subject_overlay: EffectiveEvolutionOverlayV1,
    ) -> EffectiveEvolutionOverlayV1:
        return subject_overlay.model_copy(update={"assignment_id": assignment.assignment_id})

    def _load_assignment_state(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[
        tuple[Assignment, EffectiveEvolutionOverlayV1 | None] | None,
        RunAssignmentSetV1 | None,
    ]:
        loaded = self._repository.get_assignment(connection, memorial_id)
        assignment_set = self._repository.get_assignment_set(connection, memorial_id)
        self._repository.validate_assignment_projection(loaded, assignment_set)
        return loaded, assignment_set

    @staticmethod
    def _records_for_assignment_set(
        assignment_set: RunAssignmentSetV1 | None,
    ) -> tuple[SubjectAssignmentRecord, ...]:
        if assignment_set is None:
            return ()
        return tuple(
            (
                assignment,
                EffectiveEvolutionOverlayV1(
                    assignment_id=assignment.assignment_id,
                    kind=assignment.kind,
                    subject_key=assignment.subject_key,
                    artifact_digest=assignment.selected_ref.artifact_digest,
                    canonical_digest=assignment.selected_ref.canonical_digest,
                ),
            )
            for assignment in assignment_set.assignments
        )

    @staticmethod
    def _snapshot_overlay_map(
        assignment: Assignment,
        overlay: EffectiveEvolutionOverlayV1 | None,
        records: tuple[SubjectAssignmentRecord, ...],
    ) -> dict[str, EffectiveEvolutionOverlayV1]:
        if not records:
            return {}
        if len(records) == 1:
            if not isinstance(assignment, RunAssignmentV1) or overlay is None:
                raise EvolutionAssignmentConflict(
                    "single-subject assignment shadow conflicts with legacy projection"
                )
            subject = records[0][0]
            return {runtime_subject_key(subject.kind, subject.subject_key): overlay}
        result: dict[str, EffectiveEvolutionOverlayV1] = {}
        for subject, subject_overlay in records:
            key = runtime_subject_key(subject.kind, subject.subject_key)
            if key in result:
                raise EvolutionAssignmentConflict("runtime subject identity conflicts")
            result[key] = subject_overlay
        return result

    def _inherit_subject_records(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        assignment_set: RunAssignmentSetV1,
        created_at: datetime,
    ) -> tuple[tuple[SubjectAssignmentRecord, ...], tuple[SubjectRunAssignmentV1, ...]]:
        records: list[SubjectAssignmentRecord] = []
        converged: list[SubjectRunAssignmentV1] = []
        for parent in assignment_set.assignments:
            if parent.candidate_id is None:
                raise EvolutionAssignmentConflict(
                    "governed parent subject assignment has no candidate"
                )
            candidate = self._repository.get_candidate(connection, parent.candidate_id)
            if (
                candidate is None
                or candidate.kind is not parent.kind
                or candidate.subject_key != parent.subject_key
                or candidate.base != parent.champion_ref
            ):
                raise EvolutionAssignmentConflict(
                    "parent subject assignment candidate attribution conflicts"
                )
            selected_ref = self._continuity_selected_ref(
                connection,
                candidate=candidate,
                parent_selected_ref=parent.selected_ref,
            )
            assignment = parent.model_copy(
                update={
                    "assignment_id": self._subject_assignment_id(
                        memorial_id,
                        parent.kind,
                        parent.subject_key,
                    ),
                    "memorial_id": memorial_id,
                    "selected_ref": selected_ref,
                    "created_at": created_at,
                }
            )
            overlay = EffectiveEvolutionOverlayV1(
                assignment_id=assignment.assignment_id,
                kind=assignment.kind,
                subject_key=assignment.subject_key,
                artifact_digest=selected_ref.artifact_digest,
                canonical_digest=selected_ref.canonical_digest,
            )
            self._resolve_payload(connection, selected_ref, overlay)
            records.append((assignment, overlay))
            if selected_ref != parent.selected_ref:
                converged.append(assignment)
        return tuple(records), tuple(converged)

    @staticmethod
    def _assignment_set(
        memorial_id: str,
        records: tuple[SubjectAssignmentRecord, ...],
    ) -> RunAssignmentSetV1:
        assignments = tuple(record[0] for record in records)
        material = {
            "memorial_id": memorial_id,
            "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
        }
        return RunAssignmentSetV1(
            memorial_id=memorial_id,
            assignments=assignments,
            set_hash=canonical_sha256(material),
        )

    def _insert_assignment_bundle(
        self,
        connection: sqlite3.Connection,
        *,
        assignment: Assignment,
        overlay: EffectiveEvolutionOverlayV1 | None,
        assignment_set: RunAssignmentSetV1,
        records: tuple[SubjectAssignmentRecord, ...],
    ) -> Assignment:
        """Atomically dual-write the legacy projection and sealed subject set."""

        connection.execute("SAVEPOINT evolution_assignment_bundle_insert")
        try:
            if isinstance(assignment, LegacyRunAssignmentV1):
                durable: Assignment = self._repository.insert_legacy_assignment(
                    connection,
                    assignment,
                )
            else:
                if overlay is None:
                    raise EvolutionAssignmentConflict(
                        "governed assignment requires an effective overlay"
                    )
                durable = self._repository.insert_assignment(connection, assignment, overlay)
            self._repository.insert_assignment_set(
                connection,
                assignment_set,
                tuple(record[1] for record in records),
            )
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT evolution_assignment_bundle_insert")
            connection.execute("RELEASE SAVEPOINT evolution_assignment_bundle_insert")
            raise
        connection.execute("RELEASE SAVEPOINT evolution_assignment_bundle_insert")
        return durable

    def _continuity_selected_ref(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
        parent_selected_ref: CandidateVersionRefV1,
    ) -> CandidateVersionRefV1:
        if candidate.lifecycle is CandidateLifecycle.CANARY:
            if candidate.kind is CandidateKind.EXECUTOR:
                if parent_selected_ref == candidate.candidate:
                    try:
                        self._require_authorized_executor_authority(
                            connection,
                            candidate=candidate,
                        )
                    except _ExecutorAuthorityUnavailable as exc:
                        raise EvolutionAssignmentConflict(
                            "executor challenger continuity authority is unavailable"
                        ) from exc
                elif parent_selected_ref != candidate.base:
                    raise EvolutionAssignmentConflict(
                        "executor challenger continuity attribution conflicts"
                    )
            return parent_selected_ref
        if candidate.lifecycle is CandidateLifecycle.PROMOTED:
            return candidate.candidate
        if candidate.lifecycle is CandidateLifecycle.ARCHIVED:
            try:
                transition = EvolutionRepository().get_verified_lifecycle_transition_to(
                    connection,
                    candidate_id=candidate.candidate_id,
                    to_lifecycle=CandidateLifecycle.ARCHIVED,
                )
            except EvolutionRepositoryDecodeError as exc:
                raise EvolutionAssignmentConflict(
                    "archived candidate continuity provenance conflicts"
                ) from exc
            if (
                transition is None
                or transition[0] > candidate.version
                or transition[2] is not CandidateLifecycle.ARCHIVED
            ):
                raise EvolutionAssignmentConflict(
                    "archived candidate continuity provenance is unavailable"
                )
            if transition[1] is CandidateLifecycle.PROMOTED:
                return candidate.candidate
        return candidate.base

    @staticmethod
    def _record_continuity_convergence(
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        assignments: tuple[SubjectRunAssignmentV1, ...],
    ) -> None:
        for assignment in assignments:
            if assignment.candidate_id is None:
                continue
            correlation_id = (
                "continuity-"
                + hashlib.sha256(
                    f"{memorial_id}\0{assignment.kind.value}\0{assignment.subject_key}".encode()
                ).hexdigest()
            )
            connection.execute("SAVEPOINT evolution_continuity_audit")
            try:
                _append_system_audit_unlocked(
                    connection,
                    AppendSystemAuditRequest(
                        correlation_id=correlation_id,
                        actor_digest=hashlib.sha256(b"system:evolution-router").hexdigest(),
                        action="evolution_continuity_converged",
                        outcome="succeeded",
                        reason_code="evolution_continuity_converged",
                        subject_kind="evolution_assignment",
                        subject_digest=hashlib.sha256(
                            f"{assignment.kind.value}\0{assignment.subject_key}".encode()
                        ).hexdigest(),
                        metadata={
                            "candidate_kind": assignment.kind.value,
                            "routing_version": assignment.routing_version,
                        },
                    ),
                )
                OutboxRepository().add(
                    connection,
                    make_event(
                        event_type="evolution_continuity_converged",
                        memorial_id=memorial_id,
                        producer="evolution_router",
                        payload={
                            "candidate_id": assignment.candidate_id,
                            "kind": assignment.kind.value,
                            "subject_key": assignment.subject_key,
                            "routing_version": assignment.routing_version,
                            "correlation_id": correlation_id,
                        },
                    ),
                )
                connection.execute("RELEASE SAVEPOINT evolution_continuity_audit")
            except Exception:  # noqa: BLE001 - audit must not block continuity recovery
                connection.execute("ROLLBACK TO SAVEPOINT evolution_continuity_audit")
                connection.execute("RELEASE SAVEPOINT evolution_continuity_audit")
                logger.warning(
                    "Evolution continuity convergence could not be audited",
                    exc_info=True,
                )

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
            loaded, assignment_set = self._load_assignment_state(connection, memorial_id)
        except (EvolutionRepositoryDecodeError, EvolutionAssignmentConflict) as exc:
            raise RunAssignmentUnavailable("run_assignment_unavailable") from exc
        if loaded is None:
            raise LookupError("run assignment not found")
        assignment, overlay = loaded
        records = self._records_for_assignment_set(assignment_set)
        if records:
            for subject_assignment, subject_overlay in records:
                self._resolve_payload(
                    connection,
                    subject_assignment.selected_ref,
                    subject_overlay,
                )
        elif isinstance(assignment, RunAssignmentV1):
            assert overlay is not None
            self._resolve_payload(connection, assignment.selected_ref, overlay)

        controller = self._get_generation_controller()
        try:
            return self._bind_system_snapshot(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                assignment=assignment,
                overlay=overlay,
                assignment_set=assignment_set,
                subject_overlays=self._snapshot_overlay_map(
                    assignment,
                    overlay,
                    records,
                ),
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
        singular_assignment: RunAssignmentV1 | None = None
        singular_overlay: EffectiveEvolutionOverlayV1 | None = None
        singular_payload: dict[str, JsonValue] | None = None
        subject_assignments: tuple[SubjectRunAssignmentV1, ...] = ()
        overlays: dict[str, EffectiveEvolutionOverlayV1] = {}
        payloads: dict[str, dict[str, JsonValue]] = {}
        with self._storage.unit_of_work() as unit_of_work:
            try:
                loaded, assignment_set = self._load_assignment_state(
                    unit_of_work.connection,
                    memorial_id,
                )
            except (EvolutionRepositoryDecodeError, EvolutionAssignmentConflict) as exc:
                raise RunAssignmentUnavailable("run_assignment_unavailable") from exc
            if loaded is None:
                raise LookupError("run assignment not found")
            assignment, overlay = loaded
            records = self._records_for_assignment_set(assignment_set)
            if records:
                for subject_assignment, subject_overlay in records:
                    key = runtime_subject_key(
                        subject_assignment.kind,
                        subject_assignment.subject_key,
                    )
                    if key in overlays:
                        raise RunAssignmentUnavailable("run_assignment_unavailable")
                    overlays[key] = subject_overlay
                    payloads[key] = self._resolve_payload(
                        unit_of_work.connection,
                        subject_assignment.selected_ref,
                        subject_overlay,
                    )
                subject_assignments = tuple(record[0] for record in records)
                if len(records) == 1:
                    if not isinstance(assignment, RunAssignmentV1) or overlay is None:
                        raise RunAssignmentUnavailable("run_assignment_unavailable")
                    key = next(iter(overlays))
                    singular_assignment = assignment
                    singular_overlay = overlay
                    singular_payload = payloads[key]
                run_binding = self._bind_system_snapshot(
                    unit_of_work.connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    assignment=assignment,
                    overlay=overlay,
                    assignment_set=assignment_set,
                    subject_overlays=overlays,
                )
                unit_of_work.commit()
            elif isinstance(assignment, LegacyRunAssignmentV1):
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
                if overlay.kind is None or overlay.subject_key is None:
                    raise RunAssignmentUnavailable("run_assignment_unavailable")
                singular_assignment = assignment
                singular_overlay = overlay
                singular_payload = payload
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
        context = EvolutionRuntimeContext(
            assignment=singular_assignment,
            overlay=singular_overlay,
            selected_payload=singular_payload,
            assignments=subject_assignments,
            overlays=overlays,
            payloads=payloads,
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
        assignment_set: RunAssignmentSetV1 | None = None,
        subject_overlays: Mapping[str, EffectiveEvolutionOverlayV1] | None = None,
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
        try:
            executor_authority = self._executor_authority_for_binding(
                connection,
                assignment=assignment,
                overlay=overlay,
                assignment_set=assignment_set,
            )
        except _ExecutorAuthorityUnavailable as exc:
            if existing is not None or exact_generation_binding is not None:
                raise GenerationRetired("generation_retired") from exc
            raise GenerationBindingUnavailable("generation_binding_unavailable") from exc
        if existing is not None:
            if (
                exact_generation_binding is not None
                and exact_generation_binding.generation_ids != existing.generation_ids
            ):
                raise GenerationRetired("generation_retired")
            if executor_authority is not None and existing.generation_ids != (
                executor_authority.generation_id,
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
                    allow_ready=executor_authority is not None,
                )
                if executor_authority is not None:
                    self._validate_executor_authority_selection(
                        exact_selection,
                        executor_authority,
                        inherited=True,
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
        inherited_generation = bool(pinned_ids)
        inherit_pinned = exact_generation_ids is None and inherited_generation
        allow_ready = False
        if executor_authority is not None:
            expected_generation_ids = (executor_authority.generation_id,)
            if pinned_ids and pinned_ids != expected_generation_ids:
                raise GenerationRetired("generation_retired")
            if exact_generation_ids == ():
                raise GenerationRetired("generation_retired")
            pinned_ids = expected_generation_ids
            inherit_pinned = False
            allow_ready = True
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
                inherited=inherited_generation,
                inherit_pinned=inherit_pinned,
                allow_ready=allow_ready,
            )
        elif pinned_ids:
            raise GenerationRetired("generation_retired")
        if selection is not None and executor_authority is not None:
            self._validate_executor_authority_selection(
                selection,
                executor_authority,
                inherited=inherited_generation,
            )

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
                assignment_set=assignment_set,
                subject_overlays=subject_overlays,
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

    def _executor_authority_for_binding(
        self,
        connection: sqlite3.Connection,
        *,
        assignment: Assignment,
        overlay: EffectiveEvolutionOverlayV1 | None,
        assignment_set: RunAssignmentSetV1 | None,
    ) -> ExecutorGenerationAuthorityV1 | None:
        selected = self._executor_subject_selection(
            assignment=assignment,
            overlay=overlay,
            assignment_set=assignment_set,
        )
        if selected is None:
            return None
        try:
            candidate = self._repository.get_candidate(connection, selected.candidate_id)
        except Exception as exc:
            raise _ExecutorAuthorityUnavailable from exc
        if (
            candidate is None
            or candidate.kind is not CandidateKind.EXECUTOR
            or candidate.subject_key != selected.subject_key
            or candidate.base != selected.champion_ref
        ):
            raise _ExecutorAuthorityUnavailable
        if selected.selected_ref == candidate.base:
            return None
        if selected.selected_ref != candidate.candidate:
            raise _ExecutorAuthorityUnavailable
        return self._require_authorized_executor_authority(
            connection,
            candidate=candidate,
        )

    @staticmethod
    def _executor_subject_selection(
        *,
        assignment: Assignment,
        overlay: EffectiveEvolutionOverlayV1 | None,
        assignment_set: RunAssignmentSetV1 | None,
    ) -> _ExecutorSubjectSelection | None:
        if assignment_set is not None:
            executor_assignments = tuple(
                item for item in assignment_set.assignments if item.kind is CandidateKind.EXECUTOR
            )
            if not executor_assignments:
                return None
            if len(executor_assignments) != 1:
                raise _ExecutorAuthorityUnavailable
            item = executor_assignments[0]
            if item.candidate_id is None:
                raise _ExecutorAuthorityUnavailable
            return _ExecutorSubjectSelection(
                candidate_id=item.candidate_id,
                subject_key=item.subject_key,
                champion_ref=item.champion_ref,
                selected_ref=item.selected_ref,
            )
        if (
            isinstance(assignment, RunAssignmentV1)
            and overlay is not None
            and overlay.kind is CandidateKind.EXECUTOR
            and overlay.subject_key is not None
        ):
            return _ExecutorSubjectSelection(
                candidate_id=assignment.candidate_id,
                subject_key=overlay.subject_key,
                champion_ref=assignment.champion_ref,
                selected_ref=assignment.selected_ref,
            )
        return None

    def _require_authorized_executor_authority(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
    ) -> ExecutorGenerationAuthorityV1:
        try:
            resolver = (
                self._executor_generation_authority_resolver()
                if self._executor_generation_authority_resolver is not None
                else None
            )
            if resolver is None:
                raise _ExecutorAuthorityUnavailable
            authority = resolver.get_current(
                connection,
                candidate_id=candidate.candidate_id,
            )
            if authority is None:
                raise _ExecutorAuthorityUnavailable
            generation_authority = resolver.get_by_generation(
                connection,
                generation_id=authority.generation_id,
            )
        except _ExecutorAuthorityUnavailable:
            raise
        except Exception as exc:
            raise _ExecutorAuthorityUnavailable from exc
        if (
            authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED
            or generation_authority != authority
            or authority.candidate_id != candidate.candidate_id
            or authority.candidate_version > candidate.version
            or authority.candidate_artifact_digest != candidate.candidate.artifact_digest
            or authority.candidate_canonical_digest != candidate.candidate.canonical_digest
            or authority.scope != candidate.subject_key
            or candidate.lifecycle
            not in {
                CandidateLifecycle.READY,
                CandidateLifecycle.CANARY,
                CandidateLifecycle.PROMOTED,
                CandidateLifecycle.ARCHIVED,
            }
        ):
            raise _ExecutorAuthorityUnavailable
        return authority

    @staticmethod
    def _validate_executor_authority_selection(
        selection: _GenerationSelection,
        authority: ExecutorGenerationAuthorityV1,
        *,
        inherited: bool,
    ) -> None:
        bundle = selection.bundles.get(authority.scope)
        if (
            selection.generation_ids != (authority.generation_id,)
            or selection.by_scope.get(authority.scope) != authority.generation_id
            or bundle is None
            or bundle.release_digest != authority.release_digest
        ):
            if inherited:
                raise GenerationRetired("generation_retired")
            raise GenerationBindingUnavailable("generation_binding_unavailable")

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
        allow_ready: bool = False,
    ) -> _GenerationSelection:
        try:
            if allow_ready:
                selection = controller.resolve_for_binding_current(
                    connection,
                    memorial_id,
                    attempt_id,
                    pinned_ids=pinned_ids,
                    inherit_pinned=inherit_pinned,
                    allow_ready=True,
                )
            else:
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
            if inherited:
                raise GenerationRetired("generation_retired")
            raise GenerationBindingUnavailable("generation_binding_unavailable")
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

    @staticmethod
    def _subject_assignment_id(
        memorial_id: str,
        kind: CandidateKind,
        subject_key: str,
    ) -> str:
        identity = f"{memorial_id}\0{kind.value}\0{subject_key}".encode()
        return "assignment:" + hashlib.sha256(identity).hexdigest()

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
