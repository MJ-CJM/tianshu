from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError
from tests.evidence._fixtures import seed_closed_run

import tianshu.evolution.promotion as promotion_module
from tianshu.app import create_app, lifespan
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import (
    ActivationReceiptV1,
    AdapterError,
    AdapterOperationUnavailable,
)
from tianshu.evolution.adapters.base import RollbackReceiptV1 as AdapterRollbackReceiptV1
from tianshu.evolution.gates import (
    REQUIRED_GATES,
    EvolutionGateReportV1,
    EvolutionGateResultV1,
    GateStatus,
)
from tianshu.evolution.promotion import (
    PromoteCommand,
    PromotionAuthorizationError,
    PromotionConflict,
    PromotionService,
    RollbackCommand,
    SkillPromotionAdapter,
    StartCanaryCommand,
)
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
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
)
from tianshu.models.evolution_policy import EvolutionPolicyV1
from tianshu.models.frozen_content import FrozenContentViewsV1
from tianshu.models.governance_contract import AcceptanceCheckV1, AcceptancePolicyV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.skills.install_service import EvaluateSkillGateCommand, ProposeSkillCommand
from tianshu.skills.loader import SkillsLoader, SkillsWatcher, bind_frozen_content_views
from tianshu.storage import Storage
from tianshu.storage.decision_repo import DecisionRepository
from tianshu.storage.evolution_policy_repo import EvolutionPolicyRepository
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict

NOW = datetime(2026, 7, 18, 8, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64


def _contract(kind: CandidateKind) -> EvolutionContractV1:
    return EvolutionContractV1(
        kind=kind,
        subject_key=f"{kind.value}:subject",
        governance_contract_hash=DIGEST_A,
        required_gates=tuple(GateName),
        regression_policy_artifact_digest=DIGEST_A,
        sample_policy_artifact_digest=DIGEST_B,
        budget_policy_artifact_digest=DIGEST_C,
        minimum_canary_samples=10,
        max_canary_allocation_basis_points=500,
        rollback_slo_seconds=30,
    )


def _candidate(kind: CandidateKind = CandidateKind.MEMORY) -> EvolutionCandidateV1:
    contract = _contract(kind)
    base = CandidateVersionRefV1(version="1", artifact_digest=DIGEST_A, canonical_digest=DIGEST_A)
    return EvolutionCandidateV1(
        candidate_id=f"candidate-{kind.value}",
        kind=kind,
        subject_key=contract.subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.API,
            source_uri_redacted=None,
            source_digest=DIGEST_B,
            actor_principal_id="principal-1",
            actor_display_name="Operator",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="test",
            producer_version="1",
            received_at=NOW,
        ),
        base=base,
        candidate=CandidateVersionRefV1(
            version="2", artifact_digest=DIGEST_B, canonical_digest=DIGEST_B
        ),
        diff_artifact_digest=DIGEST_C,
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=1,
        evidence_bundle_ids=(),
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="restore-1",
            adapter_name=kind.value,
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _ready(
    storage: Storage,
    kind: CandidateKind = CandidateKind.MEMORY,
    *,
    candidate_id: str | None = None,
) -> EvolutionCandidateV1:
    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        candidate = _candidate(kind)
        if candidate_id is not None:
            candidate = candidate.model_copy(update={"candidate_id": candidate_id})
        policy_repository = EvolutionPolicyRepository()
        if policy_repository.get_policy(unit_of_work.connection, candidate.subject_key) is None:
            policy_repository.upsert_policy(
                unit_of_work.connection,
                EvolutionPolicyV1(
                    subject_key=candidate.subject_key,
                    kind=candidate.kind,
                    mode="canary",
                    max_canary_basis_points=(
                        candidate.evolution_contract.max_canary_allocation_basis_points
                    ),
                    version=1,
                    updated_at=NOW,
                ),
                expected_version=None,
            )
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


def _green(candidate: EvolutionCandidateV1) -> EvolutionGateReportV1:
    return EvolutionGateReportV1.from_results(
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.version,
        candidate_digest=candidate.candidate.artifact_digest,
        gate_snapshot_version=candidate.gate_snapshot_version,
        results=tuple(
            EvolutionGateResultV1(
                gate=gate,
                status=GateStatus.PASSED,
                reason_code="gate_check_passed",
            )
            for gate in GateName
        ),
        evidence_bundle_ids=candidate.evidence_bundle_ids,
        evaluated_at=NOW,
    )


class _GateAuthority:
    def __init__(self, report: EvolutionGateReportV1) -> None:
        self.report = report
        self.failure: Exception | None = None
        self.bound_validations = 0

    def get_current_report_current(
        self, connection: object, candidate_id: str
    ) -> EvolutionGateReportV1:
        del connection
        if self.failure is not None:
            raise self.failure
        assert candidate_id == self.report.candidate_id
        return self.report

    def validate_bound_green_report_current(
        self,
        connection: object,
        candidate_id: str,
        *,
        candidate_version: int,
        gate_snapshot_version: int,
        candidate_digest: str,
        report_hash: str,
    ) -> EvolutionGateReportV1:
        del connection
        self.bound_validations += 1
        if self.failure is not None:
            raise self.failure
        assert (
            candidate_id,
            candidate_version,
            gate_snapshot_version,
            candidate_digest,
            report_hash,
        ) == (
            self.report.candidate_id,
            self.report.candidate_version,
            self.report.gate_snapshot_version,
            self.report.candidate_digest,
            self.report.report_hash,
        )
        return self.report


class _Adapter:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.activate_calls = 0
        self.rollback_calls = 0
        self.fail_rollback = False
        self.saw_zero_before_restore = False

    def validate_canary(
        self,
        candidate: EvolutionCandidateV1,
        *,
        connection: object | None = None,
    ) -> None:
        del candidate, connection

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1:
        self.activate_calls += 1
        return ActivationReceiptV1(
            candidate_id=candidate.candidate_id,
            artifact_digest=candidate.candidate.artifact_digest,
        )

    def rollback(self, candidate: EvolutionCandidateV1) -> AdapterRollbackReceiptV1:
        self.rollback_calls += 1
        with self.storage.unit_of_work() as unit_of_work:
            row = unit_of_work.connection.execute(
                "SELECT allocation_basis_points FROM evolution_routing_allocations WHERE candidate_id=?",
                (candidate.candidate_id,),
            ).fetchone()
            current = EvolutionRepository().get_candidate(
                unit_of_work.connection, candidate.candidate_id
            )
            unit_of_work.commit()
        self.saw_zero_before_restore = bool(
            row is not None
            and row["allocation_basis_points"] == 0
            and current is not None
            and current.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
        )
        if self.fail_rollback:
            raise RuntimeError("sensitive /tmp/provider/path")
        return AdapterRollbackReceiptV1(
            candidate_id=candidate.candidate_id,
            artifact_digest=candidate.base.artifact_digest,
        )


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    active = Storage(str(tmp_path / "promotion.db"))
    active.init_db()
    yield active
    active.close()


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
        correlation_id="promotion-test",
    )


