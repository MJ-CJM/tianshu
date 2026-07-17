"""Connection-level persistence for governed evolution candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.decision import DecisionKind, DecisionStatus
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    EvolutionCandidateV1,
    validate_lifecycle_transition,
)
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
)
from tianshu.storage.decision_repo import DecisionDecodeError, DecisionRepository

_LEGACY_ASSIGNMENT_MARKER = {"mode": "legacy_unmanaged"}
_LEGACY_ASSIGNMENT_MARKER_JSON = canonical_json_bytes(_LEGACY_ASSIGNMENT_MARKER).decode("utf-8")
_LEGACY_ASSIGNMENT_MARKER_DIGEST = canonical_sha256(_LEGACY_ASSIGNMENT_MARKER)


class EvolutionRepositoryError(RuntimeError):
    """Base error for evolution candidate persistence."""


class EvolutionRepositoryConflict(EvolutionRepositoryError):
    """The candidate identity, state, or expected version conflicts."""


class EvolutionRepositoryDecodeError(EvolutionRepositoryError):
    """A durable candidate row violates the v1 contract."""


class EvolutionAssignmentConflict(EvolutionRepositoryConflict):
    """A Memorial already has a different immutable assignment."""


class _CodePromotionDecisionBindingV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    candidate_id: str
    candidate_version: int = Field(ge=1)
    candidate_artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_snapshot_version: int = Field(ge=0)
    action: Literal["promote"]
    risk_tier: Literal["high"]


_SELECT_CANDIDATE = """
SELECT
    candidate_id, schema_version, kind, subject_key, provenance_json, provenance_hash,
    base_json, candidate_ref_json, diff_artifact_digest,
    evolution_contract_json, evolution_contract_hash, gate_snapshot_version,
    evidence_bundle_ids_json, routing_json, rollback_json, lifecycle,
    version, created_at, updated_at
FROM evolution_candidates
"""


def _json_value(raw: object, *, field: str, expected: type[object]) -> object:
    if not isinstance(raw, str):
        raise EvolutionRepositoryDecodeError(f"{field} is not text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvolutionRepositoryDecodeError(f"{field} is not valid JSON") from exc
    if not isinstance(value, expected):
        raise EvolutionRepositoryDecodeError(f"{field} has the wrong JSON shape")
    canonical = (
        canonical_json_bytes(value).decode("utf-8")
        if isinstance(value, dict)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    )
    if raw != canonical:
        raise EvolutionRepositoryDecodeError(f"{field} is not canonical JSON")
    return value


def _decode_candidate(row: sqlite3.Row) -> EvolutionCandidateV1:
    provenance = cast(
        dict[str, object],
        _json_value(row["provenance_json"], field="provenance_json", expected=dict),
    )
    if row["provenance_hash"] != canonical_sha256(provenance):
        raise EvolutionRepositoryDecodeError("persisted candidate provenance hash does not match")
    payload = {
        "candidate_id": row["candidate_id"],
        "schema_version": row["schema_version"],
        "kind": row["kind"],
        "subject_key": row["subject_key"],
        "provenance": provenance,
        "base": _json_value(row["base_json"], field="base_json", expected=dict),
        "candidate": _json_value(
            row["candidate_ref_json"], field="candidate_ref_json", expected=dict
        ),
        "diff_artifact_digest": row["diff_artifact_digest"],
        "evolution_contract": _json_value(
            row["evolution_contract_json"], field="evolution_contract_json", expected=dict
        ),
        "evolution_contract_hash": row["evolution_contract_hash"],
        "gate_snapshot_version": row["gate_snapshot_version"],
        "evidence_bundle_ids": _json_value(
            row["evidence_bundle_ids_json"], field="evidence_bundle_ids_json", expected=list
        ),
        "routing": (
            _json_value(row["routing_json"], field="routing_json", expected=dict)
            if row["routing_json"] is not None
            else None
        ),
        "rollback": _json_value(row["rollback_json"], field="rollback_json", expected=dict),
        "lifecycle": row["lifecycle"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    try:
        return EvolutionCandidateV1.model_validate_json(json.dumps(payload))
    except (ValidationError, TypeError, ValueError) as exc:
        raise EvolutionRepositoryDecodeError(
            "persisted evolution candidate violates the v1 contract"
        ) from exc


def _json_text(value: object) -> str:
    if not isinstance(value, tuple) or any(not isinstance(item, str) for item in value):
        raise TypeError("canonical string tuple required")
    return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))


def _journal_id(candidate_id: str, version: int, lifecycle: CandidateLifecycle) -> str:
    identity = f"{candidate_id}:{version}:{lifecycle.value}".encode()
    return hashlib.sha256(identity).hexdigest()


def _append_lifecycle_journal(
    connection: sqlite3.Connection,
    *,
    candidate: EvolutionCandidateV1,
    previous: CandidateLifecycle | None,
    decision_request_id: str | None,
) -> None:
    entry = {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
        "from_lifecycle": previous.value if previous is not None else None,
        "to_lifecycle": candidate.lifecycle.value,
        "decision_request_id": decision_request_id,
        "created_at": candidate.updated_at.isoformat(),
    }
    connection.execute(
        """
        INSERT INTO evolution_lifecycle_journal (
            journal_id, candidate_id, candidate_version, from_lifecycle,
            to_lifecycle, decision_request_id, entry_json, entry_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _journal_id(candidate.candidate_id, candidate.version, candidate.lifecycle),
            candidate.candidate_id,
            candidate.version,
            previous.value if previous is not None else None,
            candidate.lifecycle.value,
            decision_request_id,
            canonical_json_bytes(entry).decode("utf-8"),
            canonical_sha256(entry),
            candidate.updated_at.isoformat(),
        ),
    )


