"""Canonical per-subject routing models and repository contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
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
)
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentSetV1,
    SubjectRunAssignmentV1,
)
from tianshu.storage.evolution_repo import (
    EvolutionAssignmentConflict,
    EvolutionRepository,
    EvolutionRepositoryConflict,
    EvolutionRepositoryDecodeError,
)

_NOW = datetime(2026, 8, 26, 8, tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _ref(value: str) -> CandidateVersionRefV1:
    return CandidateVersionRefV1(
        version=value,
        artifact_digest=_digest(f"artifact:{value}"),
        canonical_digest=_digest(f"canonical:{value}"),
    )


def _candidate(
    *,
    candidate_id: str,
    kind: CandidateKind,
    subject_key: str,
    base: CandidateVersionRefV1 | None = None,
    selected: CandidateVersionRefV1 | None = None,
    routed: bool = False,
) -> EvolutionCandidateV1:
    champion_ref = base or _ref(f"{candidate_id}:champion")
    challenger_ref = selected or _ref(f"{candidate_id}:challenger")
    contract = EvolutionContractV1(
        kind=kind,
        subject_key=subject_key,
        governance_contract_hash=_digest("governance"),
        required_gates=(GateName.SCHEMA, GateName.SECURITY, GateName.EVIDENCE),
        regression_policy_artifact_digest=_digest("regression"),
        sample_policy_artifact_digest=_digest("sample"),
        budget_policy_artifact_digest=_digest("budget"),
        minimum_canary_samples=1,
        max_canary_allocation_basis_points=1_000,
        rollback_slo_seconds=30,
    )
    return EvolutionCandidateV1(
        candidate_id=candidate_id,
        kind=kind,
        subject_key=subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.SYSTEM,
            source_uri_redacted=None,
            source_digest=challenger_ref.canonical_digest,
            actor_principal_id="system",
            actor_display_name="System",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="p4b-test",
            producer_version="1",
            received_at=_NOW,
        ),
        base=champion_ref,
        candidate=challenger_ref,
        diff_artifact_digest=_digest(f"diff:{candidate_id}"),
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=0,
        evidence_bundle_ids=(),
        routing=(
            RoutingPolicyV1(
                allocation_basis_points=1_000,
                allocation_seed_id=f"seed:{candidate_id}",
                routing_version=1,
            )
            if routed
            else None
        ),
        rollback=RollbackSpecV1(
            champion_ref=champion_ref,
            restore_point_ref=f"restore:{candidate_id}",
            adapter_name=kind.value,
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _assignment_id(memorial_id: str, kind: CandidateKind, subject_key: str) -> str:
    material = f"{memorial_id}\0{kind.value}\0{subject_key}".encode()
    return "assignment:" + sha256(material).hexdigest()


def _assignment(
    candidate: EvolutionCandidateV1,
    *,
    memorial_id: str = "memorial-1",
    selected_ref: CandidateVersionRefV1 | None = None,
    assignment_id: str | None = None,
) -> SubjectRunAssignmentV1:
    return SubjectRunAssignmentV1(
        assignment_id=assignment_id
        or _assignment_id(memorial_id, candidate.kind, candidate.subject_key),
        memorial_id=memorial_id,
        kind=candidate.kind,
        subject_key=candidate.subject_key,
        candidate_id=candidate.candidate_id,
        champion_ref=candidate.base,
        selected_ref=selected_ref or candidate.candidate,
        routing_version=1,
        bucket=42,
        created_at=_NOW,
    )


def _overlay(assignment: SubjectRunAssignmentV1) -> EffectiveEvolutionOverlayV1:
    return EffectiveEvolutionOverlayV1(
        assignment_id=assignment.assignment_id,
        kind=assignment.kind,
        subject_key=assignment.subject_key,
        artifact_digest=assignment.selected_ref.artifact_digest,
        canonical_digest=assignment.selected_ref.canonical_digest,
    )


def _set_hash(memorial_id: str, assignments: tuple[SubjectRunAssignmentV1, ...]) -> str:
    return canonical_sha256(
        {
            "memorial_id": memorial_id,
            "assignments": [assignment.model_dump(mode="json") for assignment in assignments],
        }
    )


def _assignment_set(
    memorial_id: str,
    assignments: tuple[SubjectRunAssignmentV1, ...],
) -> RunAssignmentSetV1:
    return RunAssignmentSetV1(
        memorial_id=memorial_id,
        assignments=assignments,
        set_hash=_set_hash(memorial_id, assignments),
    )


def _seed_memorial(storage) -> None:  # type: ignore[no-untyped-def]
    storage._conn.execute(  # noqa: SLF001
        "INSERT INTO edicts (id, goal, created_at) VALUES ('edict-1', 'test', ?)",
        (_NOW.isoformat(),),
    )
    storage._conn.execute(  # noqa: SLF001
        """INSERT INTO memorials (id, edict_id, status, created_at)
           VALUES ('memorial-1', 'edict-1', 'pending', ?)""",
        (_NOW.isoformat(),),
    )


def _insert_routable(storage, candidate: EvolutionCandidateV1) -> None:  # type: ignore[no-untyped-def]
    repository = EvolutionRepository()
    repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        "UPDATE evolution_candidates SET lifecycle='canary' WHERE candidate_id=?",
        (candidate.candidate_id,),
    )
    assert candidate.routing is not None
    payload = candidate.routing.model_dump(mode="json")
    storage._conn.execute(  # noqa: SLF001
        """INSERT INTO evolution_routing_allocations (
               candidate_id, routing_version, allocation_basis_points,
               allocation_seed_id, routing_json, routing_hash,
               version, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (
            candidate.candidate_id,
            candidate.routing.routing_version,
            candidate.routing.allocation_basis_points,
            candidate.routing.allocation_seed_id,
            canonical_json_bytes(payload).decode("utf-8"),
            canonical_sha256(payload),
            _NOW.isoformat(),
            _NOW.isoformat(),
        ),
    )