def _auth_for(principal_id: str, *, scopes: frozenset[str]) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=principal_id,
            scopes=scopes,
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id=f"promotion-{principal_id}",
    )


def _durable_mutation_state(
    storage: Storage, candidate_id: str, adapter: _Adapter
) -> tuple[object, ...]:
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        candidate = EvolutionRepository().get_candidate(connection, candidate_id)
        routing = connection.execute(
            "SELECT * FROM evolution_routing_allocations WHERE candidate_id=?",
            (candidate_id,),
        ).fetchall()
        counts = tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "evolution_promotion_journal",
                "system_audit_events",
                "outbox_events",
            )
        )
        unit_of_work.commit()
    return (
        candidate,
        tuple(map(tuple, routing)),
        counts,
        adapter.activate_calls,
        adapter.rollback_calls,
    )


def _service(
    storage: Storage, candidate: EvolutionCandidateV1
) -> tuple[PromotionService, _GateAuthority, _Adapter]:
    gates = _GateAuthority(_green(candidate))
    adapter = _Adapter(storage)
    service = PromotionService(
        storage,
        gates,
        adapter_resolver=lambda _kind: adapter,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    return service, gates, adapter


def _start_command(
    candidate: EvolutionCandidateV1, *, key: str = "start-1", allocation: int = 250
) -> StartCanaryCommand:
    return StartCanaryCommand(
        expected_version=candidate.version,
        idempotency_key=key,
        reason="begin reviewed canary",
        allocation_basis_points=allocation,
        allocation_seed_id="seed-1",
    )


def _add_code_decision(
    storage: Storage,
    candidate: EvolutionCandidateV1,
    auth: AuthContext,
    *,
    decision_id: str,
    payload_updates: dict[str, object] | None = None,
    resolution_action: str | None = "approve",
) -> None:
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        edict_id = f"edict-{decision_id}"
        memorial_id = f"memorial-{decision_id}"
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            (edict_id, "review promotion", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
            (memorial_id, edict_id, "submitted", NOW.isoformat()),
        )
        payload = {
            "schema_version": 1,
            "candidate_id": candidate.candidate_id,
            "candidate_version": candidate.version,
            "candidate_artifact_digest": candidate.candidate.artifact_digest,
            "gate_snapshot_version": candidate.gate_snapshot_version,
            "action": "promote",
            "risk_tier": "high",
        }
        payload.update(payload_updates or {})
        request = DecisionRequestV1(
            decision_request_id=decision_id,
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id=edict_id,
            memorial_id=memorial_id,
            request_key=f"request-{decision_id}",
            payload=payload,
            payload_hash=canonical_sha256(payload),
            requested_by=auth.principal.id,
            expires_at=NOW + timedelta(hours=1),
            status=DecisionStatus.PENDING,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        decisions = DecisionRepository()
        decisions.add_or_get(connection, request)
        if resolution_action is not None:
            decisions.resolve(
                connection,
                DecisionResolutionV1(
                    decision_request_id=decision_id,
                    action=resolution_action,
                    reason="reviewed high risk code",
                    payload={"schema_version": 1},
                    actor_principal_id="reviewer-1",
                    actor_display_name="Reviewer",
                    resolved_at=NOW + timedelta(minutes=1),
                ),
                expected_version=1,
                now=NOW + timedelta(minutes=1),
            )
        unit_of_work.commit()


def _skill_adapter_case(
    storage: Storage, tmp_path: Path
) -> tuple[ArtifactStore, Path, Path, EvolutionCandidateV1, str, str]:
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
        clock=lambda: NOW,
    )
    live_root = tmp_path / "skills"
    skill_root = live_root / "review-helper"
    skill_root.mkdir(parents=True)
    base_text = "---\nname: review-helper\ndescription: safe base\n---\nbase body"
    candidate_text = "---\nname: review-helper\ndescription: safe candidate\n---\ncandidate body"
    (skill_root / "SKILL.md").write_text(base_text, encoding="utf-8")
    base_payload = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [{"path": "SKILL.md", "kind": "file", "content": base_text}],
    }
    candidate_payload = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [{"path": "SKILL.md", "kind": "file", "content": candidate_text}],
    }
    base_artifact = artifacts.put_bytes(
        canonical_json_bytes(base_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    candidate_artifact = artifacts.put_bytes(
        canonical_json_bytes(candidate_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    envelope = _candidate(CandidateKind.SKILL)
    skill_contract = envelope.evolution_contract.model_copy(
        update={"subject_key": "skill:review-helper"}
    )
    base_ref = CandidateVersionRefV1(
        version="1",
        artifact_digest=base_artifact.digest,
        canonical_digest=canonical_sha256(base_payload),
    )
    candidate_ref = CandidateVersionRefV1(
        version="2",
        artifact_digest=candidate_artifact.digest,
        canonical_digest=canonical_sha256(candidate_payload),
    )
    envelope = envelope.model_copy(
        update={
            "subject_key": "skill:review-helper",
            "base": base_ref,
            "candidate": candidate_ref,
            "evolution_contract": skill_contract,
            "evolution_contract_hash": canonical_sha256(skill_contract),
            "rollback": envelope.rollback.model_copy(update={"champion_ref": base_ref}),
            "lifecycle": CandidateLifecycle.CANARY,
        }
    )
    return artifacts, live_root, skill_root, envelope, base_text, candidate_text


def _promotion_remnants(live_root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in live_root.glob(".promotion-*")))


def test_absent_skill_candidate_is_rejected_before_canary_or_activation(
    storage: Storage,
    tmp_path: Path,
    auth: AuthContext,
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, _candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    absent_payload = {
        "name": "review-helper",
        "state": "absent",
        "trust_source": "workspace",
        "members": [],
    }
    absent_artifact = artifacts.put_bytes(
        canonical_json_bytes(absent_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    absent_ref = CandidateVersionRefV1(
        version="absent",
        artifact_digest=absent_artifact.digest,
        canonical_digest=canonical_sha256(absent_payload),
    )
    absent = envelope.model_copy(update={"candidate": absent_ref})
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)

    with pytest.raises(
        AdapterOperationUnavailable,
        match="^skill_absence_requires_durable_tombstone$",
    ):
        adapter.activate(absent)
    assert skill_root.joinpath("SKILL.md").read_text(encoding="utf-8") == base_text

    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        current = repository.insert_candidate(
            unit_of_work.connection,
            absent.model_copy(
                update={
                    "lifecycle": CandidateLifecycle.PROPOSED,
                    "routing": None,
                }
            ),
        )
        for lifecycle in (
            CandidateLifecycle.STAGED,
            CandidateLifecycle.EVALUATING,
            CandidateLifecycle.READY,
        ):
            current = repository.save_candidate(
                unit_of_work.connection,
                current.model_copy(update={"lifecycle": lifecycle, "updated_at": NOW}),
                expected_version=current.version,
            )
        EvolutionPolicyRepository().upsert_policy(
            unit_of_work.connection,
            EvolutionPolicyV1(
                subject_key=current.subject_key,
                kind=CandidateKind.SKILL,
                mode="canary",
                max_canary_basis_points=500,
                version=1,
                updated_at=NOW,
            ),
            expected_version=None,
        )
        unit_of_work.commit()
    service = PromotionService(
        storage,
        _GateAuthority(_green(current)),
        adapter_resolver=lambda _kind: adapter,
        clock=lambda: NOW + timedelta(minutes=1),
    )
    command = _start_command(current, key="absent-canary")

    for _replay in range(2):
        with pytest.raises(
            PromotionConflict,
            match="^skill_absence_requires_durable_tombstone$",
        ):
            service.start_canary(current.candidate_id, command, auth=auth)

    assert skill_root.joinpath("SKILL.md").read_text(encoding="utf-8") == base_text
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM evolution_promotion_journal"
        ).fetchone()[0]
        == 0
    )


def test_skill_canary_requires_adapter_preflight_capability(
    storage: Storage,
    auth: AuthContext,
) -> None:
    candidate = _ready(storage, CandidateKind.SKILL)
    service = PromotionService(
        storage,
        _GateAuthority(_green(candidate)),
        adapter_resolver=lambda _kind: object(),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    command = _start_command(candidate, key="missing-skill-preflight")

    for _replay in range(2):
        with pytest.raises(PromotionConflict, match="^skill_canary_validation_failed$"):
            service.start_canary(candidate.candidate_id, command, auth=auth)

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection,
            candidate.candidate_id,
        )
        routing_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        journal_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_promotion_journal"
        ).fetchone()[0]
        unit_of_work.commit()

    assert current == candidate
    assert routing_count == 0
    assert journal_count == 0


def test_candidate_owner_authorization_precedes_every_mutation_and_idempotent_replay(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    intruder = _auth_for("principal-2", scopes=frozenset({"api"}))

    start = _start_command(candidate)
    before = _durable_mutation_state(storage, candidate.candidate_id, adapter)
    with pytest.raises(PromotionAuthorizationError, match="promotion_candidate_owner_required"):
        service.start_canary(candidate.candidate_id, start, auth=intruder)
    assert _durable_mutation_state(storage, candidate.candidate_id, adapter) == before

    canary = service.start_canary(candidate.candidate_id, start, auth=auth)
    before = _durable_mutation_state(storage, candidate.candidate_id, adapter)
    with pytest.raises(PromotionAuthorizationError, match="promotion_candidate_owner_required"):
        service.start_canary(candidate.candidate_id, start, auth=intruder)
    assert _durable_mutation_state(storage, candidate.candidate_id, adapter) == before

    promote = PromoteCommand(
        expected_version=canary.candidate_version,
        idempotency_key="owner-promote",
        reason="owner reviewed canary",
    )
    promoted = service.promote(candidate.candidate_id, promote, auth=auth)
    assert promoted.lifecycle is CandidateLifecycle.PROMOTED
    before = _durable_mutation_state(storage, candidate.candidate_id, adapter)
    with pytest.raises(PromotionAuthorizationError, match="promotion_candidate_owner_required"):
        service.promote(candidate.candidate_id, promote, auth=intruder)
    assert _durable_mutation_state(storage, candidate.candidate_id, adapter) == before

    rollback_candidate = _ready(storage, candidate_id="candidate-memory-rollback-owner")
    rollback_service, _rollback_gates, rollback_adapter = _service(storage, rollback_candidate)
    rollback_canary = rollback_service.start_canary(
        rollback_candidate.candidate_id,
        _start_command(rollback_candidate, key="owner-rollback-start"),
        auth=auth,
    )
    rollback = RollbackCommand(
        expected_version=rollback_canary.candidate_version,
        idempotency_key="owner-rollback",
        reason="owner requested rollback",
    )
    rollback_service.rollback(rollback_candidate.candidate_id, rollback, auth=auth)
    before = _durable_mutation_state(storage, rollback_candidate.candidate_id, rollback_adapter)
    with pytest.raises(PromotionAuthorizationError, match="promotion_candidate_owner_required"):
        rollback_service.rollback(rollback_candidate.candidate_id, rollback, auth=intruder)
    assert (
        _durable_mutation_state(storage, rollback_candidate.candidate_id, rollback_adapter)
        == before
    )


def test_admin_may_start_promote_and_rollback_another_principals_candidate(
    storage: Storage,
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    admin = _auth_for("principal-admin", scopes=frozenset({"admin"}))

    canary = service.start_canary(
        candidate.candidate_id,
        _start_command(candidate, key="admin-start"),
        auth=admin,
    )
    promoted = service.promote(
        candidate.candidate_id,
        PromoteCommand(
            expected_version=canary.candidate_version,
            idempotency_key="admin-promote",
            reason="admin reviewed canary",
        ),
        auth=admin,
    )
    assert promoted.lifecycle is CandidateLifecycle.PROMOTED
    rollback_candidate = _ready(storage, candidate_id="candidate-memory-admin-rollback")
    rollback_service, _rollback_gates, rollback_adapter = _service(storage, rollback_candidate)
    rollback_canary = rollback_service.start_canary(
        rollback_candidate.candidate_id,
        _start_command(rollback_candidate, key="admin-rollback-start"),
        auth=admin,
    )
    rolled_back = rollback_service.rollback(
        rollback_candidate.candidate_id,
        RollbackCommand(
            expected_version=rollback_canary.candidate_version,
            idempotency_key="admin-rollback",
            reason="admin requested rollback",
        ),
        auth=admin,
    )

    assert rolled_back.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert adapter.activate_calls == 1
    assert rollback_adapter.rollback_calls == 1


def test_commands_are_strict_and_require_reason_version_and_idempotency() -> None:
    with pytest.raises(ValidationError):
        StartCanaryCommand.model_validate(
            {
                "expected_version": 1,
                "idempotency_key": "key",
                "reason": "reason",
                "allocation_basis_points": 100,
                "allocation_seed_id": "seed",
                "unexpected": True,
            }
        )
    for model in (PromoteCommand, RollbackCommand):
        with pytest.raises(ValidationError):
            model(expected_version=1, idempotency_key="key", reason=" ")


def test_start_canary_fails_closed_for_stale_gate_and_contract_overallocation(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage)
    service, gates, _adapter = _service(storage, candidate)
    gates.failure = EvolutionRepositoryConflict("current gate snapshot evidence is no longer valid")

    with pytest.raises(PromotionConflict, match="gate_snapshot_conflict"):
        service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    gates.failure = None
    with pytest.raises(PromotionConflict, match="allocation_exceeds_contract"):
        service.start_canary(
            candidate.candidate_id,
            _start_command(candidate, key="start-over", allocation=501),
            auth=auth,
        )

    with storage.unit_of_work() as unit_of_work:
        counts = tuple(
            unit_of_work.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "evolution_promotion_journal",
                "evolution_routing_allocations",
                "system_audit_events",
                "outbox_events",
            )
        )
        unit_of_work.commit()
    assert counts == (0, 0, 0, 0)


def test_start_canary_is_atomic_and_idempotent(
    storage: Storage, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _ready(storage)
    service, _gates, _adapter = _service(storage, candidate)

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(service._outbox, "add", fail_outbox)
    with pytest.raises(RuntimeError, match="injected outbox failure"):
        service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    monkeypatch.undo()

    first = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    repeated = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    assert repeated == first
    assert first.lifecycle is CandidateLifecycle.CANARY
    assert first.gate_report_hash == _green(candidate).report_hash

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        counts = tuple(
            unit_of_work.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "evolution_promotion_journal",
                "evolution_routing_allocations",
                "system_audit_events",
                "outbox_events",
            )
        )
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.CANARY
    assert counts == (1, 1, 1, 1)


def test_promote_revalidates_bound_gate_and_rejects_duplicate_or_stale_command(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage)
    service, gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = PromoteCommand(
        expected_version=canary.candidate_version,
        idempotency_key="promote-1",
        reason="promote reviewed canary",
    )
    receipt = service.promote(candidate.candidate_id, command, auth=auth)

    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert gates.bound_validations == 2
    assert adapter.activate_calls == 1
    assert service.promote(candidate.candidate_id, command, auth=auth) == receipt
    assert adapter.activate_calls == 1
    with pytest.raises(PromotionConflict, match="promotion_preconditions_not_met"):
        service.promote(
            candidate.candidate_id,
            command.model_copy(
                update={
                    "idempotency_key": "promote-duplicate",
                    "expected_version": receipt.candidate_version,
                }
            ),
            auth=auth,
        )


def test_code_never_promotes_without_explicit_current_decision(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage, CandidateKind.CODE)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)

    with pytest.raises(PromotionConflict, match="promotion_decision_required"):
        service.promote(
            candidate.candidate_id,
            PromoteCommand(
                expected_version=canary.candidate_version,
                idempotency_key="code-promote",
                reason="reviewed code candidate",
            ),
            auth=auth,
        )
    assert adapter.activate_calls == 0


@pytest.mark.parametrize(
    ("case", "payload_updates", "resolution_action"),
    (
        ("nonexistent", None, None),
        ("pending", None, None),
        ("rejected", None, "reject"),
        ("wrong-version", {"candidate_version": 999}, "approve"),
        ("wrong-digest", {"candidate_artifact_digest": DIGEST_C}, "approve"),
        ("wrong-snapshot", {"gate_snapshot_version": 999}, "approve"),
    ),
)
def test_code_decision_is_durably_preflighted_before_journal_or_effect(
    storage: Storage,
    auth: AuthContext,
    case: str,
    payload_updates: dict[str, object] | None,
    resolution_action: str | None,
) -> None:
    candidate = _ready(storage, CandidateKind.CODE)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    decision_id = f"decision-{case}"
    if case != "nonexistent":
        current = candidate.model_copy(
            update={"version": canary.candidate_version, "lifecycle": CandidateLifecycle.CANARY}
        )
        _add_code_decision(
            storage,
            current,
            auth,
            decision_id=decision_id,
            payload_updates=payload_updates,
            resolution_action=resolution_action,
        )

    with pytest.raises(PromotionConflict, match="^promotion_decision_required$"):
        service.promote(
            candidate.candidate_id,
            PromoteCommand(
                expected_version=canary.candidate_version,
                idempotency_key=f"promote-{case}",
                reason="reviewed code candidate",
                decision_request_id=decision_id,
            ),
            auth=auth,
        )

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        promote_journal_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_promotion_journal WHERE action='promote'"
        ).fetchone()[0]
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.CANARY
    assert adapter.activate_calls == 0
    assert promote_journal_count == 0


def test_code_promotes_with_current_resolved_high_risk_decision(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage, CandidateKind.CODE)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        current = EvolutionRepository().get_candidate(connection, candidate.candidate_id)
        assert current is not None
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("edict-promote", "review promotion", NOW.isoformat()),
        )
        connection.execute(
            "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
            ("memorial-promote", "edict-promote", "submitted", NOW.isoformat()),
        )
        payload = {
            "schema_version": 1,
            "candidate_id": current.candidate_id,
            "candidate_version": current.version,
            "candidate_artifact_digest": current.candidate.artifact_digest,
            "gate_snapshot_version": current.gate_snapshot_version,
            "action": "promote",
            "risk_tier": "high",
        }
        request = DecisionRequestV1(
            decision_request_id="decision-promote",
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id="edict-promote",
            memorial_id="memorial-promote",
            request_key="code-promote-current",
            payload=payload,
            payload_hash=canonical_sha256(payload),
            requested_by=auth.principal.id,
            expires_at=NOW + timedelta(hours=1),
            status=DecisionStatus.PENDING,
            version=1,
            created_at=NOW,
            updated_at=NOW,
        )
        decisions = DecisionRepository()
        decisions.add_or_get(connection, request)
        decisions.resolve(
            connection,
            DecisionResolutionV1(
                decision_request_id=request.decision_request_id,
                action="approve",
                reason="reviewed high risk code",
                payload={"schema_version": 1},
                actor_principal_id="reviewer-1",
                actor_display_name="Reviewer",
                resolved_at=NOW + timedelta(minutes=1),
            ),
            expected_version=1,
            now=NOW + timedelta(minutes=1),
        )
        unit_of_work.commit()

    receipt = service.promote(
        candidate.candidate_id,
        PromoteCommand(
            expected_version=canary.candidate_version,
            idempotency_key="code-promote-approved",
            reason="apply approved code",
            decision_request_id="decision-promote",
        ),
        auth=auth,
    )
    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert adapter.activate_calls == 1


