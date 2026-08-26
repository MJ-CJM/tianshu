"""End-to-end acceptance path for governed executor self-evolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

import pytest

from tests.evolution.test_executor_drift_scanner import (
    _FakeMaterializer as _DriftMaterializer,
)
from tests.evolution.test_executor_drift_scanner import (
    _install_active_baseline,
    _release,
    _scanner,
)
from tests.evolution.test_executor_promotion import _Executor, _Materializer
from tests.evolution.test_gate_evaluator import _evidence_id, _seed_gate_evidence
from tests.universe.test_challenger_routing import _seed_canary, _seed_memorial
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.executor_promotion import ExecutorPromotionAdapter
from tianshu.evolution.candidate_service import CandidateLiveAuthorities, CandidateService
from tianshu.evolution.gates import REQUIRED_GATES, GateEvaluator, GateStatus
from tianshu.evolution.promotion import (
    PromoteCommand,
    PromotionConflict,
    PromotionService,
    RollbackCommand,
    StartCanaryCommand,
)
from tianshu.evolution.runtime_context import current_run_binding
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.capabilities import pi_manifest
from tianshu.executor.generation_controller import GenerationController
from tianshu.governance.decision_service import DecisionService
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    DecisionStatus,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.evolution_candidate import CandidateKind, CandidateLifecycle
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.runtime_generation import RuntimeGenerationState
from tianshu.storage import Storage
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.universe.router import ChallengerRouter

_NOW = datetime(2026, 8, 26, 14, tzinfo=UTC)
_SCOPE = "executor:keqing:pi"
_SKILL_SUBJECT = "skill:demo-reviewer"
_SKILL_SEED = "skill-demo-seed"
_EXECUTOR_SEED = "executor-demo-seed"


def _candidate_service(
    storage: Storage,
    artifacts: ArtifactStore,
    tmp_path: Path,
) -> CandidateService:
    roots = {
        name: tmp_path / "live" / name
        for name in ("memory", "skill", "policy", "persona", "code", "executor")
    }
    for root in roots.values():
        root.mkdir(parents=True)
    return CandidateService(
        storage,
        artifacts,
        live_authorities=CandidateLiveAuthorities(
            memory_root=roots["memory"],
            skill_target=roots["skill"],
            policy_root=roots["policy"],
            persona_root=roots["persona"],
            code_worktree=roots["code"],
            executor_root=roots["executor"],
        ),
        clock=lambda: _NOW,
    )


def _operator(candidate_actor_id: str) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=candidate_actor_id,
            kind=PrincipalKind.SERVICE,
            display_name="Executor evolution operator",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.SYSTEM,
        correlation_id="executor-evolution-demo",
    )


def _reviewer() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="reviewer:executor-evolution-demo",
            kind=PrincipalKind.HUMAN,
            display_name="Executor evolution reviewer",
            scopes=frozenset({"workspace:apply"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="executor-evolution-demo-review",
    )


def _candidate(storage: Storage, candidate_id: str):
    with storage.unit_of_work() as unit_of_work:
        candidate = EvolutionRepository().get_candidate(
            unit_of_work.connection,
            candidate_id,
        )
        unit_of_work.commit()
    assert candidate is not None
    return candidate


def _assignment_set(storage: Storage, memorial_id: str):
    with storage.unit_of_work() as unit_of_work:
        assignment_set = EvolutionRepository().get_assignment_set(
            unit_of_work.connection,
            memorial_id,
        )
        unit_of_work.commit()
    assert assignment_set is not None
    return assignment_set


def _generation(storage: Storage, generation_id: str):
    with storage.unit_of_work() as unit_of_work:
        generation = GenerationRepository().get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    assert generation is not None
    return generation


def _pointer(storage: Storage):
    with storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=_SCOPE,
        )
        unit_of_work.commit()
    assert pointer is not None
    return pointer


def _snapshot_resolver() -> SystemSnapshotResolver:
    return SystemSnapshotResolver(
        kernel_facts=lambda: {"tianshu_version": "executor-evolution-demo"},
        executor_digests=lambda: {"keqing:pi": canonical_sha256("static-pi")},
        skills_digest=lambda: canonical_sha256("skills"),
        personas_digest=lambda: canonical_sha256("personas"),
        policy_rules_digest=lambda: canonical_sha256("policies"),
        provider_profiles_digest=lambda: canonical_sha256("providers"),
    )


def _resolved_promotion_decision(
    storage: Storage,
    *,
    candidate_id: str,
    operator: AuthContext,
    now: list[datetime],
) -> str:
    candidate = _candidate(storage, candidate_id)
    _seed_memorial(storage, "executor-promotion-decision")
    decision_service = DecisionService(storage, clock=lambda: now[0])
    payload = {
        "schema_version": 1,
        "candidate_id": candidate.candidate_id,
        "candidate_version": candidate.version,
        "candidate_artifact_digest": candidate.candidate.artifact_digest,
        "gate_snapshot_version": candidate.gate_snapshot_version,
        "action": "promote",
        "risk_tier": "high",
    }
    request = decision_service.request(
        RequestDecisionCommand(
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id="edict-executor-promotion-decision",
            memorial_id="executor-promotion-decision",
            request_key=f"executor-promote:{candidate.candidate_id}:{candidate.version}",
            payload=payload,
            expires_at=now[0] + timedelta(hours=1),
        ),
        auth=operator,
    )
    now[0] += timedelta(seconds=1)
    decision_service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="reviewed high-risk executor generation",
            payload={"schema_version": 1},
            expected_version=request.version,
        ),
        auth=_reviewer(),
    )
    record = decision_service.get(request.decision_request_id)
    assert record is not None
    assert record.request.status is DecisionStatus.RESOLVED
    assert record.resolution is not None and record.resolution.action == "approve"
    return request.decision_request_id


@pytest.mark.asyncio
async def test_executor_evolution_demo_end_to_end(
    storage: Storage,
    tmp_path: Path,
) -> None:
    artifacts = ArtifactStore(
        tmp_path / "candidate-artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: _NOW,
    )
    candidates = _candidate_service(storage, artifacts, tmp_path)
    base_release = _release("demo-base")
    challenger_release = _release("demo-challenger")
    _install_active_baseline(storage, base_release)

    authority_repository = ExecutorGenerationAuthorityRepository()
    registry = ExecutorAdapterRegistry((_Executor(pi_manifest()),))

    async def warm_probe(_bundle: object) -> tuple[bool, str | None]:
        return True, None

    def recovery_roots(connection) -> frozenset[str]:  # type: ignore[no-untyped-def]
        return frozenset(
            authority.generation_id
            for authority in authority_repository.list_recovery_roots(connection)
        )

    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        registry,
        warm_probe=warm_probe,
        required_scope_provider=lambda _connection, _memorial_id: (_SCOPE,),
        recovery_root_provider=recovery_roots,
        clock=lambda: _NOW + timedelta(seconds=3),
    )
    recovered = controller.recover()
    base_generation_id = _pointer(storage).active_generation_id
    assert recovered.materialized_generation_ids == (base_generation_id,)
    assert _generation(storage, base_generation_id).state is RuntimeGenerationState.ACTIVE

    scanner = _scanner(
        storage,
        candidates,
        _DriftMaterializer(challenger_release),
    )
    assert scanner.scan_once() == 1
    assert scanner.last_candidate_id is not None
    executor_id = scanner.last_candidate_id
    proposed = _candidate(storage, executor_id)
    assert proposed.kind is CandidateKind.EXECUTOR
    assert proposed.lifecycle is CandidateLifecycle.PROPOSED
    assert proposed.base.canonical_digest == canonical_sha256(base_release)
    assert proposed.candidate.canonical_digest == canonical_sha256(challenger_release)

    staged = candidates.stage(executor_id).candidate
    assert staged.lifecycle is CandidateLifecycle.STAGED
    evidence_service = _seed_gate_evidence(
        storage,
        tmp_path / "gate-artifacts",
        staged,
        close=True,
        bind_candidate=True,
        evidence_time=_NOW + timedelta(seconds=1),
    )
    gates = GateEvaluator(
        storage,
        artifact_verifier=evidence_service._artifacts,  # noqa: SLF001
        clock=lambda: _NOW + timedelta(seconds=2),
    )
    report = gates.evaluate(
        executor_id,
        expected_version=staged.version,
        additional_evidence_bundle_ids=(_evidence_id(),),
    )
    assert tuple(result.gate for result in report.results) == REQUIRED_GATES
    assert all(result.status is GateStatus.PASSED for result in report.results)
    assert report.promotion_allowed is True
    ready = _candidate(storage, executor_id)
    assert ready.lifecycle is CandidateLifecycle.READY

    with storage.unit_of_work() as unit_of_work:
        EvolutionPolicyRepository().upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=ready.subject_key,
                kind=CandidateKind.EXECUTOR,
                mode="canary",
                max_canary_basis_points=500,
                version=1,
                updated_at=_NOW,
            ),
            expected_version=None,
        )
        unit_of_work.commit()

    skill_base: dict[str, JsonValue] = {"marker": "skill champion"}
    skill_challenger: dict[str, JsonValue] = {"marker": "skill challenger"}
    skill = _seed_canary(
        storage,
        kind=CandidateKind.SKILL,
        subject_key=_SKILL_SUBJECT,
        base_payload=skill_base,
        candidate_payload=skill_challenger,
        allocation=1_000,
        seed_id=_SKILL_SEED,
    )
    skill_payloads = {
        canonical_sha256(skill_base): skill_base,
        canonical_sha256(skill_challenger): skill_challenger,
    }
    assert skill.lifecycle is CandidateLifecycle.CANARY

    now = [_NOW + timedelta(seconds=3)]
    executor_adapter = ExecutorPromotionAdapter(
        artifacts,
        controller,
        storage.unit_of_work,
        clock=lambda: now[0],
    )
    promotion = PromotionService(
        storage,
        gates,
        adapter_resolver=lambda kind: (
            executor_adapter
            if kind is CandidateKind.EXECUTOR
            else pytest.fail("unexpected promotion adapter kind")
        ),
        clock=lambda: now[0],
    )
    operator = _operator(ready.provenance.actor_principal_id)
    start = await promotion.start_canary_async(
        executor_id,
        StartCanaryCommand(
            expected_version=ready.version,
            idempotency_key="executor-demo-start-canary",
            reason="route reviewed executor challenger",
            allocation_basis_points=500,
            allocation_seed_id=_EXECUTOR_SEED,
        ),
        auth=operator,
    )
    assert start.lifecycle is CandidateLifecycle.CANARY
    with storage.unit_of_work() as unit_of_work:
        authority = authority_repository.get_current(
            unit_of_work.connection,
            candidate_id=executor_id,
        )
        unit_of_work.commit()
    assert authority is not None
    target_generation_id = authority.generation_id
    assert _pointer(storage).active_generation_id == base_generation_id
    assert _generation(storage, target_generation_id).state is RuntimeGenerationState.READY
    assert _candidate(storage, skill.candidate_id) == skill

    def resolve_payload(connection, selected_ref, overlay):  # type: ignore[no-untyped-def]
        if overlay.kind is CandidateKind.SKILL:
            return skill_payloads[selected_ref.artifact_digest]
        return candidates.resolve_effective_payload_current(connection, selected_ref, overlay)

    def bucket(memorial_id: str, seed_id: str, _secret: bytes) -> int:
        if seed_id == _EXECUTOR_SEED and memorial_id == "root-champion":
            return 999
        return 0

    router = ChallengerRouter(
        storage,
        allocation_secret=b"executor-evolution-demo",
        bucket_calculator=bucket,
        payload_resolver=resolve_payload,
        snapshot_resolver=_snapshot_resolver,
        generation_controller=lambda: controller,
        executor_generation_authority_resolver=lambda: authority_repository,
        clock=lambda: now[0],
    )
    for memorial_id in ("root-champion", "root-challenger"):
        _seed_memorial(storage, memorial_id)
        router.assign(memorial_id)

    champion_set = _assignment_set(storage, "root-champion")
    challenger_set = _assignment_set(storage, "root-challenger")
    champion_by_subject = {item.subject_key: item for item in champion_set.assignments}
    challenger_by_subject = {item.subject_key: item for item in challenger_set.assignments}
    assert (
        set(champion_by_subject)
        == set(challenger_by_subject)
        == {
            _SCOPE,
            _SKILL_SUBJECT,
        }
    )
    assert champion_by_subject[_SCOPE].selected_ref == ready.base
    assert challenger_by_subject[_SCOPE].selected_ref == ready.candidate
    assert champion_by_subject[_SKILL_SUBJECT].selected_ref == skill.candidate
    assert challenger_by_subject[_SKILL_SUBJECT].selected_ref == skill.candidate

    with router.bind_runtime("root-champion", attempt_id="attempt-champion"):
        champion_binding = current_run_binding()
        assert champion_binding is not None
        assert champion_binding.generation_ids == (base_generation_id,)
        assert _generation(storage, base_generation_id).state is RuntimeGenerationState.ACTIVE
    assert controller.release_binding("attempt-champion") is True
    with router.bind_runtime("root-challenger", attempt_id="attempt-challenger"):
        challenger_binding = current_run_binding()
        assert challenger_binding is not None
        assert challenger_binding.generation_ids == (target_generation_id,)
        assert _generation(storage, target_generation_id).state is RuntimeGenerationState.READY
    assert controller.release_binding("attempt-challenger") is True

    with pytest.raises(PromotionConflict, match="^promotion_decision_required$"):
        promotion.promote(
            executor_id,
            PromoteCommand(
                expected_version=start.candidate_version,
                idempotency_key="executor-demo-promote-without-decision",
                reason="must not promote without high-risk approval",
            ),
            auth=operator,
        )
    assert _pointer(storage).active_generation_id == base_generation_id

    now[0] += timedelta(seconds=1)
    decision_id = _resolved_promotion_decision(
        storage,
        candidate_id=executor_id,
        operator=operator,
        now=now,
    )
    now[0] += timedelta(seconds=1)
    promoted = promotion.promote(
        executor_id,
        PromoteCommand(
            expected_version=start.candidate_version,
            idempotency_key="executor-demo-promote-approved",
            reason="activate approved executor generation",
            decision_request_id=decision_id,
        ),
        auth=operator,
    )
    promoted_pointer = _pointer(storage)
    assert promoted.lifecycle is CandidateLifecycle.PROMOTED
    assert promoted_pointer.active_generation_id == target_generation_id
    assert promoted_pointer.last_good_generation_id == base_generation_id
    assert _generation(storage, target_generation_id).state is RuntimeGenerationState.ACTIVE
    assert _generation(storage, base_generation_id).state is RuntimeGenerationState.DRAINING
    assert _candidate(storage, skill.candidate_id) == skill
    assert _assignment_set(storage, "root-champion") == champion_set
    assert _assignment_set(storage, "root-challenger") == challenger_set

    now[0] += timedelta(seconds=1)
    rollback_started = monotonic()
    rolled_back = promotion.rollback(
        executor_id,
        RollbackCommand(
            expected_version=promoted.candidate_version,
            idempotency_key="executor-demo-rollback",
            reason="restore exact last-good executor generation",
        ),
        auth=operator,
    )
    rollback_elapsed = monotonic() - rollback_started
    rollback_contract_seconds = ready.evolution_contract.rollback_slo_seconds
    assert rollback_elapsed <= rollback_contract_seconds
    rolled_back_pointer = _pointer(storage)
    assert rolled_back.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert rolled_back_pointer.active_generation_id == base_generation_id
    assert rolled_back_pointer.last_good_generation_id == base_generation_id
    assert _generation(storage, base_generation_id).state is RuntimeGenerationState.ACTIVE
    assert _generation(storage, target_generation_id).state is RuntimeGenerationState.DRAINING

    _seed_memorial(storage, "root-after-rollback")
    router.assign("root-after-rollback")
    after_set = _assignment_set(storage, "root-after-rollback")
    assert tuple(item.subject_key for item in after_set.assignments) == (_SKILL_SUBJECT,)
    assert after_set.assignments[0].selected_ref == skill.candidate
    with router.bind_runtime("root-after-rollback", attempt_id="attempt-after-rollback"):
        after_binding = current_run_binding()
        assert after_binding is not None
        assert after_binding.generation_ids == (base_generation_id,)
    assert controller.release_binding("attempt-after-rollback") is True
    assert _candidate(storage, skill.candidate_id) == skill
    assert _assignment_set(storage, "root-champion") == champion_set
    assert _assignment_set(storage, "root-challenger") == challenger_set