def _require_high_risk_code_promotion_decision(
    connection: sqlite3.Connection,
    *,
    candidate: EvolutionCandidateV1,
    decision_request_id: str | None,
) -> None:
    if decision_request_id is None or not decision_request_id.strip():
        raise EvolutionRepositoryConflict(
            "code promotion requires an explicit resolved high-risk Decision"
        )
    try:
        record = DecisionRepository().get(connection, decision_request_id)
        if record is None or record.resolution is None:
            raise ValueError("decision is missing or unresolved")
        binding = _CodePromotionDecisionBindingV1.model_validate(record.request.payload)
        if (
            record.request.kind is not DecisionKind.GOVERNED_APPLY
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution.action != "approve"
            or binding.candidate_id != candidate.candidate_id
            or binding.candidate_version != candidate.version
            or binding.candidate_artifact_digest != candidate.candidate.artifact_digest
            or binding.gate_snapshot_version != candidate.gate_snapshot_version
        ):
            raise ValueError("decision is not bound to the current candidate")
    except (DecisionDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise EvolutionRepositoryConflict(
            "code promotion requires an explicit resolved high-risk Decision"
        ) from exc


def _require_immutable_core(current: EvolutionCandidateV1, candidate: EvolutionCandidateV1) -> None:
    if current.provenance != candidate.provenance:
        raise EvolutionRepositoryConflict("candidate provenance is immutable")
    immutable_fields = (
        "schema_version",
        "candidate_id",
        "kind",
        "subject_key",
        "base",
        "candidate",
        "diff_artifact_digest",
        "evolution_contract",
        "evolution_contract_hash",
        "rollback",
        "created_at",
    )
    if any(getattr(current, field) != getattr(candidate, field) for field in immutable_fields):
        raise EvolutionRepositoryConflict("candidate canonical identity is immutable")
    if candidate.evidence_bundle_ids[: len(current.evidence_bundle_ids)] != (
        current.evidence_bundle_ids
    ):
        raise EvolutionRepositoryConflict("candidate evidence provenance is immutable")
    if candidate.gate_snapshot_version < current.gate_snapshot_version:
        raise EvolutionRepositoryConflict("candidate gate snapshot version cannot move backwards")
    if candidate.updated_at < current.updated_at:
        raise EvolutionRepositoryConflict("candidate updated_at cannot move backwards")


class EvolutionRepository:
    """Stateless primitives whose caller owns the SQLite transaction."""

    def require_code_promotion_decision(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
        decision_request_id: str | None,
    ) -> None:
        """Preflight an exact, resolved high-risk code promotion Decision."""

        if candidate.kind is not CandidateKind.CODE:
            raise ValueError("code promotion Decision validation requires a code candidate")
        _require_high_risk_code_promotion_decision(
            connection,
            candidate=candidate,
            decision_request_id=decision_request_id,
        )

    def get_candidate(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> EvolutionCandidateV1 | None:
        row = connection.execute(
            _SELECT_CANDIDATE + " WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return _decode_candidate(row) if row is not None else None

    def get_routable_candidate(self, connection: sqlite3.Connection) -> EvolutionCandidateV1 | None:
        """Return the single canary routing authority or fail closed if ambiguous."""

        rows = connection.execute(
            _SELECT_CANDIDATE + " WHERE lifecycle = 'canary' ORDER BY created_at, candidate_id"
        ).fetchall()
        candidates = tuple(_decode_candidate(row) for row in rows)
        if len(candidates) > 1:
            raise EvolutionRepositoryConflict("multiple canary routing authorities")
        if not candidates:
            return None
        candidate = candidates[0]
        routing = candidate.routing
        row = connection.execute(
            "SELECT * FROM evolution_routing_allocations WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()
        if routing is None or row is None:
            raise EvolutionRepositoryConflict("canary routing authority is incomplete")
        payload = routing.model_dump(mode="json")
        if (
            row["routing_version"] != routing.routing_version
            or row["allocation_basis_points"] != routing.allocation_basis_points
            or row["allocation_seed_id"] != routing.allocation_seed_id
            or row["routing_json"] != canonical_json_bytes(payload).decode("utf-8")
            or row["routing_hash"] != canonical_sha256(payload)
        ):
            raise EvolutionRepositoryConflict("canary routing authority conflicts")
        return candidate

    def get_assignment(
        self, connection: sqlite3.Connection, memorial_id: str
    ) -> (
        tuple[
            RunAssignmentV1 | LegacyRunAssignmentV1,
            EffectiveEvolutionOverlayV1 | None,
        ]
        | None
    ):
        row = connection.execute(
            "SELECT * FROM run_evolution_assignments WHERE memorial_id=?",
            (memorial_id,),
        ).fetchone()
        if row is None:
            return None
        raw = row["assignment_json"]
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EvolutionRepositoryDecodeError(
                "persisted run assignment is not valid JSON"
            ) from exc
        if decoded is None or canonical_sha256(decoded) != row["assignment_hash"]:
            raise EvolutionRepositoryDecodeError("persisted assignment hash does not match")
        assignment_type = (
            LegacyRunAssignmentV1
            if isinstance(decoded, dict) and decoded.get("mode") == "legacy_unmanaged"
            else RunAssignmentV1
        )
        try:
            assignment = assignment_type.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise EvolutionRepositoryDecodeError(
                "persisted run assignment violates the v1 contract"
            ) from exc
        if canonical_json_bytes(assignment).decode("utf-8") != raw:
            raise EvolutionRepositoryDecodeError("assignment_json is not canonical JSON")
        if isinstance(assignment, LegacyRunAssignmentV1):
            if (
                row["assignment_id"] != assignment.assignment_id
                or row["memorial_id"] != assignment.memorial_id
                or row["candidate_id"] is not None
                or row["routing_version"] != 1
                or row["bucket"] != 0
                or row["champion_ref_json"] != _LEGACY_ASSIGNMENT_MARKER_JSON
                or row["selected_ref_json"] != _LEGACY_ASSIGNMENT_MARKER_JSON
                or row["overlay_digest"] != _LEGACY_ASSIGNMENT_MARKER_DIGEST
                or row["created_at"] != assignment.created_at.isoformat()
            ):
                raise EvolutionRepositoryDecodeError(
                    "legacy assignment columns conflict with assignment_json"
                )
            return assignment, None
        if (
            row["assignment_id"] != assignment.assignment_id
            or row["memorial_id"] != assignment.memorial_id
            or row["candidate_id"] != assignment.candidate_id
            or row["routing_version"] != assignment.routing_version
            or row["bucket"] != assignment.bucket
            or row["champion_ref_json"]
            != canonical_json_bytes(assignment.champion_ref).decode("utf-8")
            or row["selected_ref_json"]
            != canonical_json_bytes(assignment.selected_ref).decode("utf-8")
            or row["created_at"] != assignment.created_at.isoformat()
        ):
            raise EvolutionRepositoryDecodeError("assignment columns conflict with assignment_json")
        candidate = self.get_candidate(connection, assignment.candidate_id)
        if (
            candidate is None
            or candidate.base != assignment.champion_ref
            or assignment.selected_ref not in {candidate.base, candidate.candidate}
        ):
            raise EvolutionRepositoryDecodeError("assignment candidate attribution conflicts")
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id=assignment.assignment_id,
            kind=candidate.kind,
            subject_key=candidate.subject_key,
            artifact_digest=assignment.selected_ref.artifact_digest,
            canonical_digest=assignment.selected_ref.canonical_digest,
        )
        if row["overlay_digest"] != canonical_sha256(overlay):
            raise EvolutionRepositoryDecodeError("assignment overlay digest conflicts")
        return assignment, overlay

    def insert_legacy_assignment(
        self,
        connection: sqlite3.Connection,
        assignment: LegacyRunAssignmentV1,
    ) -> LegacyRunAssignmentV1:
        existing = self.get_assignment(connection, assignment.memorial_id)
        if existing is not None:
            if existing != (assignment, None):
                raise EvolutionAssignmentConflict("Memorial assignment is immutable")
            assert isinstance(existing[0], LegacyRunAssignmentV1)
            return existing[0]
        raw = canonical_json_bytes(assignment).decode("utf-8")
        try:
            connection.execute(
                """INSERT INTO run_evolution_assignments (
                       assignment_id, memorial_id, candidate_id, routing_version, bucket,
                       champion_ref_json, selected_ref_json, overlay_digest,
                       assignment_json, assignment_hash, created_at
                   ) VALUES (?, ?, NULL, 1, 0, ?, ?, ?, ?, ?, ?)""",
                (
                    assignment.assignment_id,
                    assignment.memorial_id,
                    _LEGACY_ASSIGNMENT_MARKER_JSON,
                    _LEGACY_ASSIGNMENT_MARKER_JSON,
                    _LEGACY_ASSIGNMENT_MARKER_DIGEST,
                    raw,
                    canonical_sha256(assignment),
                    assignment.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.get_assignment(connection, assignment.memorial_id)
            if replay == (assignment, None):
                assert isinstance(replay[0], LegacyRunAssignmentV1)
                return replay[0]
            raise EvolutionAssignmentConflict("Memorial assignment identity conflict") from exc
        durable = self.get_assignment(connection, assignment.memorial_id)
        if durable is None or not isinstance(durable[0], LegacyRunAssignmentV1):
            raise EvolutionAssignmentConflict("Memorial assignment disappeared")
        return durable[0]

    def insert_assignment(
        self,
        connection: sqlite3.Connection,
        assignment: RunAssignmentV1,
        overlay: EffectiveEvolutionOverlayV1,
    ) -> RunAssignmentV1:
        if (
            overlay.assignment_id != assignment.assignment_id
            or overlay.artifact_digest != assignment.selected_ref.artifact_digest
            or overlay.canonical_digest != assignment.selected_ref.canonical_digest
        ):
            raise ValueError("effective overlay does not match assignment")
        existing = self.get_assignment(connection, assignment.memorial_id)
        if existing is not None:
            if existing != (assignment, overlay):
                raise EvolutionAssignmentConflict("Memorial assignment is immutable")
            return existing[0]
        raw = canonical_json_bytes(assignment).decode("utf-8")
        try:
            connection.execute(
                """INSERT INTO run_evolution_assignments (
                       assignment_id, memorial_id, candidate_id, routing_version, bucket,
                       champion_ref_json, selected_ref_json, overlay_digest,
                       assignment_json, assignment_hash, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    assignment.assignment_id,
                    assignment.memorial_id,
                    assignment.candidate_id,
                    assignment.routing_version,
                    assignment.bucket,
                    canonical_json_bytes(assignment.champion_ref).decode("utf-8"),
                    canonical_json_bytes(assignment.selected_ref).decode("utf-8"),
                    canonical_sha256(overlay),
                    raw,
                    canonical_sha256(assignment),
                    assignment.created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.get_assignment(connection, assignment.memorial_id)
            if replay is not None and replay == (assignment, overlay):
                assert isinstance(replay[0], RunAssignmentV1)
                return replay[0]
            raise EvolutionAssignmentConflict("Memorial assignment identity conflict") from exc
        durable = self.get_assignment(connection, assignment.memorial_id)
        if durable is None or not isinstance(
            durable[0], RunAssignmentV1
        ):  # pragma: no cover - successful insert preserves the identity
            raise EvolutionAssignmentConflict("Memorial assignment disappeared")
        return durable[0]

    def insert_candidate(
        self, connection: sqlite3.Connection, candidate: EvolutionCandidateV1
    ) -> EvolutionCandidateV1:
        if candidate.version != 1:
            raise ValueError("new evolution candidate must start at version 1")
        if candidate.lifecycle is not CandidateLifecycle.PROPOSED:
            raise ValueError("new evolution candidate must start as proposed")
        try:
            connection.execute(
                """
                INSERT INTO evolution_candidates (
                    candidate_id, schema_version, kind, subject_key, provenance_json,
                    provenance_hash, base_json, candidate_ref_json, diff_artifact_digest,
                    evolution_contract_json, evolution_contract_hash, gate_snapshot_version,
                    evidence_bundle_ids_json, routing_json, rollback_json, lifecycle,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.schema_version,
                    candidate.kind.value,
                    candidate.subject_key,
                    canonical_json_bytes(candidate.provenance).decode("utf-8"),
                    canonical_sha256(candidate.provenance),
                    canonical_json_bytes(candidate.base).decode("utf-8"),
                    canonical_json_bytes(candidate.candidate).decode("utf-8"),
                    candidate.diff_artifact_digest,
                    canonical_json_bytes(candidate.evolution_contract).decode("utf-8"),
                    candidate.evolution_contract_hash,
                    candidate.gate_snapshot_version,
                    _json_text(candidate.evidence_bundle_ids),
                    (
                        canonical_json_bytes(candidate.routing).decode("utf-8")
                        if candidate.routing is not None
                        else None
                    ),
                    canonical_json_bytes(candidate.rollback).decode("utf-8"),
                    candidate.lifecycle.value,
                    candidate.version,
                    candidate.created_at.isoformat(),
                    candidate.updated_at.isoformat(),
                ),
            )
            _append_lifecycle_journal(
                connection, candidate=candidate, previous=None, decision_request_id=None
            )
        except sqlite3.IntegrityError as exc:
            raise EvolutionRepositoryConflict("evolution candidate identity conflict") from exc
        durable = self.get_candidate(connection, candidate.candidate_id)
        if durable is None:  # pragma: no cover - successful insert preserves the primary key
            raise EvolutionRepositoryConflict("evolution candidate disappeared after insert")
        return durable

    def save_candidate(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        expected_version: int,
        high_risk_decision_request_id: str | None = None,
    ) -> EvolutionCandidateV1:
        current = self.get_candidate(connection, candidate.candidate_id)
        if current is None or current.version != expected_version:
            raise EvolutionRepositoryConflict("evolution candidate compare-and-swap conflict")
        if candidate.version != expected_version:
            raise EvolutionRepositoryConflict("evolution candidate compare-and-swap conflict")
        _require_immutable_core(current, candidate)
        if candidate.lifecycle is not current.lifecycle:
            try:
                validate_lifecycle_transition(current.lifecycle, candidate.lifecycle)
            except ValueError as exc:
                raise EvolutionRepositoryConflict("illegal lifecycle transition") from exc

            if (
                candidate.kind is CandidateKind.CODE
                and candidate.lifecycle is CandidateLifecycle.PROMOTED
            ):
                self.require_code_promotion_decision(
                    connection,
                    candidate=current,
                    decision_request_id=high_risk_decision_request_id,
                )
        saved = candidate.model_copy(update={"version": expected_version + 1})
        cursor = connection.execute(
            """
            UPDATE evolution_candidates
            SET gate_snapshot_version = ?, evidence_bundle_ids_json = ?,
                routing_json = ?, lifecycle = ?, version = ?, updated_at = ?
            WHERE candidate_id = ? AND version = ?
            """,
            (
                saved.gate_snapshot_version,
                _json_text(saved.evidence_bundle_ids),
                (
                    canonical_json_bytes(saved.routing).decode("utf-8")
                    if saved.routing is not None
                    else None
                ),
                saved.lifecycle.value,
                saved.version,
                saved.updated_at.isoformat(),
                saved.candidate_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise EvolutionRepositoryConflict("evolution candidate compare-and-swap conflict")
        if saved.lifecycle is not current.lifecycle:
            try:
                _append_lifecycle_journal(
                    connection,
                    candidate=saved,
                    previous=current.lifecycle,
                    decision_request_id=high_risk_decision_request_id,
                )
            except sqlite3.IntegrityError as exc:
                raise EvolutionRepositoryConflict("candidate lifecycle journal conflict") from exc
        durable = self.get_candidate(connection, saved.candidate_id)
        if durable is None:  # pragma: no cover - successful CAS preserves the primary key
            raise EvolutionRepositoryConflict("evolution candidate disappeared after save")
        return durable


__all__ = [
    "EvolutionAssignmentConflict",
    "EvolutionRepository",
    "EvolutionRepositoryConflict",
    "EvolutionRepositoryDecodeError",
    "EvolutionRepositoryError",
]
