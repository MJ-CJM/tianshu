from __future__ import annotations

import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
import scripts.check_s5_lean_evidence as checker
from scripts.check_s5_lean_evidence import (
    GateEvidenceError,
    render_report,
    validate_evidence,
)
from tests.evolution.test_gate_evaluator import (
    NOW,
    _evidence_id,
    _seed_gate_evidence,
    _staged_candidate,
)

from tianshu.application.evolution_view import EvolutionCenterQueryService
from tianshu.evolution.gates import GateEvaluator
from tianshu.evolution.promotion import (
    PromotionService,
    StartCanaryCommand,
    UnavailablePromotionAdapter,
)
from tianshu.models import Edict, Memorial
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage.facade import Storage


@pytest.fixture(scope="module")
def production_evidence(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    root = tmp_path_factory.mktemp("s5-production-evidence")
    path = root / "s5-lean-evidence.json"
    checker.generate_evidence_artifact(work_dir=root / "runtime", output=path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_production_harness_emits_strict_real_execution_evidence(
    production_evidence: tuple[Path, dict[str, object]],
) -> None:
    path, raw = production_evidence
    validated = checker.validate_evidence_file(path)

    assert raw["schema_version"] == "s5-lean-core-gate-v2"
    assert validated["assignment_total"] == 10_000
    assert validated["challenger_assignments"] == 1_029
    assert 0.09 <= validated["distribution_rate"] <= 0.11
    assert validated["restart_stable"] is True
    assert validated["rollback_closed_traffic"] is True
    assert validated["evidence_bundle_count"] == 2
    snapshots = raw["snapshots"]
    assert snapshots["canary"]["candidates"][0]["lifecycle"] == "canary"  # type: ignore[index]
    assert snapshots["promoted"]["candidates"][0]["lifecycle"] == "promoted"  # type: ignore[index]
    assert snapshots["rolled_back"]["candidates"][0]["lifecycle"] == "rolled_back"  # type: ignore[index]


def test_complete_lean_evidence_is_recomputed_and_report_is_bounded(
    production_evidence: tuple[Path, dict[str, object]],
) -> None:
    _path, raw = production_evidence
    validated = validate_evidence(raw)
    report = render_report(validated)

    assert validated["distribution_rate"] == pytest.approx(0.1029)
    assert report.startswith("# S5 Lean Core Gate\n")
    assert "Lean Core Gate `passed`" in report
    assert "G4 passed" not in report
    assert "OpenHands" in report and "external_pending" in report


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_claim",
        "unknown_nested_field",
        "arbitrary_action",
        "missing_decision",
        "mismatched_expected_version",
        "assignment_hash",
        "restart_assignment",
        "rollback_traffic",
        "evidence_bundle",
        "deferred_claim",
    ),
)
def test_lean_gate_rejects_forgeable_corrupt_or_unbound_artifacts(
    production_evidence: tuple[Path, dict[str, object]],
    mutation: str,
) -> None:
    _path, original = production_evidence
    evidence = copy.deepcopy(original)
    if mutation == "unknown_claim":
        evidence["unreviewed_claim"] = "G4 passed; OpenHands passed; ROI passed"
    elif mutation == "unknown_nested_field":
        evidence["promotion_actions"]["promote_receipt"]["claimed_safe"] = True  # type: ignore[index]
    elif mutation == "arbitrary_action":
        evidence["promotion_journal"][0]["action"] = "direct_universe_switch"  # type: ignore[index]
    elif mutation == "missing_decision":
        del evidence["decisions"]["promote"]  # type: ignore[index]
    elif mutation == "mismatched_expected_version":
        evidence["promotion_actions"]["promote_command"]["expected_version"] = 999_999  # type: ignore[index]
    elif mutation == "assignment_hash":
        evidence["assignments"]["assignment_hashes"][0] = "0" * 64  # type: ignore[index]
    elif mutation == "restart_assignment":
        evidence["restart_after"]["assignment_id"] = "assignment:reassigned"  # type: ignore[index]
    elif mutation == "rollback_traffic":
        evidence["final_routing"]["allocation_basis_points"] = 100  # type: ignore[index]
    elif mutation == "evidence_bundle":
        evidence["assignment_bundle"]["content_hash"] = "0" * 64  # type: ignore[index]
    else:
        evidence["deferred"]["full_g4"] = "passed"  # type: ignore[index]

    with pytest.raises(GateEvidenceError):
        validate_evidence(evidence)


def test_real_evolution_snapshot_reads_candidates_blockers_and_last_gate_hash(
    tmp_path: Path,
) -> None:
    storage = Storage(str(tmp_path / "evolution-view.db"))
    storage.init_db()
    try:
        staged = _staged_candidate(storage)
        evaluator = GateEvaluator(storage, clock=lambda: NOW)
        report = evaluator.evaluate(staged.candidate_id, expected_version=staged.version)
        service = EvolutionCenterQueryService(storage, evaluator)
        auth = AuthContext(
            principal=Principal(
                id="user:evolution-reviewer",
                kind=PrincipalKind.HUMAN,
                display_name="Evolution reviewer",
                scopes=frozenset({"admin"}),
            ),
            source=AuthenticationSource.BEARER,
            client_kind=ClientKind.WEB,
            correlation_id="corr-s5-lean",
        )

        snapshot = service.get_snapshot(auth)

        assert snapshot.status == "enabled"
        assert snapshot.reason_code == "evidence_blocking"
        assert snapshot.last_gate_hash == report.report_hash
        assert len(snapshot.candidates) == 1
        summary = snapshot.candidates[0]
        assert summary.candidate_id == staged.candidate_id
        assert summary.promotion_allowed is False
        assert {gate.code for gate in summary.gates if gate.blocking} == {
            item.value for item in report.blocking_gates
        }
    finally:
        storage.close()


