"""Write-side enforcement for governed per-subject evolution policies."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.evolution.test_candidate_adapters import (
    _proposal as candidate_proposal,
)
from tests.evolution.test_candidate_adapters import (
    _service as candidate_service,
)
from tests.evolution.test_promotion_fail_closed import (
    _candidate as promotion_candidate,
)
from tests.evolution.test_promotion_fail_closed import (
    _ready as ready_candidate,
)
from tests.evolution.test_promotion_fail_closed import (
    _service as promotion_service,
)
from tests.evolution.test_promotion_fail_closed import (
    _start_command as start_command,
)

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.candidate_service import CandidateServiceError
from tianshu.evolution.promotion import (
    PromoteCommand,
    PromotionConflict,
    RollbackCommand,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    EvolutionCandidateV1,
    RoutingPolicyV1,
)
from tianshu.models.evolution_policy import EvolutionPolicyMode, EvolutionPolicyV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict

NOW = datetime(2026, 8, 26, 8, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path: Path) -> Iterator[Storage]:
    active = Storage(str(tmp_path / "policy-enforcement.db"))
    active.init_db()
    yield active
    active.close()


@pytest.fixture
def artifact_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts"


@pytest.fixture
def artifacts(artifact_root: Path, storage: Storage) -> ArtifactStore:
    return ArtifactStore(
        artifact_root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: NOW,
    )


@pytest.fixture
def auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Operator",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="policy-enforcement-test",
    )


def _set_policy(
    storage: Storage,
    *,
    subject_key: str,
    kind: CandidateKind,
    mode: EvolutionPolicyMode,
    max_canary_basis_points: int,
) -> EvolutionPolicyV1:
    repository = EvolutionPolicyRepository()
    with storage.unit_of_work() as unit_of_work:
        current = repository.get_policy(unit_of_work.connection, subject_key)
        expected_version = current.version if current is not None else None
        durable = repository.upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=subject_key,
                kind=kind,
                mode=mode,
                max_canary_basis_points=max_canary_basis_points,
                version=expected_version or 1,
                updated_at=NOW + timedelta(seconds=expected_version or 0),
            ),
            expected_version=expected_version,
        )
        unit_of_work.commit()
    return durable


def _ready_subject(
    storage: Storage,
    *,
    candidate_id: str,
    subject_key: str,
    kind: CandidateKind = CandidateKind.SKILL,
) -> EvolutionCandidateV1:
    repository = EvolutionRepository()
    candidate = promotion_candidate(kind)
    contract = candidate.evolution_contract.model_copy(update={"subject_key": subject_key})
    candidate = candidate.model_copy(
        update={
            "candidate_id": candidate_id,
            "subject_key": subject_key,
            "evolution_contract": contract,
            "evolution_contract_hash": canonical_sha256(contract),
        }
    )
    with storage.unit_of_work() as unit_of_work:
        current = repository.insert_candidate(unit_of_work.connection, candidate)
        for lifecycle in (
            CandidateLifecycle.STAGED,
            CandidateLifecycle.EVALUATING,
            CandidateLifecycle.READY,
        ):
            current = repository.save_candidate(
                unit_of_work.connection,
                current.model_copy(
                    update={
                        "lifecycle": lifecycle,
                        "updated_at": current.updated_at + timedelta(seconds=1),
                    }
                ),
                expected_version=current.version,
            )
        unit_of_work.commit()
    return current


def _routing(allocation_basis_points: int = 100) -> RoutingPolicyV1:
    return RoutingPolicyV1(
        allocation_basis_points=allocation_basis_points,
        allocation_seed_id="policy-enforcement-seed",
        routing_version=1,
    )


def test_frozen_propose_rejects_before_any_artifact_or_callback(
    storage: Storage,
    artifacts: ArtifactStore,
    artifact_root: Path,
) -> None:
    proposal = candidate_proposal(CandidateKind.SKILL)
    _set_policy(
        storage,
        subject_key=proposal.subject_key,
        kind=proposal.kind,
        mode="frozen",
        max_canary_basis_points=0,
    )
    callbacks: list[str] = []

    with pytest.raises(CandidateServiceError, match="^subject_frozen$"):
        candidate_service(storage, artifacts).propose(
            proposal,
            on_persist=lambda _connection, candidate: callbacks.append(candidate.candidate_id),
        )

    with storage.unit_of_work() as unit_of_work:
        counts = tuple(
            unit_of_work.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("artifact_records", "evolution_candidates", "system_audit_events")
        )
        unit_of_work.commit()
    assert counts == (0, 0, 0)
    assert callbacks == []
    assert tuple(artifact_root.iterdir()) == ()


@pytest.mark.parametrize("mode", ("frozen", "manual"))
def test_non_canary_policy_rejects_start_without_routing_side_effect(
    storage: Storage,
    auth: AuthContext,
    mode: EvolutionPolicyMode,
) -> None:
    candidate = _ready_subject(
        storage,
        candidate_id=f"candidate-{mode}",
        subject_key=f"skill:{mode}",
    )
    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode=mode,
        max_canary_basis_points=0,
    )
    service, _gates, _adapter = promotion_service(storage, candidate)

    with pytest.raises(PromotionConflict, match="^policy_forbids_canary$"):
        service.start_canary(candidate.candidate_id, start_command(candidate), auth=auth)

    with storage.unit_of_work() as unit_of_work:
        routing_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        journal_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_promotion_journal"
        ).fetchone()[0]
        unit_of_work.commit()
    assert (routing_count, journal_count) == (0, 0)


def test_explicit_policy_cap_is_stricter_than_candidate_contract(
    storage: Storage,
    auth: AuthContext,
) -> None:
    candidate = _ready_subject(
        storage,
        candidate_id="candidate-policy-cap",
        subject_key="skill:policy-cap",
    )
    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="canary",
        max_canary_basis_points=100,
    )
    service, _gates, _adapter = promotion_service(storage, candidate)

    with pytest.raises(PromotionConflict, match="^allocation_exceeds_policy$"):
        service.start_canary(
            candidate.candidate_id,
            start_command(candidate, allocation=101),
            auth=auth,
        )


def test_p4a_temporarily_rejects_a_second_canary_for_a_different_subject(
    storage: Storage,
    auth: AuthContext,
) -> None:
    first = _ready_subject(
        storage,
        candidate_id="candidate-first-subject",
        subject_key="skill:first-subject",
    )
    second = _ready_subject(
        storage,
        candidate_id="candidate-second-subject",
        subject_key="skill:second-subject",
    )
    first_service, _first_gates, _first_adapter = promotion_service(storage, first)
    second_service, _second_gates, _second_adapter = promotion_service(storage, second)

    first_service.start_canary(first.candidate_id, start_command(first), auth=auth)
    with pytest.raises(PromotionConflict, match="^global_canary_exists$"):
        second_service.start_canary(
            second.candidate_id,
            start_command(second, key="start-second-subject"),
            auth=auth,
        )
    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^global_canary_exists$"),
    ):
        EvolutionRepository().save_candidate(
            unit_of_work.connection,
            second.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(),
                    "updated_at": second.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=second.version,
        )

    with storage.unit_of_work() as unit_of_work:
        routable = EvolutionRepository().get_routable_candidate(unit_of_work.connection)
        unit_of_work.commit()
    assert routable is not None and routable.candidate_id == first.candidate_id


def test_repository_is_policy_authority_for_canary_and_promoted_transitions(
    storage: Storage,
) -> None:
    subject_key = "skill:repository-authority"
    _set_policy(
        storage,
        subject_key=subject_key,
        kind=CandidateKind.SKILL,
        mode="frozen",
        max_canary_basis_points=0,
    )
    candidate = _ready_subject(
        storage,
        candidate_id="candidate-repository-authority",
        subject_key=subject_key,
    )
    assert candidate.lifecycle is CandidateLifecycle.READY
    repository = EvolutionRepository()

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^subject_frozen$"),
    ):
        repository.save_candidate(
            unit_of_work.connection,
            candidate.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(),
                    "updated_at": candidate.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=candidate.version,
        )

    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="canary",
        max_canary_basis_points=100,
    )
    with storage.unit_of_work() as unit_of_work:
        canary = repository.save_candidate(
            unit_of_work.connection,
            candidate.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(),
                    "updated_at": candidate.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=candidate.version,
        )
        unit_of_work.commit()
    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="frozen",
        max_canary_basis_points=0,
    )
    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^subject_frozen$"),
    ):
        repository.save_candidate(
            unit_of_work.connection,
            canary.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROMOTED,
                    "updated_at": canary.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=canary.version,
        )


def test_repository_rejects_policy_and_contract_allocation_bypass(storage: Storage) -> None:
    candidate = _ready_subject(
        storage,
        candidate_id="candidate-allocation-authority",
        subject_key="skill:allocation-authority",
    )
    repository = EvolutionRepository()
    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="canary",
        max_canary_basis_points=100,
    )

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^allocation_exceeds_policy$"),
    ):
        repository.save_candidate(
            unit_of_work.connection,
            candidate.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(101),
                    "updated_at": candidate.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=candidate.version,
        )

    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="canary",
        max_canary_basis_points=(candidate.evolution_contract.max_canary_allocation_basis_points),
    )
    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^allocation_exceeds_contract$"),
    ):
        repository.save_candidate(
            unit_of_work.connection,
            candidate.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(
                        candidate.evolution_contract.max_canary_allocation_basis_points + 1
                    ),
                    "updated_at": candidate.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=candidate.version,
        )

    with storage.unit_of_work() as unit_of_work:
        durable = repository.get_candidate(unit_of_work.connection, candidate.candidate_id)
        routing_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        unit_of_work.commit()
    assert durable == candidate
    assert routing_count == 0


def test_same_subject_canary_conflict_has_stable_repository_code(storage: Storage) -> None:
    first = _ready_subject(
        storage,
        candidate_id="candidate-same-subject-first",
        subject_key="skill:same-subject",
    )
    second = _ready_subject(
        storage,
        candidate_id="candidate-same-subject-second",
        subject_key="skill:same-subject",
    )
    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.save_candidate(
            unit_of_work.connection,
            first.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(),
                    "updated_at": first.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=first.version,
        )
        unit_of_work.commit()

    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(EvolutionRepositoryConflict, match="^subject_canary_exists$"),
    ):
        repository.save_candidate(
            unit_of_work.connection,
            second.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.CANARY,
                    "routing": _routing(),
                    "updated_at": second.updated_at + timedelta(seconds=1),
                }
            ),
            expected_version=second.version,
        )


def test_frozen_after_canary_blocks_new_promote_but_not_replay_or_rollback(
    storage: Storage,
    auth: AuthContext,
) -> None:
    candidate = ready_candidate(storage, CandidateKind.SKILL)
    service, _gates, adapter = promotion_service(storage, candidate)
    start = start_command(candidate, key="frozen-replay-start")
    canary = service.start_canary(candidate.candidate_id, start, auth=auth)
    _set_policy(
        storage,
        subject_key=candidate.subject_key,
        kind=candidate.kind,
        mode="frozen",
        max_canary_basis_points=0,
    )

    assert service.start_canary(candidate.candidate_id, start, auth=auth) == canary
    with pytest.raises(PromotionConflict, match="^subject_frozen$"):
        service.promote(
            candidate.candidate_id,
            PromoteCommand(
                expected_version=canary.candidate_version,
                idempotency_key="frozen-promote",
                reason="must remain frozen",
            ),
            auth=auth,
        )
    assert adapter.activate_calls == 0

    receipt = service.rollback(
        candidate.candidate_id,
        RollbackCommand(
            expected_version=canary.candidate_version,
            idempotency_key="frozen-rollback",
            reason="rollback remains an unconditional safety path",
        ),
        auth=auth,
    )
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert adapter.rollback_calls == 1