def test_assignment_set_requires_one_canonical_unique_memorial_bound_sequence() -> None:
    policy = _assignment(
        _candidate(
            candidate_id="candidate-policy",
            kind=CandidateKind.POLICY,
            subject_key="policy:default",
        )
    )
    skill = _assignment(
        _candidate(
            candidate_id="candidate-skill",
            kind=CandidateKind.SKILL,
            subject_key="skill:reviewer",
        )
    )
    assignments = (policy, skill)
    assignment_set = RunAssignmentSetV1(
        memorial_id="memorial-1",
        assignments=assignments,
        set_hash=_set_hash("memorial-1", assignments),
    )
    assert assignment_set.assignments == assignments

    with pytest.raises(ValidationError, match="canonically sorted"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=tuple(reversed(assignments)),
            set_hash=_set_hash("memorial-1", tuple(reversed(assignments))),
        )
    with pytest.raises(ValidationError, match="pairs must be unique"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=(policy, policy.model_copy(update={"assignment_id": "duplicate"})),
            set_hash="0" * 64,
        )
    with pytest.raises(ValidationError, match="same Memorial"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=(policy.model_copy(update={"memorial_id": "memorial-2"}),),
            set_hash="0" * 64,
        )
    with pytest.raises(ValidationError, match="hash does not match"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=(policy,),
            set_hash="0" * 64,
        )


def test_multi_subject_projection_requires_matching_memorial_and_timestamp() -> None:
    assignments = tuple(
        _assignment(
            _candidate(
                candidate_id=f"candidate-{name}",
                kind=CandidateKind.SKILL,
                subject_key=f"skill:{name}",
            )
        )
        for name in ("reviewer", "writer")
    )
    assignment_set = _assignment_set("memorial-1", assignments)
    legacy = LegacyRunAssignmentV1(
        assignment_id="legacy-memorial-1",
        memorial_id="memorial-1",
        created_at=_NOW,
    )
    EvolutionRepository.validate_assignment_projection((legacy, None), assignment_set)

    with pytest.raises(EvolutionAssignmentConflict, match="multi-subject"):
        EvolutionRepository.validate_assignment_projection(
            (legacy.model_copy(update={"memorial_id": "memorial-other"}), None),
            assignment_set,
        )
    drifted = (
        assignments[0],
        assignments[1].model_copy(update={"created_at": _NOW + timedelta(seconds=1)}),
    )
    with pytest.raises(EvolutionAssignmentConflict, match="multi-subject"):
        EvolutionRepository.validate_assignment_projection(
            (legacy, None),
            _assignment_set("memorial-1", drifted),
        )


def test_assignment_set_enforces_the_frozen_one_to_sixty_four_bound() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=(),
            set_hash="0" * 64,
        )
    assignments = tuple(
        _assignment(
            _candidate(
                candidate_id=f"candidate-{index:02d}",
                kind=CandidateKind.SKILL,
                subject_key=f"skill:{index:02d}",
            )
        )
        for index in range(65)
    )
    with pytest.raises(ValidationError, match="at most 64 items"):
        RunAssignmentSetV1(
            memorial_id="memorial-1",
            assignments=assignments,
            set_hash=_set_hash("memorial-1", assignments),
        )


