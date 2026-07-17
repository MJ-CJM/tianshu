"""Strict domain and repository contracts for governed evolution candidates."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_sha256
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