def test_rollback_zeroes_allocation_before_restore_and_retries_from_pending(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = RollbackCommand(
        expected_version=canary.candidate_version,
        idempotency_key="rollback-1",
        reason="canary regression",
    )
    adapter.fail_rollback = True
    with pytest.raises(PromotionConflict, match="rollback_restore_failed") as failure:
        service.rollback(candidate.candidate_id, command, auth=auth)
    assert "/tmp" not in str(failure.value)
    assert adapter.saw_zero_before_restore is True

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        allocation = unit_of_work.connection.execute(
            "SELECT allocation_basis_points FROM evolution_routing_allocations WHERE candidate_id=?",
            (candidate.candidate_id,),
        ).fetchone()[0]
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert allocation == 0

    adapter.fail_rollback = False
    receipt = service.rollback(candidate.candidate_id, command, auth=auth)
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert receipt.allocation_basis_points == 0
    assert adapter.rollback_calls == 2
    assert service.rollback(candidate.candidate_id, command, auth=auth) == receipt
    assert adapter.rollback_calls == 2


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("journal_id", "f" * 64),
        ("candidate_id", "cross-bound-candidate"),
        ("action", "start_canary"),
        ("idempotency_key", "different-key"),
        ("effect_artifact_digest", DIGEST_C),
    ),
)
def test_completed_receipt_tamper_is_rejected_as_journal_conflict(
    storage: Storage,
    auth: AuthContext,
    field: str,
    value: str,
) -> None:
    candidate = _ready(storage)
    service, _gates, _adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = PromoteCommand(
        expected_version=canary.candidate_version,
        idempotency_key="tamper-promote",
        reason="reviewed canary",
    )
    service.promote(candidate.candidate_id, command, auth=auth)
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
        row = connection.execute(
            """SELECT promotion_journal_id, entry_json
               FROM evolution_promotion_journal
               WHERE candidate_id=? AND action='promote' AND status='completed'""",
            (candidate.candidate_id,),
        ).fetchone()
        payload = json.loads(row["entry_json"])
        payload["receipt"][field] = value
        tampered = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET entry_json=?, entry_hash=? WHERE promotion_journal_id=?""",
            (tampered, sha256(tampered.encode()).hexdigest(), row["promotion_journal_id"]),
        )
        unit_of_work.commit()

    with pytest.raises(PromotionConflict, match="^promotion_journal_conflict$"):
        service.promote(candidate.candidate_id, command, auth=auth)


@pytest.mark.parametrize(
    ("field", "value"),
    (("principal_id", "different-principal"), ("reason", "different reason")),
)
def test_journal_command_identity_tamper_is_rejected_on_replay(
    storage: Storage,
    auth: AuthContext,
    field: str,
    value: str,
) -> None:
    candidate = _ready(storage)
    service, _gates, _adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = PromoteCommand(
        expected_version=canary.candidate_version,
        idempotency_key="identity-tamper-promote",
        reason="reviewed canary identity",
    )
    service.promote(candidate.candidate_id, command, auth=auth)
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
        row = connection.execute(
            """SELECT promotion_journal_id, entry_json
               FROM evolution_promotion_journal
               WHERE candidate_id=? AND action='promote' AND status='completed'""",
            (candidate.candidate_id,),
        ).fetchone()
        payload = json.loads(row["entry_json"])
        payload[field] = value
        tampered = canonical_json_bytes(payload).decode("utf-8")
        connection.execute(
            """UPDATE evolution_promotion_journal
               SET entry_json=?, entry_hash=? WHERE promotion_journal_id=?""",
            (tampered, sha256(tampered.encode()).hexdigest(), row["promotion_journal_id"]),
        )
        unit_of_work.commit()

    with pytest.raises(PromotionConflict, match="^promotion_journal_conflict$"):
        service.promote(candidate.candidate_id, command, auth=auth)


def test_journal_row_cross_binding_is_rejected_even_when_entry_hash_is_valid(
    storage: Storage, auth: AuthContext
) -> None:
    candidate = _ready(storage)
    other = _ready(storage, candidate_id="candidate-memory-cross-bind")
    service, _gates, _adapter = _service(storage, candidate)
    command = _start_command(candidate, key="cross-bind-start")
    service.start_canary(candidate.candidate_id, command, auth=auth)
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
        connection.execute(
            """UPDATE evolution_promotion_journal SET candidate_id=?
               WHERE candidate_id=? AND action='start_canary' AND status='completed'""",
            (other.candidate_id, candidate.candidate_id),
        )
        unit_of_work.commit()

    with pytest.raises(PromotionConflict, match="^promotion_journal_conflict$"):
        service.start_canary(candidate.candidate_id, command, auth=auth)


def test_real_skill_adapter_applies_and_restores_exact_artifacts(
    storage: Storage, tmp_path: Path
) -> None:
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
        clock=lambda: NOW,
    )
    live_root = tmp_path / "skills"
    skill_root = live_root / "review-helper"
    skill_root.mkdir(parents=True)
    base_text = "---\nname: review-helper\ndescription: safe base\n---\nbase body"
    candidate_text = "---\nname: review-helper\ndescription: safe candidate\n---\ncandidate body"
    (skill_root / "SKILL.md").write_text(base_text, encoding="utf-8")
    base_payload = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [{"path": "SKILL.md", "kind": "file", "content": base_text}],
    }
    candidate_payload = {
        "name": "review-helper",
        "state": "present",
        "trust_source": "workspace",
        "members": [{"path": "SKILL.md", "kind": "file", "content": candidate_text}],
    }
    base_artifact = artifacts.put_bytes(
        canonical_json_bytes(base_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    candidate_artifact = artifacts.put_bytes(
        canonical_json_bytes(candidate_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    envelope = _candidate(CandidateKind.SKILL)
    skill_contract = envelope.evolution_contract.model_copy(
        update={"subject_key": "skill:review-helper"}
    )
    base_ref = CandidateVersionRefV1(
        version="1",
        artifact_digest=base_artifact.digest,
        canonical_digest=canonical_sha256(base_payload),
    )
    candidate_ref = CandidateVersionRefV1(
        version="2",
        artifact_digest=candidate_artifact.digest,
        canonical_digest=canonical_sha256(candidate_payload),
    )
    envelope = envelope.model_copy(
        update={
            "subject_key": "skill:review-helper",
            "base": base_ref,
            "candidate": candidate_ref,
            "evolution_contract": skill_contract,
            "evolution_contract_hash": canonical_sha256(skill_contract),
            "rollback": envelope.rollback.model_copy(update={"champion_ref": base_ref}),
            "lifecycle": CandidateLifecycle.CANARY,
        }
    )
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)

    activation = adapter.activate(envelope)
    activated_inode = (skill_root / "SKILL.md").stat().st_ino
    repeated = adapter.activate(envelope)
    assert activation == repeated
    assert (skill_root / "SKILL.md").stat().st_ino == activated_inode
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text

    rollback_envelope = envelope.model_copy(
        update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING}
    )
    restore = adapter.rollback(rollback_envelope)
    restored_inode = (skill_root / "SKILL.md").stat().st_ino
    repeated_restore = adapter.rollback(rollback_envelope)
    assert restore == repeated_restore
    assert (skill_root / "SKILL.md").stat().st_ino == restored_inode
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == base_text


def test_skill_exchange_capability_failure_leaves_old_tree_untouched(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)

    def unavailable_exchange(_source: Path, _target: Path) -> None:
        raise OSError("injected atomic exchange unavailable")

    monkeypatch.setattr(promotion_module, "_atomic_exchange", unavailable_exchange, raising=False)
    with pytest.raises(AdapterError, match="skill promotion artifact apply failed"):
        adapter.activate(envelope)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == base_text
    assert _promotion_remnants(live_root) == ()

    monkeypatch.undo()
    reopened = SkillPromotionAdapter(artifacts, live_root=live_root)
    reopened.activate(envelope)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    assert _promotion_remnants(live_root) == ()


@pytest.mark.parametrize(
    ("platform", "operation_name", "expected_directory_fd", "expected_flags"),
    [
        ("darwin", "renameatx_np", -2, 0x12),
        ("linux", "renameat2", -100, 0x02),
    ],
)
def test_atomic_exchange_uses_supported_platform_primitive(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    operation_name: str,
    expected_directory_fd: int,
    expected_flags: int,
) -> None:
    calls: list[tuple[object, ...]] = []

    class Operation:
        argtypes: object = None
        restype: object = None

        def __call__(self, *args: object) -> int:
            calls.append(args)
            return 0

    operation = Operation()
    libc = type("Libc", (), {operation_name: operation})()
    monkeypatch.setattr(promotion_module.sys, "platform", platform)
    monkeypatch.setattr(promotion_module.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    promotion_module._atomic_exchange(Path("source"), Path("target"))

    assert calls == [
        (
            expected_directory_fd,
            b"source",
            expected_directory_fd,
            b"target",
            expected_flags,
        )
    ]


def test_atomic_exchange_missing_platform_primitive_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(promotion_module.sys, "platform", "linux")
    monkeypatch.setattr(promotion_module.ctypes, "CDLL", lambda *_args, **_kwargs: object())

    with pytest.raises(OSError, match="atomic directory exchange unavailable"):
        promotion_module._atomic_exchange(Path("source"), Path("target"))


def test_skill_atomic_exchange_ignores_old_install_and_compensation_rename_failures(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, live_root, skill_root, envelope, _base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)
    original_replace = promotion_module.os.replace
    calls = 0

    def fail_install_and_restore(source: Path | str, target: Path | str) -> None:
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError("injected rename failure")
        original_replace(source, target)

    monkeypatch.setattr(promotion_module.os, "replace", fail_install_and_restore)
    adapter.activate(envelope)
    assert calls == 0
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    assert _promotion_remnants(live_root) == ()

    monkeypatch.undo()
    reopened = SkillPromotionAdapter(artifacts, live_root=live_root)
    reopened.activate(envelope)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    assert _promotion_remnants(live_root) == ()


def test_skill_cleanup_failure_keeps_complete_new_tree_and_retry_preserves_inode(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, live_root, skill_root, envelope, _base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)
    original_rmtree = promotion_module.shutil.rmtree
    failed = False

    def fail_stage_cleanup(path: Path | str, *args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed and "promotion-stage-review-helper" in Path(path).name:
            failed = True
            raise OSError("injected stage cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(promotion_module.shutil, "rmtree", fail_stage_cleanup)
    with pytest.raises(AdapterError, match="skill promotion artifact apply failed"):
        adapter.activate(envelope)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    activated_inode = (skill_root / "SKILL.md").stat().st_ino
    assert any("stage" in name for name in _promotion_remnants(live_root))

    monkeypatch.undo()
    reopened = SkillPromotionAdapter(artifacts, live_root=live_root)
    reopened.activate(envelope)
    assert (skill_root / "SKILL.md").stat().st_ino == activated_inode
    assert _promotion_remnants(live_root) == ()


def test_skill_activation_invalidates_cache_before_post_swap_cleanup_failure(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, live_root, skill_root, envelope, _base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    observed_live_content: list[str] = []
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=lambda: observed_live_content.append(
            (skill_root / "SKILL.md").read_text(encoding="utf-8")
        ),
    )
    original_rmtree = promotion_module.shutil.rmtree
    failed = False

    def fail_stage_cleanup(path: Path | str, *args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed and "promotion-stage-review-helper" in Path(path).name:
            failed = True
            raise OSError("injected stage cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(promotion_module.shutil, "rmtree", fail_stage_cleanup)
    with pytest.raises(AdapterError, match="skill promotion artifact apply failed"):
        adapter.activate(envelope)

    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    assert observed_live_content == [candidate_text]


def test_skill_rollback_invalidates_cache_before_post_swap_cleanup_failure(
    storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, _candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    SkillPromotionAdapter(artifacts, live_root=live_root).activate(envelope)
    observed_live_content: list[str] = []
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=lambda: observed_live_content.append(
            (skill_root / "SKILL.md").read_text(encoding="utf-8")
        ),
    )
    original_rmtree = promotion_module.shutil.rmtree
    failed = False

    def fail_stage_cleanup(path: Path | str, *args: object, **kwargs: object) -> None:
        nonlocal failed
        if not failed and "promotion-stage-review-helper" in Path(path).name:
            failed = True
            raise OSError("injected stage cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(promotion_module.shutil, "rmtree", fail_stage_cleanup)
    rollback = envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING})
    with pytest.raises(AdapterError, match="skill rollback verification failed"):
        adapter.rollback(rollback)

    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == base_text
    assert observed_live_content == [base_text]


def test_skill_absent_restore_is_idempotent_after_recovery_state_is_clean(
    storage: Storage, tmp_path: Path
) -> None:
    artifacts, live_root, skill_root, envelope, _base_text, _candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    promotion_module.shutil.rmtree(skill_root)
    absent_payload = {
        "name": "review-helper",
        "state": "absent",
        "trust_source": "workspace",
        "members": [],
    }
    absent_artifact = artifacts.put_bytes(
        canonical_json_bytes(absent_payload),
        media_type="application/vnd.tianshu.evolution.skill+json",
        redaction="governed_candidate",
    )
    absent_ref = CandidateVersionRefV1(
        version="absent",
        artifact_digest=absent_artifact.digest,
        canonical_digest=canonical_sha256(absent_payload),
    )
    envelope = envelope.model_copy(
        update={
            "base": absent_ref,
            "rollback": envelope.rollback.model_copy(update={"champion_ref": absent_ref}),
        }
    )
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)
    adapter.activate(envelope)
    rollback = envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING})
    first = adapter.rollback(rollback)
    second = adapter.rollback(rollback)

    assert first == second
    assert not skill_root.exists()
    assert _promotion_remnants(live_root) == ()


def test_skill_promotion_invalidates_after_successful_activation_and_rollback(
    storage: Storage,
    tmp_path: Path,
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    observed_live_content: list[str] = []
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=lambda: observed_live_content.append(
            (skill_root / "SKILL.md").read_text(encoding="utf-8")
        ),
    )

    adapter.activate(envelope)
    adapter.rollback(envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING}))

    assert observed_live_content == [candidate_text, base_text]


def test_skill_promotion_swallows_and_logs_cache_invalidation_failures(
    storage: Storage,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    invalidation_calls = 0

    def fail_invalidation() -> None:
        nonlocal invalidation_calls
        invalidation_calls += 1
        raise RuntimeError("injected cache invalidation failure")

    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=fail_invalidation,
    )
    caplog.set_level("WARNING", logger="tianshu.evolution.promotion")

    adapter.activate(envelope)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    adapter.rollback(envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING}))

    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == base_text
    assert invalidation_calls == 2
    assert caplog.messages.count("Skill promotion cache invalidation failed") == 2


def test_failed_skill_activation_does_not_invalidate_cache(
    storage: Storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, live_root, _skill_root, envelope, _base_text, _candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    invalidation_calls: list[None] = []
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=lambda: invalidation_calls.append(None),
    )

    def unavailable_exchange(_source: Path, _target: Path) -> None:
        raise OSError("injected atomic exchange unavailable")

    monkeypatch.setattr(promotion_module, "_atomic_exchange", unavailable_exchange)
    with pytest.raises(AdapterError, match="skill promotion artifact apply failed"):
        adapter.activate(envelope)

    assert invalidation_calls == []


def test_skill_activation_retry_invalidates_after_uncertain_successful_exchange(
    storage: Storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, live_root, skill_root, envelope, _base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    builtin_root = tmp_path / "builtin-skills"
    builtin_root.mkdir()
    loader = SkillsLoader(builtin_dir=builtin_root, user_dir=live_root)
    assert loader.get_skill("review-helper")["content"] == "base body"  # type: ignore[index]
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=loader.invalidate_cache,
    )
    adapter._ensure_atomic_exchange()  # noqa: SLF001
    real_exchange = promotion_module._atomic_exchange

    def exchange_then_report_failure(source: Path, target: Path) -> None:
        real_exchange(source, target)
        raise OSError("injected uncertain exchange result")

    monkeypatch.setattr(promotion_module, "_atomic_exchange", exchange_then_report_failure)
    with pytest.raises(AdapterError, match="skill promotion artifact apply failed"):
        adapter.activate(envelope)

    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == candidate_text
    assert loader.get_skill("review-helper")["content"] == "base body"  # type: ignore[index]

    monkeypatch.setattr(promotion_module, "_atomic_exchange", real_exchange)
    adapter.activate(envelope)

    assert loader.get_skill("review-helper")["content"] == "candidate body"  # type: ignore[index]


def test_skill_verify_rollback_invalidates_a_stale_live_cache(
    storage: Storage,
    tmp_path: Path,
) -> None:
    artifacts, live_root, skill_root, envelope, base_text, _candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    builtin_root = tmp_path / "builtin-skills"
    builtin_root.mkdir()
    loader = SkillsLoader(builtin_dir=builtin_root, user_dir=live_root)
    adapter_without_invalidation = SkillPromotionAdapter(artifacts, live_root=live_root)
    adapter_without_invalidation.activate(envelope)
    loader.invalidate_cache()
    assert loader.get_skill("review-helper")["content"] == "candidate body"  # type: ignore[index]

    rollback = envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING})
    adapter_without_invalidation.rollback(rollback)

    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == base_text
    assert loader.get_skill("review-helper")["content"] == "candidate body"  # type: ignore[index]

    verifier = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=loader.invalidate_cache,
    )
    receipt = verifier.verify_rollback(rollback)

    assert receipt is not None
    assert loader.get_skill("review-helper")["content"] == "base body"  # type: ignore[index]


def test_watcher_promotion_and_frozen_run_never_expose_a_mixed_live_view(
    storage: Storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts, live_root, _skill_root, envelope, base_text, candidate_text = _skill_adapter_case(
        storage, tmp_path
    )
    builtin_root = tmp_path / "builtin-skills"
    builtin_root.mkdir()
    loader = SkillsLoader(builtin_dir=builtin_root, user_dir=live_root)
    adapter = SkillPromotionAdapter(
        artifacts,
        live_root=live_root,
        cache_invalidator=loader.invalidate_cache,
    )
    adapter._ensure_atomic_exchange()  # noqa: SLF001 - isolate the real swap barrier
    base_views = FrozenContentViewsV1(skills=loader.freeze_view())
    base_content = base_views.skills.skills["review-helper"].content
    base_body = base_text.rsplit("---\n", maxsplit=1)[-1]
    candidate_body = candidate_text.rsplit("---\n", maxsplit=1)[-1]
    watcher_changes: list[list[str]] = []
    watcher_callback_entered = Event()
    continue_watcher_callback = Event()

    def record_watcher_change(paths: list[str]) -> None:
        watcher_changes.append(paths)
        watcher_callback_entered.set()
        if not continue_watcher_callback.wait(timeout=5):
            raise TimeoutError("test watcher callback barrier timed out")

    watcher = SkillsWatcher(loader, on_change=record_watcher_change)
    watcher.DEBOUNCE_SECONDS = 0
    watcher._running = True  # noqa: SLF001 - drive the debounce boundary directly
    watcher._generation = 1  # noqa: SLF001 - no filesystem observer in this barrier test
    live_skill_path = live_root / "review-helper" / "SKILL.md"
    real_exchange = promotion_module._atomic_exchange

    def assert_bound_content(views: FrozenContentViewsV1, expected: str) -> None:
        with bind_frozen_content_views(views):
            for _ in range(20):
                skill = loader.get_skill("review-helper")
                assert skill is not None
                assert skill["content"] == expected
                assert expected in loader.load_all()

    def flush_watcher() -> None:
        async def flush() -> None:
            watcher._debounced_reload([str(live_skill_path)])  # noqa: SLF001
            task = watcher._debounce_task  # noqa: SLF001
            assert isinstance(task, asyncio.Task)
            await task

        asyncio.run(flush())

    def run_concurrently_during_post_swap(
        operation,
        current_views: FrozenContentViewsV1,
        expected_current: str,
        expected_new: str,
    ) -> FrozenContentViewsV1:
        swap_completed = Event()
        continue_promotion = Event()
        watcher_callback_entered.clear()
        continue_watcher_callback.clear()

        def blocked_exchange(source: Path, target: Path) -> None:
            real_exchange(source, target)
            swap_completed.set()
            if not continue_promotion.wait(timeout=5):
                raise TimeoutError("test promotion barrier timed out")

        monkeypatch.setattr(promotion_module, "_atomic_exchange", blocked_exchange)
        with ThreadPoolExecutor(max_workers=3) as executor:
            promotion_future = executor.submit(operation)
            assert swap_completed.wait(timeout=5)
            watcher_future = executor.submit(flush_watcher)
            assert watcher_callback_entered.wait(timeout=5)
            freeze_future = executor.submit(
                lambda: FrozenContentViewsV1(skills=loader.freeze_view())
            )
            try:
                assert_bound_content(current_views, expected_current)
                new_views = freeze_future.result(timeout=5)
                assert_bound_content(new_views, expected_new)
            finally:
                continue_watcher_callback.set()
                continue_promotion.set()
            watcher_future.result(timeout=5)
            promotion_future.result(timeout=5)
        monkeypatch.setattr(promotion_module, "_atomic_exchange", real_exchange)
        assert_bound_content(current_views, expected_current)
        return new_views

    candidate_views = run_concurrently_during_post_swap(
        lambda: adapter.activate(envelope),
        base_views,
        base_content,
        candidate_body,
    )
    candidate_content = candidate_views.skills.skills["review-helper"].content

    assert base_views.skills.source_digest != candidate_views.skills.source_digest
    assert base_content == base_body
    assert candidate_content == candidate_body
    assert_bound_content(base_views, base_content)
    assert_bound_content(candidate_views, candidate_content)

    rollback = envelope.model_copy(update={"lifecycle": CandidateLifecycle.ROLLBACK_PENDING})
    restored_views = run_concurrently_during_post_swap(
        lambda: adapter.rollback(rollback),
        candidate_views,
        candidate_content,
        base_body,
    )

    assert_bound_content(candidate_views, candidate_content)
    assert_bound_content(restored_views, base_content)
    assert restored_views.skills.source_digest == base_views.skills.source_digest
    assert watcher_changes == [
        [str(live_root / "review-helper" / "SKILL.md")],
        [str(live_root / "review-helper" / "SKILL.md")],
    ]
    watcher.stop()


def test_promote_retry_after_final_outbox_failure_reuses_applied_receipt(
    storage: Storage, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = PromoteCommand(
        expected_version=canary.candidate_version,
        idempotency_key="promote-crash",
        reason="fault injection",
    )

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected final outbox failure")

    monkeypatch.setattr(service._outbox, "add", fail_outbox)
    with pytest.raises(RuntimeError, match="injected final outbox failure"):
        service.promote(candidate.candidate_id, command, auth=auth)
    assert adapter.activate_calls == 1
    monkeypatch.undo()

    receipt = service.promote(candidate.candidate_id, command, auth=auth)
    assert receipt.lifecycle is CandidateLifecycle.PROMOTED
    assert adapter.activate_calls == 1


def test_rollback_retry_after_final_outbox_failure_reuses_applied_receipt(
    storage: Storage, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=auth)
    command = RollbackCommand(
        expected_version=canary.candidate_version,
        idempotency_key="rollback-crash",
        reason="fault injection",
    )
    original_add = service._outbox.add
    calls = 0

    def fail_second_outbox(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected final outbox failure")
        original_add(*args, **kwargs)

    monkeypatch.setattr(service._outbox, "add", fail_second_outbox)
    with pytest.raises(RuntimeError, match="injected final outbox failure"):
        service.rollback(candidate.candidate_id, command, auth=auth)
    assert adapter.rollback_calls == 1
    monkeypatch.undo()

    receipt = service.rollback(candidate.candidate_id, command, auth=auth)
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert adapter.rollback_calls == 1


async def test_create_app_real_skill_candidate_promotes_and_rolls_back_live_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auth: AuthContext
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TIANSHU_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app()
    async with lifespan(app):
        name = "promotion-golden-demo"
        candidate_text = (
            "---\nname: promotion-golden-demo\ndescription: governed demo\n---\ncandidate body"
        )
        proposed = app.state.skill_install_service.propose(
            ProposeSkillCommand(
                command_id="golden-demo-proposal",
                name=name,
                version="candidate-v1",
                base_version="absent-v1",
                base_state="absent",
                source_channel=CandidateSourceChannel.API,
                base_members=(),
                members=({"path": "SKILL.md", "kind": "file", "content": candidate_text},),
                evidence_bundle_ids=(),
                restore_point_ref="absent-skill",
            ),
            auth=auth,
        )
        staged = app.state.skill_install_service.stage(proposed.candidate_id, auth=auth).candidate
        checks = [
            AcceptanceCheckV1(name=f"evolution.gate.{gate.value}", command="true")
            for gate in REQUIRED_GATES
            if gate is not GateName.EVIDENCE
        ]
        checks.append(
            AcceptanceCheckV1(
                name=(
                    f"evolution.candidate.{staged.candidate_id}."
                    f"{staged.version}.{staged.candidate.artifact_digest}"
                ),
                command="true",
            )
        )
        _edict, memorial = seed_closed_run(
            app.state.storage,
            acceptance=AcceptancePolicyV1(checks=tuple(checks)),
            submitter=auth.principal.id,
        )
        for check in checks:
            app.state.storage.append_event(
                memorial.edict_id,
                memorial.id,
                "acceptance.check.completed",
                {
                    "name": check.name,
                    "status": "passed",
                    "exit_code": 0,
                    "started_at": datetime.now(UTC).isoformat(),
                    "completed_at": datetime.now(UTC).isoformat(),
                },
            )
        app.state.evidence_service.build_open(memorial.id)
        evidence_bundle = app.state.evidence_service.close(memorial.id, expected_version=1)
        gate_report = app.state.skill_install_service.evaluate_gate(
            staged.candidate_id,
            EvaluateSkillGateCommand(
                expected_version=staged.version,
                evidence_bundle_ids=(evidence_bundle.bundle_id,),
            ),
            auth=auth,
        )
        assert gate_report.promotion_allowed is True
        ready = app.state.evolution_gate_evaluator.get_candidate(staged.candidate_id)
        assert ready is not None and ready.lifecycle is CandidateLifecycle.READY

        service: PromotionService = app.state.promotion_service
        canary = service.start_canary(
            ready.candidate_id,
            StartCanaryCommand(
                expected_version=ready.version,
                idempotency_key="golden-canary",
                reason="golden demo canary",
                allocation_basis_points=100,
                allocation_seed_id="golden-seed",
            ),
            auth=auth,
        )
        promoted = service.promote(
            ready.candidate_id,
            PromoteCommand(
                expected_version=canary.candidate_version,
                idempotency_key="golden-promote",
                reason="golden demo promote",
            ),
            auth=auth,
        )
        live_skill = tmp_path / "home/.tianshu/skills" / name / "SKILL.md"
        assert live_skill.read_text(encoding="utf-8") == candidate_text

        rolled_back = service.rollback(
            ready.candidate_id,
            RollbackCommand(
                expected_version=promoted.candidate_version,
                idempotency_key="golden-rollback",
                reason="golden demo rollback",
            ),
            auth=auth,
        )
        assert rolled_back.lifecycle is CandidateLifecycle.ROLLED_BACK
        assert not live_skill.parent.exists()
