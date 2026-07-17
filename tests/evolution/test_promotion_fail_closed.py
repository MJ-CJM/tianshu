from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.evidence._fixtures import seed_closed_run

from tianshu.app import create_app, lifespan
from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import ActivationReceiptV1
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
from tianshu.models.governance_contract import AcceptanceCheckV1, AcceptancePolicyV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.skills.install_service import ProposeSkillCommand
from tianshu.storage import Storage
from tianshu.storage.decision_repo import DecisionRepository
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
        evidence_id = f"evidence:{sha256(b'memorial-evidence').hexdigest()[:32]}"
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
                evidence_bundle_ids=(evidence_id,),
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
        app.state.evidence_service.close(memorial.id, expected_version=1)
        gate_report = app.state.evolution_gate_evaluator.evaluate(
            staged.candidate_id,
            expected_version=staged.version,
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
