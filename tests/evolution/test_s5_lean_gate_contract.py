from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from scripts.check_s5_lean_evidence import (
    GateEvidenceError,
    render_report,
    validate_evidence,
)
from tests.evolution.test_gate_evaluator import NOW, _staged_candidate

from tianshu.application.evolution_view import EvolutionCenterQueryService
from tianshu.evolution.gates import GateEvaluator
from tianshu.models import Edict, Memorial
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage.facade import Storage

_ROLES = ("candidate", "gate", "promotion", "assignment", "rollback", "decision")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _good_evidence(tmp_path: Path) -> dict[str, object]:
    artifacts: dict[str, dict[str, str]] = {}
    for role in _ROLES:
        path = tmp_path / f"{role}.json"
        payload = f"{role}-contract-v1\n".encode()
        path.write_bytes(payload)
        artifacts[role] = {"path": path.name, "sha256": _digest(payload)}

    candidate_digest = artifacts["candidate"]["sha256"]
    champion_digest = "a" * 64
    assignment_hash = artifacts["assignment"]["sha256"]
    assignment = {
        "assignment_id": "assignment:lean-1",
        "candidate_id": "candidate:lean-1",
        "routing_version": 2,
        "arm": "challenger",
        "champion_digest": champion_digest,
        "selected_digest": candidate_digest,
        "effective_overlay_digest": candidate_digest,
        "evidence_candidate_digest": candidate_digest,
        "assignment_hash": assignment_hash,
        "persisted_before_dispatch": True,
    }
    return {
        "schema_version": "s5-lean-core-gate-v1",
        "gate_name": "Lean Core Gate",
        "gate_status": "passed",
        "artifacts": artifacts,
        "candidate": {
            "candidate_id": "candidate:lean-1",
            "kind": "skill",
            "candidate_digest": candidate_digest,
            "automatic_promotion": False,
        },
        "gate": {
            "candidate_id": "candidate:lean-1",
            "report_hash": artifacts["gate"]["sha256"],
            "evidence_bundle_id": "evidence:lean-1",
            "evidence_hash": artifacts["assignment"]["sha256"],
            "promotion_allowed": True,
            "blocking_gates": [],
        },
        "promotion": {
            "authority": "PromotionService",
            "action": "start_canary",
            "candidate_id": "candidate:lean-1",
            "expected_version": 4,
            "reason": "bounded Lean challenger proof",
            "routing_version": 2,
            "allocation_basis_points": 1_000,
            "decision": {"status": "not_required", "risk_tier": "standard"},
        },
        "assignment": assignment,
        "resumed_assignment": copy.deepcopy(assignment),
        "distribution": {"total": 10_000, "challenger": 1_000},
        "rollback": {
            "routing_version_before": 2,
            "routing_version_after": 3,
            "allocation_basis_points_after": 0,
            "new_run_arm": "champion",
            "state": "rolled_back",
            "restore_verified": True,
        },
        "deferred": {
            "openhands": "external_pending",
            "compatibility": "external_pending",
            "roi": "external_pending",
            "cost": "external_pending",
            "full_g4": "external_pending",
        },
    }


def _mutated(
    evidence: dict[str, object],
    *path: str,
    value: object,
) -> dict[str, object]:
    changed = copy.deepcopy(evidence)
    target: dict[str, object] = changed
    for part in path[:-1]:
        target = target[part]  # type: ignore[assignment]
    target[path[-1]] = value
    return changed


def test_complete_lean_evidence_is_recomputed_and_report_is_bounded(tmp_path: Path) -> None:
    evidence = _good_evidence(tmp_path)

    validated = validate_evidence(evidence, root=tmp_path)
    report = render_report(validated)

    assert validated["distribution_rate"] == 0.1
    assert report.startswith("# S5 Lean Core Gate\n")
    assert "Lean Core Gate `passed`" in report
    assert "G4 passed" not in report
    assert "OpenHands" in report and "external_pending" in report


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("gate", "evidence_hash"), "0" * 64, "Evidence hash"),
        (("promotion", "authority"), "UniverseManager", "PromotionService"),
        (("assignment", "selected_digest"), "a" * 64, "challenger overlay"),
        (("assignment", "routing_version"), 3, "routing version"),
        (("distribution", "challenger"), 899, "9%-11%"),
        (("distribution", "challenger"), 1_101, "9%-11%"),
        (("resumed_assignment", "assignment_id"), "assignment:reassigned", "reassigned"),
        (("rollback", "allocation_basis_points_after"), 100, "reopen"),
        (("rollback", "new_run_arm"), "challenger", "reopen"),
        (("candidate", "automatic_promotion"), True, "auto-promote"),
        (("deferred", "full_g4"), "passed", "full G4"),
        (("deferred", "openhands"), "passed", "OpenHands"),
        (("deferred", "roi"), "passed", "ROI"),
        (("deferred", "cost"), "passed", "cost"),
    ],
)
def test_lean_gate_rejects_bypass_corruption_and_unbounded_claims(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    with pytest.raises(GateEvidenceError, match=message):
        validate_evidence(_mutated(_good_evidence(tmp_path), *path, value=value), root=tmp_path)


def test_lean_gate_rejects_missing_evidence_artifact(tmp_path: Path) -> None:
    evidence = _good_evidence(tmp_path)
    (tmp_path / "assignment.json").unlink()

    with pytest.raises(GateEvidenceError, match="Evidence artifact"):
        validate_evidence(evidence, root=tmp_path)


def test_code_promotion_requires_current_high_risk_decision(tmp_path: Path) -> None:
    evidence = _good_evidence(tmp_path)
    evidence = _mutated(evidence, "candidate", "kind", value="code")
    evidence = _mutated(evidence, "promotion", "action", value="promote")

    with pytest.raises(GateEvidenceError, match="Decision"):
        validate_evidence(evidence, root=tmp_path)

    decision = {"status": "resolved", "risk_tier": "high", "resolution": "approve"}
    validate_evidence(
        _mutated(evidence, "promotion", "decision", value=decision),
        root=tmp_path,
    )


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
