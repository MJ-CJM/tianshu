"""Strict domain and repository contracts for governed evolution candidates."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
)
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
    EvolutionContractV1,
    EvolutionProvenanceV1,
    GateName,
    RollbackSpecV1,
    RoutingPolicyV1,
    validate_lifecycle_transition,
)
from tianshu.storage.decision_repo import DecisionRepository
from tianshu.storage.evolution_repo import (
    EvolutionRepository,
    EvolutionRepositoryConflict,
    EvolutionRepositoryDecodeError,
)
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

NOW = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _contract(*, kind: CandidateKind = CandidateKind.SKILL) -> EvolutionContractV1:
    return EvolutionContractV1(
        kind=kind,
        subject_key="skill:reviewer" if kind is CandidateKind.SKILL else "code:tianshu",
        governance_contract_hash=DIGEST_A,
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.EVIDENCE),
        regression_policy_artifact_digest=DIGEST_B,
        sample_policy_artifact_digest=DIGEST_C,
        budget_policy_artifact_digest=DIGEST_D,
        minimum_canary_samples=10,
        max_canary_allocation_basis_points=1_000,
        rollback_slo_seconds=30,
    )


def _candidate(
    *,
    kind: CandidateKind = CandidateKind.SKILL,
    lifecycle: CandidateLifecycle = CandidateLifecycle.PROPOSED,
    version: int = 1,
    updated_at: datetime = NOW,
) -> EvolutionCandidateV1:
    contract = _contract(kind=kind)
    subject_key = contract.subject_key
    base = CandidateVersionRefV1(
        version="champion-v1", artifact_digest=DIGEST_A, canonical_digest=DIGEST_B
    )
    return EvolutionCandidateV1(
        candidate_id="candidate-1",
        kind=kind,
        subject_key=subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.REVIEWER,
            source_uri_redacted="review://bundle-1",
            source_digest=DIGEST_C,
            actor_principal_id="principal-1",
            actor_display_name="Reviewer",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="candidate-test",
            producer_version="1.0",
            received_at=NOW,
        ),
        base=base,
        candidate=CandidateVersionRefV1(
            version="candidate-v1", artifact_digest=DIGEST_C, canonical_digest=DIGEST_D
        ),
        diff_artifact_digest=DIGEST_D,
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=0,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="restore-1",
            adapter_name="skill",
            max_seconds=30,
        ),
        lifecycle=lifecycle,
        version=version,
        created_at=NOW,
        updated_at=updated_at,
    )


def _code_candidate_at_canary(
    connection: sqlite3.Connection,
) -> tuple[EvolutionRepository, EvolutionCandidateV1]:
    repository = EvolutionRepository()
    current = repository.insert_candidate(connection, _candidate(kind=CandidateKind.CODE))
    for lifecycle in (
        CandidateLifecycle.STAGED,
        CandidateLifecycle.EVALUATING,
        CandidateLifecycle.READY,
        CandidateLifecycle.CANARY,
    ):
        current = repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "updated_at": current.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=current.version,
        )
    return repository, current


def _insert_decision_roots(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-code-promote", "review code promotion", NOW.isoformat()),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-code-promote", "edict-code-promote", "submitted", NOW.isoformat()),
    )


def _promotion_decision_payload(candidate: EvolutionCandidateV1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
        "candidate_artifact_digest": candidate.candidate.artifact_digest,
        "gate_snapshot_version": candidate.gate_snapshot_version,
        "action": "promote",
        "risk_tier": "high",
    }


def _persist_promotion_decision(
    connection: sqlite3.Connection,
    candidate: EvolutionCandidateV1,
    *,
    payload_updates: dict[str, object] | None = None,
    resolve_action: str | None = "approve",
) -> str:
    _insert_decision_roots(connection)
    payload = {**_promotion_decision_payload(candidate), **(payload_updates or {})}
    now = candidate.updated_at
    request = DecisionRequestV1(
        decision_request_id="decision-code-promote",
        kind=DecisionKind.GOVERNED_APPLY,
        edict_id="edict-code-promote",
        memorial_id="memorial-code-promote",
        request_key="candidate-1:promote:5",
        payload=payload,
        payload_hash=canonical_sha256(payload),
        requested_by="principal-1",
        expires_at=now + timedelta(minutes=10),
        status=DecisionStatus.PENDING,
        version=1,
        created_at=now,
        updated_at=now,
    )
    repository = DecisionRepository()
    repository.add_or_get(connection, request)
    if resolve_action is not None:
        repository.resolve(
            connection,
            DecisionResolutionV1(
                decision_request_id=request.decision_request_id,
                action=resolve_action,
                reason="current high-risk code promotion reviewed",
                payload={"schema_version": 1},
                actor_principal_id="principal-reviewer",
                actor_display_name="Reviewer",
                resolved_at=now + timedelta(seconds=1),
            ),
            expected_version=1,
            now=now + timedelta(seconds=1),
        )
    return request.decision_request_id


@pytest.fixture
def connection() -> sqlite3.Connection:
    active = sqlite3.connect(":memory:")
    active.row_factory = sqlite3.Row
    active.execute("PRAGMA foreign_keys=ON")
    apply_migrations(active, MIGRATIONS)
    yield active
    active.close()


def test_candidate_schema_is_strict_frozen_and_canonical() -> None:
    candidate = _candidate()

    assert candidate.model_config["extra"] == "forbid"
    assert candidate.model_config["frozen"] is True
    assert canonical_sha256(candidate) == canonical_sha256(
        EvolutionCandidateV1.model_validate_json(candidate.model_dump_json())
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvolutionCandidateV1.model_validate({**candidate.model_dump(), "surprise": True})
    with pytest.raises(ValidationError, match="Instance is frozen"):
        candidate.provenance.actor_display_name = "Changed"


def test_contract_hash_kind_subject_and_automatic_promotion_are_invariants() -> None:
    candidate = _candidate()

    with pytest.raises(ValidationError, match="evolution_contract_hash"):
        EvolutionCandidateV1.model_validate(
            {**candidate.model_dump(), "evolution_contract_hash": DIGEST_A}
        )
    with pytest.raises(ValidationError, match="kind and subject_key"):
        EvolutionCandidateV1.model_validate({**candidate.model_dump(), "subject_key": "other"})
    with pytest.raises(ValidationError, match="automatic_promotion_allowed"):
        EvolutionContractV1.model_validate(
            {**candidate.evolution_contract.model_dump(), "automatic_promotion_allowed": True}
        )


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (CandidateLifecycle.PROPOSED, CandidateLifecycle.STAGED),
        (CandidateLifecycle.STAGED, CandidateLifecycle.EVALUATING),
        (CandidateLifecycle.EVALUATING, CandidateLifecycle.BLOCKED),
        (CandidateLifecycle.EVALUATING, CandidateLifecycle.READY),
        (CandidateLifecycle.BLOCKED, CandidateLifecycle.EVALUATING),
        (CandidateLifecycle.BLOCKED, CandidateLifecycle.REJECTED),
        (CandidateLifecycle.READY, CandidateLifecycle.CANARY),
        (CandidateLifecycle.READY, CandidateLifecycle.REJECTED),
        (CandidateLifecycle.CANARY, CandidateLifecycle.READY),
        (CandidateLifecycle.CANARY, CandidateLifecycle.PROMOTED),
        (CandidateLifecycle.CANARY, CandidateLifecycle.REJECTED),
        (CandidateLifecycle.CANARY, CandidateLifecycle.ROLLBACK_PENDING),
        (CandidateLifecycle.PROMOTED, CandidateLifecycle.ROLLBACK_PENDING),
        (CandidateLifecycle.PROMOTED, CandidateLifecycle.ARCHIVED),
        (CandidateLifecycle.ROLLBACK_PENDING, CandidateLifecycle.ROLLED_BACK),
        (CandidateLifecycle.ROLLED_BACK, CandidateLifecycle.ARCHIVED),
        (CandidateLifecycle.REJECTED, CandidateLifecycle.ARCHIVED),
    ],
)
def test_repository_accepts_each_legal_lifecycle_edge(
    source: CandidateLifecycle,
    target: CandidateLifecycle,
) -> None:
    validate_lifecycle_transition(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (CandidateLifecycle.PROPOSED, CandidateLifecycle.PROMOTED),
        (CandidateLifecycle.BLOCKED, CandidateLifecycle.CANARY),
        (CandidateLifecycle.ARCHIVED, CandidateLifecycle.PROPOSED),
    ],
)
def test_repository_rejects_illegal_lifecycle_edges(
    source: CandidateLifecycle,
    target: CandidateLifecycle,
) -> None:
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        validate_lifecycle_transition(source, target)


def test_repository_does_not_allow_inserting_a_candidate_after_proposed(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError, match="start as proposed"):
        EvolutionRepository().insert_candidate(
            connection, _candidate(lifecycle=CandidateLifecycle.READY)
        )


def test_repository_rejects_stale_version_and_provenance_rewrite(
    connection: sqlite3.Connection,
) -> None:
    repository = EvolutionRepository()
    original = _candidate()
    repository.insert_candidate(connection, original)

    with pytest.raises(EvolutionRepositoryConflict, match="compare-and-swap"):
        repository.save_candidate(connection, original, expected_version=2)
    changed = original.model_copy(
        update={
            "provenance": original.provenance.model_copy(
                update={"actor_display_name": "Other reviewer"}
            )
        }
    )
    with pytest.raises(EvolutionRepositoryConflict, match="provenance is immutable"):
        repository.save_candidate(connection, changed, expected_version=1)


def test_code_promote_requires_a_bound_resolved_high_risk_decision(
    connection: sqlite3.Connection,
) -> None:
    repository, current = _code_candidate_at_canary(connection)

    with pytest.raises(EvolutionRepositoryConflict, match="high-risk Decision"):
        repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": current.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=current.version,
        )


def test_code_promote_accepts_only_a_current_canonical_high_risk_decision(
    connection: sqlite3.Connection,
) -> None:
    repository, current = _code_candidate_at_canary(connection)
    decision_request_id = _persist_promotion_decision(connection, current)

    saved = repository.save_candidate(
        connection,
        current.model_copy(
            update={
                "lifecycle": CandidateLifecycle.PROMOTED,
                "updated_at": current.updated_at + timedelta(seconds=2),
            }
        ),
        expected_version=current.version,
        high_risk_decision_request_id=decision_request_id,
    )

    assert saved.lifecycle is CandidateLifecycle.PROMOTED
    assert saved.version == current.version + 1


@pytest.mark.parametrize(
    "payload_updates",
    [
        {"candidate_version": 1},
        {"candidate_artifact_digest": DIGEST_A},
        {"gate_snapshot_version": 999},
    ],
    ids=("stale-version", "wrong-digest", "wrong-gate-snapshot"),
)
def test_code_promote_rejects_a_stale_or_mismatched_decision_binding(
    connection: sqlite3.Connection,
    payload_updates: dict[str, object],
) -> None:
    repository, current = _code_candidate_at_canary(connection)
    decision_request_id = _persist_promotion_decision(
        connection, current, payload_updates=payload_updates
    )

    with pytest.raises(EvolutionRepositoryConflict, match="high-risk Decision"):
        repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": current.updated_at + timedelta(seconds=2),
                }
            ),
            expected_version=current.version,
            high_risk_decision_request_id=decision_request_id,
        )


@pytest.mark.parametrize(
    ("payload_updates", "resolve_action"),
    [
        ({"action": "start_canary"}, "approve"),
        ({"risk_tier": "medium"}, "approve"),
        ({}, None),
        ({}, "reject"),
    ],
    ids=("wrong-action", "wrong-risk", "pending", "rejected-resolution"),
)
def test_code_promote_rejects_wrong_request_or_resolution_state(
    connection: sqlite3.Connection,
    payload_updates: dict[str, object],
    resolve_action: str | None,
) -> None:
    repository, current = _code_candidate_at_canary(connection)
    decision_request_id = _persist_promotion_decision(
        connection,
        current,
        payload_updates=payload_updates,
        resolve_action=resolve_action,
    )

    with pytest.raises(EvolutionRepositoryConflict, match="high-risk Decision"):
        repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": current.updated_at + timedelta(seconds=2),
                }
            ),
            expected_version=current.version,
            high_risk_decision_request_id=decision_request_id,
        )


@pytest.mark.parametrize("corruption", ["payload", "hash"])
def test_code_promote_rejects_a_corrupt_decision_payload_or_hash(
    connection: sqlite3.Connection,
    corruption: str,
) -> None:
    repository, current = _code_candidate_at_canary(connection)
    decision_request_id = _persist_promotion_decision(connection, current)
    if corruption == "payload":
        corrupt_payload = {**_promotion_decision_payload(current), "candidate_version": 1}
        connection.execute(
            "UPDATE decision_requests SET payload_json = ? WHERE decision_request_id = ?",
            (
                json.dumps(corrupt_payload, separators=(",", ":"), sort_keys=True),
                decision_request_id,
            ),
        )
    else:
        connection.execute(
            "UPDATE decision_requests SET payload_hash = ? WHERE decision_request_id = ?",
            (DIGEST_A, decision_request_id),
        )

    with pytest.raises(EvolutionRepositoryConflict, match="high-risk Decision"):
        repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": current.updated_at + timedelta(seconds=2),
                }
            ),
            expected_version=current.version,
            high_risk_decision_request_id=decision_request_id,
        )


def test_code_promote_rejects_a_corrupt_resolution_payload(
    connection: sqlite3.Connection,
) -> None:
    repository, current = _code_candidate_at_canary(connection)
    decision_request_id = _persist_promotion_decision(connection, current, resolve_action=None)
    resolved_at = current.updated_at + timedelta(seconds=1)
    connection.execute(
        """
        INSERT INTO decision_resolutions (
            decision_request_id, action, reason, payload_json,
            actor_principal_id, actor_display_name, resolved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            decision_request_id,
            "approve",
            "invalid resolution payload",
            '{"schema_version":2}',
            "principal-reviewer",
            "Reviewer",
            resolved_at.isoformat(),
        ),
    )
    connection.execute(
        """
        UPDATE decision_requests
        SET status = 'resolved', version = 2, updated_at = ?
        WHERE decision_request_id = ?
        """,
        (resolved_at.isoformat(), decision_request_id),
    )

    with pytest.raises(EvolutionRepositoryConflict, match="high-risk Decision"):
        repository.save_candidate(
            connection,
            current.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": current.updated_at + timedelta(seconds=2),
                }
            ),
            expected_version=current.version,
            high_risk_decision_request_id=decision_request_id,
        )


def test_repository_rejects_noncanonical_or_hash_mismatched_provenance(
    connection: sqlite3.Connection,
) -> None:
    repository = EvolutionRepository()
    repository.insert_candidate(connection, _candidate())
    connection.execute(
        "UPDATE evolution_candidates SET provenance_hash = ? WHERE candidate_id = ?",
        (DIGEST_A, "candidate-1"),
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match="provenance"):
        repository.get_candidate(connection, "candidate-1")


def test_routing_policy_is_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        RoutingPolicyV1(
            allocation_basis_points=10_001,
            allocation_seed_id="seed-1",
            routing_version=1,
        )