def test_repository_persists_idempotent_assignments_and_returns_a_canonical_set(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    skill_candidate = _candidate(
        candidate_id="candidate-skill",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    policy_candidate = _candidate(
        candidate_id="candidate-policy",
        kind=CandidateKind.POLICY,
        subject_key="policy:default",
    )
    repository.insert_candidate(storage._conn, skill_candidate)  # noqa: SLF001
    repository.insert_candidate(storage._conn, policy_candidate)  # noqa: SLF001
    skill_assignment = _assignment(skill_candidate)
    policy_assignment = _assignment(policy_candidate)

    assignments = (policy_assignment, skill_assignment)
    requested = _assignment_set("memorial-1", assignments)
    assert (
        repository.insert_assignment_set(  # noqa: SLF001
            storage._conn,
            requested,
            tuple(_overlay(assignment) for assignment in assignments),
        )
        == requested
    )
    before = tuple(
        storage._conn.execute(  # noqa: SLF001
            "SELECT rowid FROM run_subject_assignments ORDER BY kind, subject_key"
        )
    )
    assert (
        repository.insert_assignment_set(  # noqa: SLF001
            storage._conn,
            requested,
            tuple(_overlay(assignment) for assignment in assignments),
        )
        == requested
    )
    assert (
        tuple(
            storage._conn.execute(  # noqa: SLF001
                "SELECT rowid FROM run_subject_assignments ORDER BY kind, subject_key"
            )
        )
        == before
    )

    assignment_set = repository.get_assignment_set(storage._conn, "memorial-1")  # noqa: SLF001
    assert assignment_set is not None
    assert assignment_set.assignments == (policy_assignment, skill_assignment)
    assert assignment_set.set_hash == _set_hash("memorial-1", assignment_set.assignments)
    assert repository.get_assignment_set(storage._conn, "missing") is None  # noqa: SLF001


def test_repository_seals_a_complete_set_against_later_subject_append(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    candidates = (
        _candidate(
            candidate_id="candidate-policy",
            kind=CandidateKind.POLICY,
            subject_key="policy:default",
        ),
        _candidate(
            candidate_id="candidate-skill",
            kind=CandidateKind.SKILL,
            subject_key="skill:reviewer",
        ),
        _candidate(
            candidate_id="candidate-third",
            kind=CandidateKind.SKILL,
            subject_key="skill:third",
        ),
    )
    for candidate in candidates:
        repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    assignments = tuple(_assignment(candidate) for candidate in candidates[:2])
    sealed = _assignment_set("memorial-1", assignments)
    repository.insert_assignment_set(  # noqa: SLF001
        storage._conn,
        sealed,
        tuple(_overlay(assignment) for assignment in assignments),
    )
    third = _assignment(candidates[2])

    with pytest.raises(EvolutionAssignmentConflict, match="set is immutable"):
        repository.insert_subject_assignment(storage._conn, third, _overlay(third))  # noqa: SLF001

    assert repository.get_assignment_set(storage._conn, "memorial-1") == sealed  # noqa: SLF001
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        == 2
    )


def test_assignment_set_batch_rolls_back_every_member_on_a_late_identity_conflict(storage) -> None:
    _seed_memorial(storage)
    storage._conn.execute(  # noqa: SLF001
        """INSERT INTO memorials (id, edict_id, status, created_at)
           VALUES ('memorial-2', 'edict-1', 'pending', ?)""",
        (_NOW.isoformat(),),
    )
    repository = EvolutionRepository()
    policy = _candidate(
        candidate_id="candidate-policy",
        kind=CandidateKind.POLICY,
        subject_key="policy:default",
    )
    skill = _candidate(
        candidate_id="candidate-skill",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    repository.insert_candidate(storage._conn, policy)  # noqa: SLF001
    repository.insert_candidate(storage._conn, skill)  # noqa: SLF001
    policy_assignment = _assignment(policy)
    skill_assignment = _assignment(skill)
    collision = _assignment(
        skill,
        memorial_id="memorial-2",
        assignment_id=skill_assignment.assignment_id,
    )
    repository.insert_subject_assignment(storage._conn, collision, _overlay(collision))  # noqa: SLF001
    requested = _assignment_set("memorial-1", (policy_assignment, skill_assignment))

    with pytest.raises(EvolutionAssignmentConflict, match="identity conflict"):
        repository.insert_assignment_set(  # noqa: SLF001
            storage._conn,
            requested,
            (_overlay(policy_assignment), _overlay(skill_assignment)),
        )

    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id='memorial-2'"
        ).fetchone()[0]
        == 1
    )


def test_repository_allows_an_unattributed_champion_placeholder_to_be_cleaned_up(storage) -> None:
    _seed_memorial(storage)
    champion = _ref("placeholder")
    assignment = SubjectRunAssignmentV1(
        assignment_id=_assignment_id("memorial-1", CandidateKind.SKILL, "skill:placeholder"),
        memorial_id="memorial-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:placeholder",
        candidate_id=None,
        champion_ref=champion,
        selected_ref=champion,
        routing_version=1,
        bucket=0,
        created_at=_NOW,
    )
    repository = EvolutionRepository()
    repository.insert_subject_assignment(storage._conn, assignment, _overlay(assignment))  # noqa: SLF001

    storage._conn.execute(  # noqa: SLF001
        "DELETE FROM run_subject_assignments WHERE assignment_id=?",
        (assignment.assignment_id,),
    )
    assert repository.get_assignment_set(storage._conn, "memorial-1") is None  # noqa: SLF001


def test_assignment_set_reader_fails_closed_on_a_partial_sealed_set(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    champion = _ref("placeholder")
    assignments: list[SubjectRunAssignmentV1] = []
    for index in range(2):
        subject_key = f"skill:{index:02d}"
        assignments.append(
            SubjectRunAssignmentV1(
                assignment_id=_assignment_id("memorial-1", CandidateKind.SKILL, subject_key),
                memorial_id="memorial-1",
                kind=CandidateKind.SKILL,
                subject_key=subject_key,
                candidate_id=None,
                champion_ref=champion,
                selected_ref=champion,
                routing_version=1,
                bucket=0,
                created_at=_NOW,
            )
        )
    requested = _assignment_set("memorial-1", tuple(assignments))
    repository.insert_assignment_set(  # noqa: SLF001
        storage._conn,
        requested,
        tuple(_overlay(assignment) for assignment in assignments),
    )
    storage._conn.execute(  # noqa: SLF001
        "DELETE FROM run_subject_assignments WHERE assignment_id=?",
        (assignments[1].assignment_id,),
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match="member count"):
        repository.get_assignment_set(storage._conn, "memorial-1")  # noqa: SLF001
    with pytest.raises(EvolutionRepositoryDecodeError, match="member count"):
        repository.insert_assignment_set(  # noqa: SLF001
            storage._conn,
            requested,
            tuple(_overlay(assignment) for assignment in assignments),
        )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_subject_assignments WHERE memorial_id='memorial-1'"
        ).fetchone()[0]
        == 1
    )


