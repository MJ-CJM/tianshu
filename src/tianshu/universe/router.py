"""Persist-once challenger routing and verified per-run overlay binding."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol

from tianshu.evolution.runtime_context import (
    EvolutionRuntimeContext,
    bind_evolution_runtime,
)
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
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


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
        before_insert: BeforeInsert | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._allocation_secret = allocation_secret
        self._bucket_calculator = bucket_calculator
        self._payload_resolver = payload_resolver
        self._before_insert = before_insert
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = EvolutionRepository()

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
    ) -> Assignment:
        if not memorial_id.strip():
            raise ValueError("memorial_id must be non-blank")
        connection = unit_of_work.connection
        existing = self._repository.get_assignment(connection, memorial_id)
        if existing is not None:
            return existing[0]
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

    def get(self, memorial_id: str) -> Assignment | None:
        loaded = self._load(memorial_id)
        return loaded[0] if loaded is not None else None

    def overlay_for(self, memorial_id: str) -> EffectiveEvolutionOverlayV1 | None:
        loaded = self._load(memorial_id)
        return loaded[1] if loaded is not None else None

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
    def bind_runtime(self, memorial_id: str) -> Iterator[EvolutionRuntimeContext | None]:
        legacy = False
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
                unit_of_work.commit()
            else:
                assert overlay is not None
                payload = self._resolve_payload(
                    unit_of_work.connection,
                    assignment.selected_ref,
                    overlay,
                )
                unit_of_work.commit()
        if legacy:
            yield None
            return
        assert isinstance(assignment, RunAssignmentV1)
        assert overlay is not None
        context = EvolutionRuntimeContext(
            assignment=assignment,
            overlay=overlay,
            selected_payload=payload,
        )
        with bind_evolution_runtime(context):
            yield context

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
    "RunAssignmentUnavailable",
    "allocation_bucket",
    "selects_challenger",
]
