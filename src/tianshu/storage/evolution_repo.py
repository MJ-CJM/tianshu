"""Connection-level persistence for governed evolution candidates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.decision import DecisionKind, DecisionStatus
from tianshu.models.evolution_candidate import (
    HIGH_RISK_PROMOTION_KINDS,
    CandidateKind,
    CandidateLifecycle,
    EvolutionCandidateV1,
    validate_lifecycle_transition,
)
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    RunAssignmentV1,
    SubjectRunAssignmentV1,
)
from tianshu.storage.decision_repo import DecisionDecodeError, DecisionRepository
from tianshu.storage.evolution_policy_repo import (
    EvolutionPolicyRepository,
    default_mode_for,
)

_LEGACY_ASSIGNMENT_MARKER = {"mode": "legacy_unmanaged"}
_LEGACY_ASSIGNMENT_MARKER_JSON = canonical_json_bytes(_LEGACY_ASSIGNMENT_MARKER).decode("utf-8")
_LEGACY_ASSIGNMENT_MARKER_DIGEST = canonical_sha256(_LEGACY_ASSIGNMENT_MARKER)
_SUBJECT_CANARY_UNIQUE_MESSAGE = (
    "UNIQUE constraint failed: evolution_candidates.kind, evolution_candidates.subject_key"
)


class EvolutionRepositoryError(RuntimeError):
    """Base error for evolution candidate persistence."""


class EvolutionRepositoryConflict(EvolutionRepositoryError):
    """The candidate identity, state, or expected version conflicts."""


class EvolutionRepositoryDecodeError(EvolutionRepositoryError):
    """A durable candidate row violates the v1 contract."""


class EvolutionAssignmentConflict(EvolutionRepositoryConflict):
    """A Memorial already has a different immutable assignment."""


class PromotionDecisionBindingV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    candidate_id: str
    candidate_version: int = Field(ge=1)
    candidate_artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_snapshot_version: int = Field(ge=0)
    action: Literal["promote"]
    risk_tier: Literal["high"]


_CodePromotionDecisionBindingV1 = PromotionDecisionBindingV1


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


def _decode_lifecycle_journal_transition(
    row: sqlite3.Row,
) -> tuple[CandidateLifecycle | None, CandidateLifecycle]:
    raw = row["entry_json"]
    if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != row["entry_hash"]:
        raise EvolutionRepositoryDecodeError("candidate lifecycle journal is corrupt")
    try:
        payload = cast(
            dict[str, object],
            _json_value(raw, field="entry_json", expected=dict),
        )
        if set(payload) != {
            "schema_version",
            "candidate_id",
            "candidate_version",
            "from_lifecycle",
            "to_lifecycle",
            "decision_request_id",
            "created_at",
        }:
            raise ValueError("unexpected lifecycle journal fields")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("unsupported lifecycle journal schema")
        if not isinstance(payload["candidate_id"], str) or not payload["candidate_id"].strip():
            raise ValueError("invalid lifecycle journal candidate id")
        if type(payload["candidate_version"]) is not int or payload["candidate_version"] < 1:
            raise ValueError("invalid lifecycle journal candidate version")
        from_lifecycle = (
            CandidateLifecycle(payload["from_lifecycle"])
            if isinstance(payload["from_lifecycle"], str)
            else None
        )
        if payload["from_lifecycle"] is not None and from_lifecycle is None:
            raise ValueError("invalid lifecycle journal source")
        if not isinstance(payload["to_lifecycle"], str):
            raise ValueError("invalid lifecycle journal target")
        to_lifecycle = CandidateLifecycle(payload["to_lifecycle"])
        decision_request_id = payload["decision_request_id"]
        if decision_request_id is not None and (
            not isinstance(decision_request_id, str) or not decision_request_id.strip()
        ):
            raise ValueError("invalid lifecycle journal decision")
        created_at = payload["created_at"]
        if not isinstance(created_at, str):
            raise ValueError("invalid lifecycle journal timestamp")
        parsed_created_at = datetime.fromisoformat(created_at)
        if parsed_created_at.tzinfo is None or parsed_created_at.isoformat() != created_at:
            raise ValueError("invalid lifecycle journal timestamp")
    except (KeyError, TypeError, ValueError) as exc:
        raise EvolutionRepositoryDecodeError("candidate lifecycle journal is corrupt") from exc

    expected_id = _journal_id(
        cast(str, payload["candidate_id"]),
        cast(int, payload["candidate_version"]),
        to_lifecycle,
    )
    if (
        row["journal_id"] != expected_id
        or row["candidate_id"] != payload["candidate_id"]
        or row["candidate_version"] != payload["candidate_version"]
        or row["from_lifecycle"] != (from_lifecycle.value if from_lifecycle is not None else None)
        or row["to_lifecycle"] != to_lifecycle.value
        or row["decision_request_id"] != decision_request_id
        or row["created_at"] != created_at
        or row["entry_hash"] != canonical_sha256(payload)
    ):
        raise EvolutionRepositoryDecodeError("candidate lifecycle journal conflicts")
    if from_lifecycle is not None:
        try:
            validate_lifecycle_transition(from_lifecycle, to_lifecycle)
        except ValueError as exc:
            raise EvolutionRepositoryDecodeError(
                "candidate lifecycle journal transition is illegal"
            ) from exc
    return from_lifecycle, to_lifecycle


def _require_high_risk_promotion_decision(
    connection: sqlite3.Connection,
    *,
    candidate: EvolutionCandidateV1,
    decision_request_id: str | None,
) -> None:
    if decision_request_id is None or not decision_request_id.strip():
        raise EvolutionRepositoryConflict(
            "high-risk promotion requires an explicit resolved high-risk Decision"
        )
    try:
        record = DecisionRepository().get(connection, decision_request_id)
        if record is None or record.resolution is None:
            raise ValueError("decision is missing or unresolved")
        binding = PromotionDecisionBindingV1.model_validate(record.request.payload)
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
            "high-risk promotion requires an explicit resolved high-risk Decision"
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


def _is_subject_canary_unique_conflict(error: sqlite3.IntegrityError) -> bool:
    return getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE and (
        str(error) == _SUBJECT_CANARY_UNIQUE_MESSAGE
        or "idx_evolution_candidates_subject_canary" in str(error)
    )


class EvolutionRepository:
    """Stateless primitives whose caller owns the SQLite transaction."""

    def require_high_risk_promotion_decision(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
        decision_request_id: str | None,
    ) -> None:
        """Preflight an exact Decision for a code-level high-risk candidate."""

        if candidate.kind not in HIGH_RISK_PROMOTION_KINDS:
            raise ValueError("promotion Decision validation requires a high-risk candidate")
        _require_high_risk_promotion_decision(
            connection,
            candidate=candidate,
            decision_request_id=decision_request_id,
        )

    def require_code_promotion_decision(
        self,
        connection: sqlite3.Connection,
        *,
        candidate: EvolutionCandidateV1,
        decision_request_id: str | None,
    ) -> None:
        """Compatibility alias for the original CODE-only public method."""

        self.require_high_risk_promotion_decision(
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

    def get_verified_lifecycle_transition_to(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
        to_lifecycle: CandidateLifecycle,
    ) -> tuple[int, CandidateLifecycle | None, CandidateLifecycle] | None:
        rows = connection.execute(
            """SELECT journal_id, candidate_id, candidate_version, from_lifecycle,
                      to_lifecycle, decision_request_id, entry_json, entry_hash, created_at
               FROM evolution_lifecycle_journal
               WHERE candidate_id=? AND to_lifecycle=?
               ORDER BY candidate_version""",
            (candidate_id, to_lifecycle.value),
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise EvolutionRepositoryDecodeError(
                "candidate lifecycle journal has multiple terminal transitions"
            )
        from_lifecycle, durable_to_lifecycle = _decode_lifecycle_journal_transition(rows[0])
        return rows[0]["candidate_version"], from_lifecycle, durable_to_lifecycle

    def get_routable_candidates(
        self, connection: sqlite3.Connection
    ) -> tuple[EvolutionCandidateV1, ...]:
        """Return one complete canary routing authority per governed subject."""

        rows = connection.execute(
            _SELECT_CANDIDATE
            + " WHERE lifecycle = 'canary'"
            + " ORDER BY kind, subject_key, created_at, candidate_id"
        ).fetchall()
        candidates = tuple(_decode_candidate(row) for row in rows)
        seen_subjects: set[tuple[CandidateKind, str]] = set()
        for candidate in candidates:
            subject = (candidate.kind, candidate.subject_key)
            if subject in seen_subjects:
                raise EvolutionRepositoryConflict("multiple canary routing authorities for subject")
            seen_subjects.add(subject)
        for candidate in candidates:
            routing = candidate.routing
            allocation = connection.execute(
                "SELECT * FROM evolution_routing_allocations WHERE candidate_id=?",
                (candidate.candidate_id,),
            ).fetchone()
            if routing is None or allocation is None:
                raise EvolutionRepositoryConflict("canary routing authority is incomplete")
            payload = routing.model_dump(mode="json")
            if (
                allocation["routing_version"] != routing.routing_version
                or allocation["allocation_basis_points"] != routing.allocation_basis_points
                or allocation["allocation_seed_id"] != routing.allocation_seed_id
                or allocation["routing_json"] != canonical_json_bytes(payload).decode("utf-8")
                or allocation["routing_hash"] != canonical_sha256(payload)
            ):
                raise EvolutionRepositoryConflict("canary routing authority conflicts")
        return candidates

    def get_routable_candidate(self, connection: sqlite3.Connection) -> EvolutionCandidateV1 | None:
        """Return the legacy single canary authority or fail closed if ambiguous."""

        candidates = self.get_routable_candidates(connection)
        if len(candidates) > 1:
            raise EvolutionRepositoryConflict("multiple canary routing authorities")
        return candidates[0] if candidates else None

    def _decode_subject_assignment_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> tuple[SubjectRunAssignmentV1, EffectiveEvolutionOverlayV1]:
        raw = row["assignment_json"]
        try:
            decoded = json.loads(raw) if isinstance(raw, str) else None
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EvolutionRepositoryDecodeError(
                "persisted subject assignment is not valid JSON"
            ) from exc
        if not isinstance(decoded, dict) or canonical_sha256(decoded) != row["assignment_hash"]:
            raise EvolutionRepositoryDecodeError("persisted subject assignment hash does not match")
        try:
            assignment = SubjectRunAssignmentV1.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise EvolutionRepositoryDecodeError(
                "persisted subject assignment violates the v1 contract"
            ) from exc
        if canonical_json_bytes(assignment).decode("utf-8") != raw:
            raise EvolutionRepositoryDecodeError("subject assignment_json is not canonical JSON")
        if (
            row["assignment_id"] != assignment.assignment_id
            or row["memorial_id"] != assignment.memorial_id
            or row["kind"] != assignment.kind.value
            or row["subject_key"] != assignment.subject_key
            or row["candidate_id"] != assignment.candidate_id
            or row["routing_version"] != assignment.routing_version
            or row["bucket"] != assignment.bucket
            or row["champion_ref_json"]
            != canonical_json_bytes(assignment.champion_ref).decode("utf-8")
            or row["selected_ref_json"]
            != canonical_json_bytes(assignment.selected_ref).decode("utf-8")
            or row["created_at"] != assignment.created_at.isoformat()
        ):
            raise EvolutionRepositoryDecodeError(
                "subject assignment columns conflict with assignment_json"
            )
        if assignment.candidate_id is None:
            if assignment.selected_ref != assignment.champion_ref:
                raise EvolutionRepositoryDecodeError(
                    "subject assignment without candidate selected a challenger"
                )
        else:
            candidate = self.get_candidate(connection, assignment.candidate_id)
            if (
                candidate is None
                or candidate.kind is not assignment.kind
                or candidate.subject_key != assignment.subject_key
                or candidate.base != assignment.champion_ref
                or assignment.selected_ref not in {candidate.base, candidate.candidate}
            ):
                raise EvolutionRepositoryDecodeError(
                    "subject assignment candidate attribution conflicts"
                )
        overlay = EffectiveEvolutionOverlayV1(
            assignment_id=assignment.assignment_id,
            kind=assignment.kind,
            subject_key=assignment.subject_key,
            artifact_digest=assignment.selected_ref.artifact_digest,
            canonical_digest=assignment.selected_ref.canonical_digest,
        )
        if row["overlay_digest"] != canonical_sha256(overlay):
            raise EvolutionRepositoryDecodeError("subject assignment overlay digest conflicts")
        return assignment, overlay

    def _get_subject_assignment(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        kind: CandidateKind,
        subject_key: str,
    ) -> tuple[SubjectRunAssignmentV1, EffectiveEvolutionOverlayV1] | None:
        row = connection.execute(
            """SELECT * FROM run_subject_assignments
               WHERE memorial_id=? AND kind=? AND subject_key=?""",
            (memorial_id, kind.value, subject_key),
        ).fetchone()
        return self._decode_subject_assignment_row(connection, row) if row is not None else None

    def get_assignment_set(
        self, connection: sqlite3.Connection, memorial_id: str
    ) -> RunAssignmentSetV1 | None:
        rows = connection.execute(
            """SELECT * FROM run_subject_assignments
               WHERE memorial_id=? ORDER BY kind, subject_key""",
            (memorial_id,),
        ).fetchall()
        if not rows:
            return None
        declared_hashes = {row["assignment_set_hash"] for row in rows}
        declared_sizes = {row["assignment_set_size"] for row in rows}
        if len(declared_hashes) != 1 or len(declared_sizes) != 1:
            raise EvolutionRepositoryDecodeError(
                "persisted run assignment set seal is inconsistent"
            )
        declared_hash = next(iter(declared_hashes))
        declared_size = next(iter(declared_sizes))
        if type(declared_size) is not int or declared_size != len(rows):
            raise EvolutionRepositoryDecodeError(
                "persisted run assignment set member count conflicts with its seal"
            )
        assignments = tuple(self._decode_subject_assignment_row(connection, row)[0] for row in rows)
        try:
            return RunAssignmentSetV1(
                memorial_id=memorial_id,
                assignments=assignments,
                set_hash=declared_hash,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise EvolutionRepositoryDecodeError(
                "persisted run assignment set violates the v1 contract"
            ) from exc

    @staticmethod
    def validate_assignment_projection(
        existing: tuple[
            RunAssignmentV1 | LegacyRunAssignmentV1,
            EffectiveEvolutionOverlayV1 | None,
        ]
        | None,
        assignment_set: RunAssignmentSetV1 | None,
    ) -> None:
        """Reject missing or contradictory legacy/V34 assignment shadows."""

        if existing is None:
            if assignment_set is not None:
                raise EvolutionAssignmentConflict("subject assignments require a legacy projection")
            return
        if assignment_set is None:
            return
        assignment, overlay = existing
        if len(assignment_set.assignments) > 1:
            if (
                not isinstance(assignment, LegacyRunAssignmentV1)
                or overlay is not None
                or assignment_set.memorial_id != assignment.memorial_id
                or any(
                    subject.created_at != assignment.created_at
                    for subject in assignment_set.assignments
                )
            ):
                raise EvolutionAssignmentConflict(
                    "multi-subject assignments require a legacy projection"
                )
            return
        if not isinstance(assignment, RunAssignmentV1) or overlay is None:
            raise EvolutionAssignmentConflict(
                "single-subject assignment shadow conflicts with legacy projection"
            )
        subject = assignment_set.assignments[0]
        if (
            subject.memorial_id != assignment.memorial_id
            or subject.candidate_id != assignment.candidate_id
            or subject.champion_ref != assignment.champion_ref
            or subject.selected_ref != assignment.selected_ref
            or subject.routing_version != assignment.routing_version
            or subject.bucket != assignment.bucket
            or subject.created_at != assignment.created_at
            or overlay.kind is not subject.kind
            or overlay.subject_key != subject.subject_key
            or overlay.artifact_digest != subject.selected_ref.artifact_digest
            or overlay.canonical_digest != subject.selected_ref.canonical_digest
        ):
            raise EvolutionAssignmentConflict(
                "single-subject assignment shadow conflicts with legacy projection"
            )

    def insert_assignment_set(
        self,
        connection: sqlite3.Connection,
        assignment_set: RunAssignmentSetV1,
        overlays: tuple[EffectiveEvolutionOverlayV1, ...],
    ) -> RunAssignmentSetV1:
        """Persist one complete immutable subject set or accept an exact replay."""

        if not connection.in_transaction:
            raise RuntimeError("assignment set writes require a caller-owned transaction")
        if len(overlays) != len(assignment_set.assignments):
            raise ValueError("assignment set overlays must match its members")
        for assignment, overlay in zip(assignment_set.assignments, overlays, strict=True):
            expected_overlay = EffectiveEvolutionOverlayV1(
                assignment_id=assignment.assignment_id,
                kind=assignment.kind,
                subject_key=assignment.subject_key,
                artifact_digest=assignment.selected_ref.artifact_digest,
                canonical_digest=assignment.selected_ref.canonical_digest,
            )
            if overlay != expected_overlay:
                raise ValueError("effective overlay does not match subject assignment")
            if assignment.candidate_id is None:
                if assignment.selected_ref != assignment.champion_ref:
                    raise ValueError("subject assignment without candidate must select champion")
            else:
                candidate = self.get_candidate(connection, assignment.candidate_id)
                if (
                    candidate is None
                    or candidate.kind is not assignment.kind
                    or candidate.subject_key != assignment.subject_key
                    or candidate.base != assignment.champion_ref
                    or assignment.selected_ref not in {candidate.base, candidate.candidate}
                ):
                    raise ValueError("candidate does not match subject assignment")

        existing = self.get_assignment_set(connection, assignment_set.memorial_id)
        if existing is not None:
            if existing != assignment_set:
                raise EvolutionAssignmentConflict("Memorial subject assignment set is immutable")
            return existing

        connection.execute("SAVEPOINT evolution_assignment_set_insert")
        try:
            for assignment, overlay in zip(
                assignment_set.assignments,
                overlays,
                strict=True,
            ):
                raw = canonical_json_bytes(assignment).decode("utf-8")
                connection.execute(
                    """INSERT INTO run_subject_assignments (
                           assignment_id, memorial_id, kind, subject_key, candidate_id,
                           routing_version, bucket, champion_ref_json, selected_ref_json,
                           overlay_digest, assignment_json, assignment_hash,
                           assignment_set_hash, assignment_set_size, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        assignment.assignment_id,
                        assignment.memorial_id,
                        assignment.kind.value,
                        assignment.subject_key,
                        assignment.candidate_id,
                        assignment.routing_version,
                        assignment.bucket,
                        canonical_json_bytes(assignment.champion_ref).decode("utf-8"),
                        canonical_json_bytes(assignment.selected_ref).decode("utf-8"),
                        canonical_sha256(overlay),
                        raw,
                        canonical_sha256(assignment),
                        assignment_set.set_hash,
                        len(assignment_set.assignments),
                        assignment.created_at.isoformat(),
                    ),
                )
            durable = self.get_assignment_set(connection, assignment_set.memorial_id)
            if durable != assignment_set:  # pragma: no cover - insert must preserve the set
                raise EvolutionAssignmentConflict("Memorial subject assignment set disappeared")
        except sqlite3.IntegrityError as exc:
            connection.execute("ROLLBACK TO SAVEPOINT evolution_assignment_set_insert")
            connection.execute("RELEASE SAVEPOINT evolution_assignment_set_insert")
            replay = self.get_assignment_set(connection, assignment_set.memorial_id)
            if replay == assignment_set:
                return replay
            raise EvolutionAssignmentConflict(
                "Memorial subject assignment set identity conflict"
            ) from exc
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT evolution_assignment_set_insert")
            connection.execute("RELEASE SAVEPOINT evolution_assignment_set_insert")
            raise
        connection.execute("RELEASE SAVEPOINT evolution_assignment_set_insert")
        return durable

    def insert_subject_assignment(
        self,
        connection: sqlite3.Connection,
        assignment: SubjectRunAssignmentV1,
        overlay: EffectiveEvolutionOverlayV1,
    ) -> SubjectRunAssignmentV1:
        """Compatibility wrapper that seals one subject as a singleton set."""

        material = {
            "memorial_id": assignment.memorial_id,
            "assignments": [assignment.model_dump(mode="json")],
        }
        assignment_set = RunAssignmentSetV1(
            memorial_id=assignment.memorial_id,
            assignments=(assignment,),
            set_hash=canonical_sha256(material),
        )
        return self.insert_assignment_set(connection, assignment_set, (overlay,)).assignments[0]

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

        if candidate.lifecycle in {
            CandidateLifecycle.CANARY,
            CandidateLifecycle.PROMOTED,
        }:
            policy = EvolutionPolicyRepository().get_policy(connection, candidate.subject_key)
            if policy is not None and policy.kind is not candidate.kind:
                raise EvolutionRepositoryConflict("evolution_policy_kind_conflict")
            mode = policy.mode if policy is not None else default_mode_for(candidate.kind)
            if mode == "frozen":
                raise EvolutionRepositoryConflict("subject_frozen")
            if candidate.lifecycle is CandidateLifecycle.CANARY:
                if mode != "canary":
                    raise EvolutionRepositoryConflict("policy_forbids_canary")
                if (
                    candidate.routing is not None
                    and candidate.routing.allocation_basis_points
                    > candidate.evolution_contract.max_canary_allocation_basis_points
                ):
                    raise EvolutionRepositoryConflict("allocation_exceeds_contract")
                if (
                    policy is not None
                    and candidate.routing is not None
                    and candidate.routing.allocation_basis_points > policy.max_canary_basis_points
                ):
                    raise EvolutionRepositoryConflict("allocation_exceeds_policy")
                subject_conflict = connection.execute(
                    """SELECT 1 FROM evolution_candidates
                       WHERE lifecycle='canary' AND kind=? AND subject_key=?
                         AND candidate_id<>?
                       LIMIT 1""",
                    (candidate.kind.value, candidate.subject_key, candidate.candidate_id),
                ).fetchone()
                if subject_conflict is not None:
                    raise EvolutionRepositoryConflict("subject_canary_exists")

        if (
            candidate.lifecycle is not current.lifecycle
            and candidate.kind in HIGH_RISK_PROMOTION_KINDS
            and candidate.lifecycle is CandidateLifecycle.PROMOTED
        ):
            self.require_high_risk_promotion_decision(
                connection,
                candidate=current,
                decision_request_id=high_risk_decision_request_id,
            )
        saved = candidate.model_copy(update={"version": expected_version + 1})
        try:
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
        except sqlite3.IntegrityError as exc:
            if _is_subject_canary_unique_conflict(exc):
                raise EvolutionRepositoryConflict("subject_canary_exists") from exc
            raise
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