def test_repository_rejects_overlay_candidate_and_identity_conflicts(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    candidate = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    other = _candidate(
        candidate_id="candidate-2",
        kind=CandidateKind.POLICY,
        subject_key="policy:default",
    )
    repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    repository.insert_candidate(storage._conn, other)  # noqa: SLF001
    assignment = _assignment(candidate)
    wrong_overlay = _overlay(assignment).model_copy(update={"subject_key": "skill:other"})
    with pytest.raises(ValueError, match="overlay does not match"):
        repository.insert_subject_assignment(storage._conn, assignment, wrong_overlay)  # noqa: SLF001

    repository.insert_subject_assignment(storage._conn, assignment, _overlay(assignment))  # noqa: SLF001
    with pytest.raises(EvolutionAssignmentConflict, match="immutable"):
        repository.insert_subject_assignment(
            storage._conn,  # noqa: SLF001
            assignment.model_copy(update={"bucket": 43}),
            _overlay(assignment),
        )
    colliding = _assignment(other, assignment_id=assignment.assignment_id)
    with pytest.raises(EvolutionAssignmentConflict, match="set is immutable"):
        repository.insert_subject_assignment(storage._conn, colliding, _overlay(colliding))  # noqa: SLF001


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("assignment_hash", "0" * 64, "hash does not match"),
        ("bucket", 43, "columns conflict"),
        ("overlay_digest", "0" * 64, "overlay digest conflicts"),
        ("assignment_set_hash", "0" * 64, "set violates"),
        ("assignment_set_size", 2, "member count"),
    ],
)
def test_assignment_set_reader_fails_closed_on_hash_column_or_overlay_drift(
    storage,
    column: str,
    value: object,
    message: str,
) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    candidate = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    assignment = _assignment(candidate)
    repository.insert_subject_assignment(storage._conn, assignment, _overlay(assignment))  # noqa: SLF001
    storage._conn.execute("DROP TRIGGER run_subject_assignments_no_update")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        f"UPDATE run_subject_assignments SET {column}=? WHERE assignment_id=?",
        (value, assignment.assignment_id),
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match=message):
        repository.get_assignment_set(storage._conn, "memorial-1")  # noqa: SLF001


