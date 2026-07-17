"""Persist-once challenger routing and verified per-run overlay binding."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Protocol, cast

from tianshu.evolution.runtime_context import (
    EvolutionRuntimeContext,
    bind_evolution_runtime,
)
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import CandidateVersionRefV1
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    EvolutionRunEvidenceV1,
    RunAssignmentV1,
)
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_DEFAULT_ALLOCATION_SECRET = b"tianshu.lean-preview.routing.v1"


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


BucketCalculator = Callable[[str, str, bytes], int]
ArtifactReader = Callable[[sqlite3.Connection, str], bytes]
BeforeInsert = Callable[[RunAssignmentV1], None]


def allocation_bucket(memorial_id: str, allocation_seed_id: str, secret: bytes) -> int:
    """Return the frozen HMAC-SHA256 bucket in the inclusive range 0..9999."""

    if not memorial_id.strip() or not allocation_seed_id.strip():
        raise ValueError("routing identities must be non-blank")
    if not secret:
        raise ValueError("allocation secret must not be empty")
    identity = f"{allocation_seed_id}:{memorial_id}".encode()
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
        allocation_secret: bytes = _DEFAULT_ALLOCATION_SECRET,
        bucket_calculator: BucketCalculator = allocation_bucket,
        artifact_reader: ArtifactReader | None = None,
        before_insert: BeforeInsert | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not allocation_secret:
            raise ValueError("allocation secret must not be empty")
        self._storage = storage
        self._allocation_secret = allocation_secret
        self._bucket_calculator = bucket_calculator
        self._artifact_reader = artifact_reader
        self._before_insert = before_insert
        self._clock = clock or (lambda: datetime.now(UTC))
        self._repository = EvolutionRepository()

    def assign(self, memorial_id: str) -> RunAssignmentV1:
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
    ) -> RunAssignmentV1:
        if not memorial_id.strip():
            raise ValueError("memorial_id must be non-blank")
        connection = unit_of_work.connection
        existing = self._repository.get_assignment(connection, memorial_id)
        if existing is not None:
            return existing[0]
        candidate = self._repository.get_routable_candidate(connection)
        if candidate is None:
            seed_id = "no-canary"
            routing_version = 1
            bucket = self._bucket(memorial_id, seed_id)
            live_digest = canonical_sha256({"kind": "live", "version": 1})
            champion_ref = CandidateVersionRefV1(
                version="live",
                artifact_digest=live_digest,
                canonical_digest=live_digest,
            )
            selected_ref = champion_ref
            candidate_id = None
            kind = None
            subject_key = None
        else:
            routing = candidate.routing
            assert routing is not None
            bucket = self._bucket(memorial_id, routing.allocation_seed_id)
            selected_challenger = selects_challenger(
                bucket=bucket,
                allocation_basis_points=routing.allocation_basis_points,
            )
            routing_version = routing.routing_version
            champion_ref = candidate.base
            selected_ref = candidate.candidate if selected_challenger else champion_ref
            candidate_id = candidate.candidate_id
            kind = candidate.kind if selected_challenger else None
            subject_key = candidate.subject_key if selected_challenger else None
            self._verified_payload(connection, selected_ref)
        assigned_at = (created_at or self._clock()).astimezone(UTC)
        assignment_id = "assignment:" + hashlib.sha256(memorial_id.encode()).hexdigest()
        assignment = RunAssignmentV1(
            assignment_id=assignment_id,
            memorial_id=memorial_id,
            candidate_id=candidate_id,
            champion_ref=champion_ref,
            selected_ref=selected_ref,
            routing_version=routing_version,
            bucket=bucket,
            created_at=assigned_at,
        )
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id=assignment_id,
            kind=kind,
            subject_key=subject_key,
            artifact_digest=selected_ref.artifact_digest,
            canonical_digest=selected_ref.canonical_digest,
        )
        if self._before_insert is not None:
            self._before_insert(assignment)
        return self._repository.insert_assignment(connection, assignment, overlay)

    def get(self, memorial_id: str) -> RunAssignmentV1 | None:
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
        return EvolutionRunEvidenceV1(
            assignment=assignment,
            overlay=overlay,
            candidate_id=assignment.candidate_id,
            routing_version=assignment.routing_version,
        )

    @contextmanager
    def bind_runtime(self, memorial_id: str) -> Iterator[EvolutionRuntimeContext]:
        with self._storage.unit_of_work() as unit_of_work:
            loaded = self._repository.get_assignment(
                unit_of_work.connection,
                memorial_id,
            )
            if loaded is None:
                raise LookupError("run assignment not found")
            assignment, overlay = loaded
            verified = (
                self._verified_payload(unit_of_work.connection, assignment.selected_ref)
                if assignment.candidate_id is not None
                else None
            )
            payload = verified if assignment.selected_ref != assignment.champion_ref else None
            unit_of_work.commit()
        context = EvolutionRuntimeContext(
            assignment=assignment,
            overlay=overlay,
            candidate_payload=payload,
        )
        with bind_evolution_runtime(context):
            yield context

    def _load(self, memorial_id: str) -> tuple[RunAssignmentV1, EffectiveEvolutionOverlayV1] | None:
        with self._storage.unit_of_work() as unit_of_work:
            loaded = self._repository.get_assignment(unit_of_work.connection, memorial_id)
            unit_of_work.commit()
            return loaded

    def _bucket(self, memorial_id: str, seed_id: str) -> int:
        bucket = self._bucket_calculator(memorial_id, seed_id, self._allocation_secret)
        if type(bucket) is not int or not 0 <= bucket < 10_000:
            raise ValueError("bucket calculator returned an out-of-range value")
        return bucket

    def _verified_payload(
        self,
        connection: sqlite3.Connection,
        selected_ref: CandidateVersionRefV1,
    ) -> dict[str, JsonValue]:
        if self._artifact_reader is None:
            raise ValueError("candidate_overlay_unavailable")
        raw = self._artifact_reader(connection, selected_ref.artifact_digest)
        if hashlib.sha256(raw).hexdigest() != selected_ref.artifact_digest:
            raise ValueError("candidate_overlay_unavailable")
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("candidate_overlay_unavailable") from exc
        if (
            not isinstance(payload, dict)
            or canonical_json_bytes(payload) != raw
            or canonical_sha256(payload) != selected_ref.canonical_digest
        ):
            raise ValueError("candidate_overlay_unavailable")
        return cast(dict[str, JsonValue], payload)


__all__ = ["ChallengerRouter", "allocation_bucket", "selects_challenger"]