def test_real_evolution_snapshot_keeps_bound_gate_after_start_canary(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "evolution-canary-view.db"))
    storage.init_db()
    try:
        staged = _staged_candidate(storage, evidence_bundle_ids=(_evidence_id(),))
        evidence = _seed_gate_evidence(
            storage,
            tmp_path / "evolution-canary-artifacts",
            staged,
            close=True,
            bind_candidate=True,
            evidence_time=NOW + timedelta(seconds=1),
        )
        evaluator = GateEvaluator(
            storage,
            artifact_verifier=evidence._artifacts,
            clock=lambda: NOW + timedelta(seconds=2),
        )
        report = evaluator.evaluate(staged.candidate_id, expected_version=staged.version)
        ready = evaluator.get_candidate(staged.candidate_id)
        assert ready is not None
        auth = AuthContext(
            principal=Principal(
                id="principal-1",
                kind=PrincipalKind.HUMAN,
                display_name="Reviewer",
                scopes=frozenset({"api"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.API,
            correlation_id="corr-real-canary-view",
        )
        PromotionService(
            storage,
            evaluator,
            adapter_resolver=lambda _kind: UnavailablePromotionAdapter(),
            clock=lambda: NOW + timedelta(seconds=3),
        ).start_canary(
            ready.candidate_id,
            StartCanaryCommand(
                expected_version=ready.version,
                idempotency_key="real-canary-view",
                reason="verify compatible gate read",
                allocation_basis_points=500,
                allocation_seed_id="real-canary-view-seed",
            ),
            auth=auth,
        )

        snapshot = EvolutionCenterQueryService(storage, evaluator).get_snapshot(auth)

        assert snapshot.status == "enabled"
        assert snapshot.reason_code == "enabled"
        assert snapshot.last_gate_hash == report.report_hash
        assert snapshot.candidates[0].lifecycle == "canary"
        assert snapshot.candidates[0].promotion_allowed is True
        assert all(gate.status == "passed" for gate in snapshot.candidates[0].gates)
    finally:
        storage.close()


def test_real_evolution_snapshot_counts_only_current_routing_assignments(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "evolution-routing-view.db"))
    storage.init_db()
    try:
        candidate = _staged_candidate(storage)
        for suffix in ("current-champion", "current-challenger", "old-challenger"):
            edict = Edict(id=f"edict-{suffix}", goal=suffix, submitter="user:owner")
            memorial = Memorial(id=f"memorial-{suffix}", edict_id=edict.id)
            storage.save_edict(edict)
            storage.save_memorial(memorial)
        with storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            connection.execute(
                """INSERT INTO evolution_routing_allocations (
                       candidate_id, routing_version, allocation_basis_points,
                       allocation_seed_id, routing_json, routing_hash, version,
                       created_at, updated_at
                   ) VALUES (?, 2, 1000, 'lean-seed', '{}', ?, 1, ?, ?)""",
                (candidate.candidate_id, "f" * 64, NOW.isoformat(), NOW.isoformat()),
            )
            rows = (
                ("current-champion", 2, '{"version":"champion"}'),
                ("current-challenger", 2, '{"version":"candidate"}'),
                ("old-challenger", 1, '{"version":"candidate"}'),
            )
            for suffix, routing_version, selected_ref in rows:
                connection.execute(
                    """INSERT INTO run_evolution_assignments (
                           assignment_id, memorial_id, candidate_id, routing_version, bucket,
                           champion_ref_json, selected_ref_json, overlay_digest,
                           assignment_json, assignment_hash, created_at
                       ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, '{}', ?, ?)""",
                    (
                        f"assignment:{suffix}",
                        f"memorial-{suffix}",
                        candidate.candidate_id,
                        routing_version,
                        '{"version":"champion"}',
                        selected_ref,
                        "d" * 64,
                        hashlib.sha256(suffix.encode()).hexdigest(),
                        NOW.isoformat(),
                    ),
                )
            unit_of_work.commit()

        snapshot = EvolutionCenterQueryService(storage, GateEvaluator(storage)).get_snapshot(
            AuthContext(
                principal=Principal(
                    id="user:evolution-reviewer",
                    kind=PrincipalKind.HUMAN,
                    display_name="Evolution reviewer",
                    scopes=frozenset({"admin"}),
                ),
                source=AuthenticationSource.BEARER,
                client_kind=ClientKind.WEB,
                correlation_id="corr-routing-view",
            )
        )

        assert snapshot.routing[0].routing_version == 2
        assert snapshot.routing[0].allocation_percent == 10
        assert snapshot.routing[0].champion_assignment_count == 1
        assert snapshot.routing[0].challenger_assignment_count == 1
    finally:
        storage.close()