def test_assignment_set_reader_rejects_noncanonical_json(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    candidate = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    assignment = _assignment(candidate)
    repository.insert_subject_assignment(storage._conn, assignment, _overlay(assignment))  # noqa: SLF001
    raw = json.dumps(assignment.model_dump(mode="json"), indent=2)
    storage._conn.execute("DROP TRIGGER run_subject_assignments_no_update")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_subject_assignments SET assignment_json=? WHERE assignment_id=?",
        (raw, assignment.assignment_id),
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match="not canonical"):
        repository.get_assignment_set(storage._conn, "memorial-1")  # noqa: SLF001


def test_assignment_set_reader_rejects_candidate_attribution_drift(storage) -> None:
    _seed_memorial(storage)
    repository = EvolutionRepository()
    candidate = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    other = _candidate(
        candidate_id="candidate-2",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
    )
    repository.insert_candidate(storage._conn, candidate)  # noqa: SLF001
    repository.insert_candidate(storage._conn, other)  # noqa: SLF001
    assignment = _assignment(candidate)
    repository.insert_subject_assignment(storage._conn, assignment, _overlay(assignment))  # noqa: SLF001
    drifted = assignment.model_copy(update={"candidate_id": other.candidate_id})
    raw = canonical_json_bytes(drifted).decode("utf-8")
    storage._conn.execute("DROP TRIGGER run_subject_assignments_no_update")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        """UPDATE run_subject_assignments
           SET candidate_id=?, assignment_json=?, assignment_hash=?
           WHERE assignment_id=?""",
        (
            other.candidate_id,
            raw,
            canonical_sha256(drifted),
            assignment.assignment_id,
        ),
    )

    with pytest.raises(EvolutionRepositoryDecodeError, match="candidate attribution conflicts"):
        repository.get_assignment_set(storage._conn, "memorial-1")  # noqa: SLF001


def test_get_routable_candidates_returns_one_verified_authority_per_subject(storage) -> None:
    skill = _candidate(
        candidate_id="candidate-skill",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
        routed=True,
    )
    policy = _candidate(
        candidate_id="candidate-policy",
        kind=CandidateKind.POLICY,
        subject_key="policy:default",
        routed=True,
    )
    _insert_routable(storage, skill)
    _insert_routable(storage, policy)
    repository = EvolutionRepository()

    candidates = repository.get_routable_candidates(storage._conn)  # noqa: SLF001
    assert tuple(candidate.candidate_id for candidate in candidates) == (
        policy.candidate_id,
        skill.candidate_id,
    )
    assert all(candidate.lifecycle is CandidateLifecycle.CANARY for candidate in candidates)
    with pytest.raises(EvolutionRepositoryConflict, match="multiple canary routing authorities$"):
        repository.get_routable_candidate(storage._conn)  # noqa: SLF001


def test_get_routable_candidates_rejects_multiple_authorities_for_one_subject(storage) -> None:
    first = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
        routed=True,
    )
    second = _candidate(
        candidate_id="candidate-2",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
        routed=True,
    )
    storage._conn.execute("DROP INDEX idx_evolution_candidates_subject_canary")  # noqa: SLF001
    _insert_routable(storage, first)
    _insert_routable(storage, second)

    with pytest.raises(
        EvolutionRepositoryConflict,
        match="multiple canary routing authorities for subject",
    ):
        EvolutionRepository().get_routable_candidates(storage._conn)  # noqa: SLF001


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("routing_version", 2),
        ("allocation_basis_points", 999),
        ("allocation_seed_id", "wrong-seed"),
        ("routing_json", "{}"),
        ("routing_hash", "0" * 64),
    ],
)
def test_get_routable_candidates_performs_all_five_allocation_cross_checks(
    storage,
    column: str,
    value: object,
) -> None:
    candidate = _candidate(
        candidate_id="candidate-1",
        kind=CandidateKind.SKILL,
        subject_key="skill:reviewer",
        routed=True,
    )
    _insert_routable(storage, candidate)
    storage._conn.execute(  # noqa: SLF001
        f"UPDATE evolution_routing_allocations SET {column}=? WHERE candidate_id=?",
        (value, candidate.candidate_id),
    )

    with pytest.raises(EvolutionRepositoryConflict, match="routing authority conflicts"):
        EvolutionRepository().get_routable_candidates(storage._conn)  # noqa: SLF001
