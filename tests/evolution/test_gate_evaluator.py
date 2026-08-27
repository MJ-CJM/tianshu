"""Fail-closed contracts for governed evolution gate evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from tests.evidence._fixtures import seed_closed_run

from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.evolution.gates import (
    REQUIRED_GATES,
    EvolutionGateReportV1,
    GateEvaluator,
    GateStatus,
)
from tianshu.executor.capabilities import get_executor_manifest
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
)
from tianshu.models.governance_contract import AcceptanceCheckV1, AcceptancePolicyV1
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict
from tianshu.storage.facade import Storage

NOW = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    active = Storage(str(tmp_path / "tianshu.db"))
    active.init_db()
    yield active
    active.close()


def _staged_candidate(
    storage: Storage,
    *,
    evidence_bundle_ids: tuple[str, ...] = (),
    updated_at: datetime = NOW,
) -> EvolutionCandidateV1:
    contract = EvolutionContractV1(
        kind=CandidateKind.SKILL,
        subject_key="skill:review-helper",
        governance_contract_hash="1" * 64,
        required_gates=REQUIRED_GATES,
        regression_policy_artifact_digest="2" * 64,
        sample_policy_artifact_digest="3" * 64,
        budget_policy_artifact_digest="4" * 64,
        minimum_canary_samples=10,
        max_canary_allocation_basis_points=500,
        rollback_slo_seconds=30,
    )
    base = CandidateVersionRefV1(
        version="champion-v1", artifact_digest="5" * 64, canonical_digest="6" * 64
    )
    candidate = EvolutionCandidateV1(
        candidate_id="candidate-gate-1",
        kind=CandidateKind.SKILL,
        subject_key=contract.subject_key,
        provenance=EvolutionProvenanceV1(
            source_channel=CandidateSourceChannel.API,
            source_uri_redacted=None,
            source_digest="7" * 64,
            actor_principal_id="principal-1",
            actor_display_name="Reviewer",
            originating_edict_id=None,
            originating_memorial_id=None,
            producer_name="gate-test",
            producer_version="1",
            received_at=NOW,
        ),
        base=base,
        candidate=CandidateVersionRefV1(
            version="candidate-v1", artifact_digest="7" * 64, canonical_digest="8" * 64
        ),
        diff_artifact_digest="9" * 64,
        evolution_contract=contract,
        evolution_contract_hash=canonical_sha256(contract),
        gate_snapshot_version=0,
        evidence_bundle_ids=evidence_bundle_ids,
        routing=None,
        rollback=RollbackSpecV1(
            champion_ref=base,
            restore_point_ref="restore-1",
            adapter_name="skill",
            max_seconds=30,
        ),
        lifecycle=CandidateLifecycle.PROPOSED,
        version=1,
        created_at=NOW,
        updated_at=updated_at,
    )
    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        proposed = repository.insert_candidate(unit_of_work.connection, candidate)
        staged = repository.save_candidate(
            unit_of_work.connection,
            proposed.model_copy(update={"lifecycle": CandidateLifecycle.STAGED}),
            expected_version=proposed.version,
        )
        unit_of_work.commit()
    return staged


def test_required_gate_set_is_complete_and_canonical() -> None:
    assert tuple(GateName) == REQUIRED_GATES


def test_gate_report_never_allows_a_missing_required_gate() -> None:
    report = EvolutionGateReportV1.from_results(
        candidate_id="candidate-1",
        candidate_version=2,
        candidate_digest="a" * 64,
        gate_snapshot_version=1,
        results=(),
        evidence_bundle_ids=(),
        evaluated_at=NOW,
    )

    assert report.promotion_allowed is False
    assert report.blocking_gates == REQUIRED_GATES
    assert all(result.status is GateStatus.MISSING for result in report.results)
    assert all(result.reason_code == "evidence_missing" for result in report.results)


def test_evaluate_missing_evidence_blocks_atomically_without_champion_mutation(
    storage: Storage,
) -> None:
    candidate = _staged_candidate(storage)
    evaluator = GateEvaluator(storage, clock=lambda: NOW)

    report = evaluator.evaluate(candidate.candidate_id, expected_version=candidate.version)

    assert report.promotion_allowed is False
    assert report.blocking_gates == REQUIRED_GATES
    assert report.evidence_bundle_ids == ()
    with storage.unit_of_work() as unit_of_work:
        durable = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        snapshot = unit_of_work.connection.execute(
            "SELECT snapshot_json, snapshot_hash FROM evolution_gate_snapshots"
        ).fetchone()
        allocation_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_routing_allocations"
        ).fetchone()[0]
        unit_of_work.commit()
    assert durable is not None
    assert durable.lifecycle is CandidateLifecycle.BLOCKED
    assert durable.base == candidate.base
    assert durable.routing is None
    assert durable.gate_snapshot_version == 1
    assert snapshot is not None
    assert allocation_count == 0


def test_evaluate_rejects_stale_expected_candidate_version(storage: Storage) -> None:
    candidate = _staged_candidate(storage)
    evaluator = GateEvaluator(storage, clock=lambda: NOW)

    with pytest.raises(EvolutionRepositoryConflict, match="compare-and-swap"):
        evaluator.evaluate(candidate.candidate_id, expected_version=candidate.version - 1)


def test_missing_referenced_evidence_is_explicitly_blocking(storage: Storage) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=("evidence:missing",))

    report = GateEvaluator(storage, clock=lambda: NOW).evaluate(
        candidate.candidate_id, expected_version=candidate.version
    )

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.MISSING
    assert evidence.reason_code == "evidence_bundle_missing"
    assert evidence.evidence_hashes == ()


def test_corrupt_referenced_evidence_is_explicitly_blocking(storage: Storage) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=("evidence:corrupt",))
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            ("edict-corrupt", "corrupt evidence", NOW.isoformat()),
        )
        connection.execute(
            """INSERT INTO memorials (id, edict_id, status, created_at)
               VALUES (?, ?, ?, ?)""",
            ("memorial-corrupt", "edict-corrupt", "completed", NOW.isoformat()),
        )
        connection.execute(
            """INSERT INTO evidence_bundles (
                   bundle_id, schema_version, edict_id, memorial_id, status, body_json,
                   content_hash, version, created_at, closed_at, correlation_id
               ) VALUES (?, '1.0', ?, ?, 'closed', '{}', ?, 2, ?, ?, ?)""",
            (
                "evidence:corrupt",
                "edict-corrupt",
                "memorial-corrupt",
                "a" * 64,
                NOW.isoformat(),
                NOW.isoformat(),
                "correlation-corrupt",
            ),
        )
        unit_of_work.commit()

    report = GateEvaluator(storage, clock=lambda: NOW).evaluate(
        candidate.candidate_id, expected_version=candidate.version
    )

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.ERROR
    assert evidence.reason_code == "evidence_bundle_corrupt"


def _evidence_id() -> str:
    return f"evidence:{sha256(b'memorial-evidence').hexdigest()[:32]}"


def _seed_gate_evidence(
    storage: Storage,
    root: Path,
    candidate: EvolutionCandidateV1,
    *,
    close: bool,
    bind_candidate: bool,
    failed_gate: GateName | None = None,
    evidence_time: datetime = NOW,
) -> EvidenceService:
    checks = [
        AcceptanceCheckV1(
            name=f"evolution.gate.{gate.value}",
            command="true",
        )
        for gate in REQUIRED_GATES
        if gate is not GateName.EVIDENCE
    ]
    if bind_candidate:
        checks.append(
            AcceptanceCheckV1(
                name=(
                    f"evolution.candidate.{candidate.candidate_id}."
                    f"{candidate.version}.{candidate.candidate.artifact_digest}"
                ),
                command="true",
            )
        )
    _edict, memorial = seed_closed_run(
        storage,
        acceptance=AcceptancePolicyV1(checks=tuple(checks)),
    )
    for check in checks:
        failed = failed_gate is not None and check.name == f"evolution.gate.{failed_gate.value}"
        storage.append_event(
            memorial.edict_id,
            memorial.id,
            "acceptance.check.completed",
            {
                "name": check.name,
                "status": "failed" if failed else "passed",
                "exit_code": 1 if failed else 0,
                "started_at": NOW.isoformat(),
                "completed_at": NOW.isoformat(),
            },
        )
    artifacts = ArtifactStore(
        root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
        clock=lambda: evidence_time,
    )
    service = EvidenceService(
        storage,
        artifacts,
        executor_manifest_provider=get_executor_manifest,
        clock=lambda: evidence_time,
    )
    service.build_open(memorial.id)
    if close:
        service.close(memorial.id, expected_version=1)
    return service


def _artifact_path(root: Path, digest: str) -> Path:
    return root / digest[:2] / digest


def test_closed_evidence_older_than_staged_candidate_is_blocking(
    storage: Storage, tmp_path: Path
) -> None:
    candidate = _staged_candidate(
        storage,
        evidence_bundle_ids=(_evidence_id(),),
        updated_at=NOW + timedelta(seconds=1),
    )
    service = _seed_gate_evidence(
        storage,
        tmp_path / "stale-artifacts",
        candidate,
        close=True,
        bind_candidate=True,
    )

    report = GateEvaluator(
        storage,
        artifact_verifier=service._artifacts,
        clock=lambda: NOW + timedelta(seconds=2),
    ).evaluate(candidate.candidate_id, expected_version=candidate.version)

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.BLOCKED
    assert evidence.reason_code == "evidence_bundle_stale"


def test_evidence_closed_after_stage_but_before_evaluate_is_fresh(
    storage: Storage, tmp_path: Path
) -> None:
    stage_time = NOW
    evidence_time = NOW + timedelta(seconds=1)
    evaluation_time = NOW + timedelta(seconds=2)
    candidate = _staged_candidate(
        storage,
        evidence_bundle_ids=(_evidence_id(),),
        updated_at=stage_time,
    )
    service = _seed_gate_evidence(
        storage,
        tmp_path / "ordered-fresh-artifacts",
        candidate,
        close=True,
        bind_candidate=True,
        evidence_time=evidence_time,
    )

    report = GateEvaluator(
        storage,
        artifact_verifier=service._artifacts,
        clock=lambda: evaluation_time,
    ).evaluate(candidate.candidate_id, expected_version=candidate.version)

    assert report.promotion_allowed is True
    assert all(result.status is GateStatus.PASSED for result in report.results)


def test_missing_evidence_artifact_metadata_is_error(storage: Storage, tmp_path: Path) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    root = tmp_path / "missing-artifact-metadata"
    service = _seed_gate_evidence(
        storage,
        root,
        candidate,
        close=True,
        bind_candidate=True,
    )
    bundle = storage.evidence_repo.get(_evidence_id())
    assert bundle is not None
    artifact = bundle.snapshot.artifacts[0]
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute("DROP TRIGGER artifact_records_no_delete")
        unit_of_work.connection.execute(
            "DELETE FROM artifact_records WHERE digest=?", (artifact.digest,)
        )
        unit_of_work.commit()

    report = GateEvaluator(
        storage, artifact_verifier=service._artifacts, clock=lambda: NOW
    ).evaluate(candidate.candidate_id, expected_version=candidate.version)

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.ERROR
    assert evidence.reason_code == "evidence_artifact_invalid"


def test_tampered_evidence_artifact_bytes_are_error(storage: Storage, tmp_path: Path) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    root = tmp_path / "tampered-artifact-bytes"
    service = _seed_gate_evidence(
        storage,
        root,
        candidate,
        close=True,
        bind_candidate=True,
    )
    bundle = storage.evidence_repo.get(_evidence_id())
    assert bundle is not None
    artifact = bundle.snapshot.artifacts[0]
    _artifact_path(root, artifact.digest).write_bytes(b"tampered")

    report = GateEvaluator(
        storage, artifact_verifier=service._artifacts, clock=lambda: NOW
    ).evaluate(candidate.candidate_id, expected_version=candidate.version)

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.ERROR
    assert evidence.reason_code == "evidence_artifact_invalid"


def test_open_referenced_evidence_is_blocking(storage: Storage, tmp_path: Path) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    _seed_gate_evidence(
        storage,
        tmp_path / "open-artifacts",
        candidate,
        close=False,
        bind_candidate=True,
    )

    report = GateEvaluator(storage, clock=lambda: NOW).evaluate(
        candidate.candidate_id, expected_version=candidate.version
    )

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.BLOCKED
    assert evidence.reason_code == "evidence_bundle_open"


def test_mismatched_candidate_binding_blocks_closed_evidence(
    storage: Storage, tmp_path: Path
) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    service = _seed_gate_evidence(
        storage,
        tmp_path / "mismatch-artifacts",
        candidate,
        close=True,
        bind_candidate=False,
    )

    report = GateEvaluator(
        storage, artifact_verifier=service._artifacts, clock=lambda: NOW
    ).evaluate(candidate.candidate_id, expected_version=candidate.version)

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.BLOCKED
    assert evidence.reason_code == "candidate_binding_mismatch"


def test_complete_current_evidence_derives_all_eight_passes_and_ready(
    storage: Storage, tmp_path: Path
) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    service = _seed_gate_evidence(
        storage,
        tmp_path / "ready-artifacts",
        candidate,
        close=True,
        bind_candidate=True,
    )

    evaluator = GateEvaluator(storage, artifact_verifier=service._artifacts, clock=lambda: NOW)
    report = evaluator.evaluate(candidate.candidate_id, expected_version=candidate.version)

    assert tuple(result.gate for result in report.results) == REQUIRED_GATES
    assert all(result.status is GateStatus.PASSED for result in report.results)
    assert report.blocking_gates == ()
    assert report.promotion_allowed is True
    assert (
        GateEvaluator(storage, clock=lambda: NOW).get_candidate(candidate.candidate_id).lifecycle
        is CandidateLifecycle.READY
    )


def test_missing_artifact_verifier_fails_closed(storage: Storage, tmp_path: Path) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    _seed_gate_evidence(
        storage,
        tmp_path / "unverified-artifacts",
        candidate,
        close=True,
        bind_candidate=True,
    )

    report = GateEvaluator(storage, clock=lambda: NOW).evaluate(
        candidate.candidate_id, expected_version=candidate.version
    )

    evidence = next(result for result in report.results if result.gate is GateName.EVIDENCE)
    assert evidence.status is GateStatus.ERROR
    assert evidence.reason_code == "evidence_artifact_invalid"


def test_current_green_snapshot_is_not_reused_after_artifact_tampering(
    storage: Storage, tmp_path: Path
) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    root = tmp_path / "green-then-tampered"
    service = _seed_gate_evidence(
        storage,
        root,
        candidate,
        close=True,
        bind_candidate=True,
    )
    evaluator = GateEvaluator(storage, artifact_verifier=service._artifacts, clock=lambda: NOW)
    green = evaluator.evaluate(candidate.candidate_id, expected_version=candidate.version)
    assert green.promotion_allowed is True
    bundle = storage.evidence_repo.get(_evidence_id())
    assert bundle is not None
    artifact = bundle.snapshot.artifacts[0]
    _artifact_path(root, artifact.digest).write_bytes(b"tampered after green")

    with pytest.raises(EvolutionRepositoryConflict, match="evidence is no longer valid"):
        evaluator.get_current_report(candidate.candidate_id)


@pytest.mark.parametrize(
    "failed_gate",
    tuple(gate for gate in REQUIRED_GATES if gate is not GateName.EVIDENCE),
)
def test_each_persisted_failed_gate_is_individually_blocking(
    storage: Storage, tmp_path: Path, failed_gate: GateName
) -> None:
    candidate = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
    _seed_gate_evidence(
        storage,
        tmp_path / f"failed-{failed_gate.value}",
        candidate,
        close=False,
        bind_candidate=True,
        failed_gate=failed_gate,
    )
    # S3 refuses to close failed required evidence, so the authoritative result is fail-closed.
    report = GateEvaluator(storage, clock=lambda: NOW).evaluate(
        candidate.candidate_id, expected_version=candidate.version
    )
    assert report.promotion_allowed is False
    assert GateName.EVIDENCE in report.blocking_gates


def test_outbox_failure_rolls_back_snapshot_lifecycle_audit_and_event(
    storage: Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _staged_candidate(storage)
    evaluator = GateEvaluator(storage, clock=lambda: NOW)

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(evaluator._outbox, "add", fail_outbox)
    with pytest.raises(RuntimeError, match="injected outbox failure"):
        evaluator.evaluate(candidate.candidate_id, expected_version=candidate.version)

    durable = evaluator.get_candidate(candidate.candidate_id)
    assert durable is not None
    assert durable.lifecycle is CandidateLifecycle.STAGED
    assert durable.gate_snapshot_version == 0
    with storage.unit_of_work() as unit_of_work:
        for table in ("evolution_gate_snapshots", "system_audit_events", "outbox_events"):
            assert (
                unit_of_work.connection.execute(
                    f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed table allowlist
                ).fetchone()[0]
                == 0
            )
        unit_of_work.commit()
