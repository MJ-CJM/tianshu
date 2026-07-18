from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
VERIFIER_PATH = ROOT / "scripts" / "verify_lean_preview_evidence.py"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
STEP_IDS = (
    "doctor_ready",
    "submit_governed_edict",
    "observe_decision_required",
    "resolve_decision_with_reason",
    "observe_completed_run",
    "verify_evidence_bundle",
    "propose_skill_candidate",
    "evaluate_candidate_gate",
    "start_skill_canary",
    "submit_canary_eligible_run",
    "verify_real_candidate_overlay",
    "rollback_candidate",
    "verify_new_run_uses_champion",
)
DEFERRED_IDS = (
    "P2-A1",
    "P2-A2",
    "P2-A3",
    "P2-A4",
    "P2-A5",
    "P2-B1",
    "P2-B2",
    "P2-C1",
    "P2-C2",
    "P2-C3",
    "P3-D1",
    "P3-D2",
    "P3-D3",
    "P3-D4",
    "P4-E1",
    "P4-E2",
    "P4-E3",
    "P4-E4",
    "P4-E5",
)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _module():
    spec = importlib.util.spec_from_file_location("lean_preview_verifier", VERIFIER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ref(version: str, digest: str) -> dict[str, str]:
    return {"version": version, "artifact_digest": digest, "canonical_digest": digest}


def _write_demo(root: Path) -> tuple[Path, Path, dict[str, object]]:
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True)
    champion = _ref("champion-v1", DIGEST_A)
    candidate = _ref("candidate-v1", DIGEST_B)
    bundle: dict[str, object] = {
        "schema_version": "1.0",
        "bundle_id": "bundle-1",
        "edict_id": "edict-1",
        "memorial_id": "memorial-1",
        "status": "closed",
        "snapshot": {"checks": [], "artifacts": []},
        "version": 2,
        "created_at": "2026-07-18T00:00:00Z",
        "closed_at": "2026-07-18T00:00:01Z",
    }
    bundle["content_hash"] = _canonical_hash(bundle)
    gate = {
        "candidate_id": "candidate-1",
        "candidate_version": 3,
        "candidate_digest": DIGEST_B,
        "gate_snapshot_version": 1,
        "promotion_allowed": True,
        "blocking_gates": [],
    }
    rollback = {
        "action": "rollback",
        "status": "completed",
        "candidate_id": "candidate-1",
        "candidate_version": 4,
        "allocation_basis_points": 0,
        "effect_artifact_digest": DIGEST_A,
    }
    canary_assignment = {
        "assignment_id": "assignment-canary",
        "memorial_id": "memorial-2",
        "candidate_id": "candidate-1",
        "champion_ref": champion,
        "selected_ref": candidate,
    }
    post_assignment = {
        "assignment_id": "assignment-post-rollback",
        "memorial_id": "memorial-3",
        "candidate_id": "candidate-1",
        "champion_ref": champion,
        "selected_ref": champion,
    }
    observed: dict[str, object] = {
        "doctor_ready": {"status": "ready"},
        "submit_governed_edict": {"edict_id": "edict-1", "memorial_id": "memorial-1"},
        "observe_decision_required": {
            "decision_request_id": "decision-1",
            "edict_id": "edict-1",
            "status": "pending",
            "version": 1,
        },
        "resolve_decision_with_reason": {"status": "resolved"},
        "observe_completed_run": {"memorial_id": "memorial-1", "status": "completed"},
        "verify_evidence_bundle": {"bundle": bundle},
        "propose_skill_candidate": {"candidate_id": "candidate-1", "lifecycle": "proposed"},
        "evaluate_candidate_gate": {"gate_report": gate},
        "start_skill_canary": {"candidate_version": 3, "allocation_basis_points": 1000},
        "submit_canary_eligible_run": {
            "edict_id": "edict-2",
            "memorial_id": "memorial-2",
        },
        "verify_real_candidate_overlay": {
            "assignment": canary_assignment,
            "effective_overlay": {
                "assignment_id": "assignment-canary",
                "artifact_digest": DIGEST_B,
                "canonical_digest": DIGEST_B,
            },
        },
        "rollback_candidate": {"rollback_receipt": rollback},
        "verify_new_run_uses_champion": {
            "assignment": post_assignment,
            "effective_overlay": {
                "assignment_id": "assignment-post-rollback",
                "artifact_digest": DIGEST_A,
                "canonical_digest": DIGEST_A,
            },
            "candidate": {
                "candidate_id": "candidate-1",
                "lifecycle": "rolled_back",
                "routing": {"allocation_basis_points": 0},
            },
        },
    }
    steps: list[dict[str, object]] = []
    for index, step_id in enumerate(STEP_IDS, 1):
        artifact = {
            "schema_version": 1,
            "step_id": step_id,
            "requests": [
                {
                    "method": "GET",
                    "path": "/health/ready" if index == 1 else "/api/public",
                    "body_sha256": None,
                }
            ],
            "correlation_ids": [f"corr-{index}"],
            "response_hashes": [_canonical_hash({"ok": True, "index": index})],
            "observed": observed[step_id],
        }
        path = artifact_root / f"{index:02d}-{step_id}.json"
        path.write_bytes(_canonical_bytes(artifact))
        steps.append(
            {
                "step_id": step_id,
                "status": "passed",
                "started_at": f"2026-07-18T00:00:{index - 1:02d}Z",
                "completed_at": f"2026-07-18T00:00:{index:02d}Z",
                "evidence_hashes": [hashlib.sha256(path.read_bytes()).hexdigest()],
                "observed_state_hash": _canonical_hash(observed[step_id]),
            }
        )
    report: dict[str, object] = {
        "schema_version": 1,
        "batch_id": "batch-verifier",
        "source_commit": "1" * 40,
        "wheel_sha256": DIGEST_A,
        "environment_fingerprint": DIGEST_B,
        "fixture": False,
        "steps": steps,
        "evidence_bundle_id": "bundle-1",
        "evidence_bundle_hash": bundle["content_hash"],
        "candidate_id": "candidate-1",
        "gate_hash": _canonical_hash(gate),
        "assignment_id": "assignment-canary",
        "rollback_receipt_hash": _canonical_hash(rollback),
        "external_pending": ["voiceover"],
    }
    report["content_hash"] = _canonical_hash(report)
    report_path = root / "demo-report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path, artifact_root, report


def _rehash_report(path: Path, report: dict[str, object]) -> None:
    report = copy.deepcopy(report)
    report.pop("content_hash", None)
    report["content_hash"] = _canonical_hash(report)
    path.write_text(json.dumps(report), encoding="utf-8")


def test_verifier_recomputes_all_demo_hashes_and_semantic_bindings(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, _report = _write_demo(tmp_path)

    verified = module.verify_demo_evidence(
        report_path,
        artifact_root,
        expected_source_commit="1" * 40,
        expected_wheel_sha256=DIGEST_A,
    )

    assert verified["batch_id"] == "batch-verifier"


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("wrong_commit", "source commit"),
        ("wrong_wheel", "Wheel"),
        ("corrupt_artifact", "artifact hash"),
        ("missing_artifact", "missing artifact"),
        ("failed_step", "step status"),
        ("corrupt_bundle", "Evidence Bundle"),
        ("champion_only", "candidate overlay"),
        ("rollback_allocation", "allocation"),
        ("post_rollback_challenger", "post-rollback champion"),
        ("rollback_receipt", "rollback receipt"),
    ],
)
def test_verifier_rejects_corrupt_or_unbound_demo_evidence(
    tmp_path: Path, case: str, message: str
) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path)
    expected_commit = "1" * 40
    expected_wheel = DIGEST_A
    if case == "wrong_commit":
        expected_commit = "2" * 40
    elif case == "wrong_wheel":
        expected_wheel = DIGEST_B
    elif case == "corrupt_artifact":
        path = artifact_root / "01-doctor_ready.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif case == "missing_artifact":
        (artifact_root / "01-doctor_ready.json").unlink()
    elif case == "failed_step":
        report["steps"][2]["status"] = "failed"
        _rehash_report(report_path, report)
    else:
        step_index = {
            "corrupt_bundle": 5,
            "champion_only": 10,
            "rollback_allocation": 11,
            "post_rollback_challenger": 12,
            "rollback_receipt": 11,
        }[case]
        step_id = STEP_IDS[step_index]
        path = artifact_root / f"{step_index + 1:02d}-{step_id}.json"
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if case == "corrupt_bundle":
            artifact["observed"]["bundle"]["snapshot"] = {"tampered": True}
        elif case == "champion_only":
            assignment = artifact["observed"]["assignment"]
            assignment["selected_ref"] = assignment["champion_ref"]
            artifact["observed"]["effective_overlay"]["artifact_digest"] = DIGEST_A
            artifact["observed"]["effective_overlay"]["canonical_digest"] = DIGEST_A
        elif case == "rollback_allocation":
            artifact["observed"]["rollback_receipt"]["allocation_basis_points"] = 1
        elif case == "post_rollback_challenger":
            assignment = artifact["observed"]["assignment"]
            assignment["selected_ref"] = _ref("candidate-v1", DIGEST_B)
        else:
            report["rollback_receipt_hash"] = DIGEST_B
        if case != "rollback_receipt":
            path.write_bytes(_canonical_bytes(artifact))
            report["steps"][step_index]["evidence_hashes"] = [
                hashlib.sha256(path.read_bytes()).hexdigest()
            ]
            report["steps"][step_index]["observed_state_hash"] = _canonical_hash(
                artifact["observed"]
            )
        _rehash_report(report_path, report)

    with pytest.raises(module.EvidenceVerificationError, match=message):
        module.verify_demo_evidence(
            report_path,
            artifact_root,
            expected_source_commit=expected_commit,
            expected_wheel_sha256=expected_wheel,
        )


def test_candidate_verifier_recomputes_phase_and_release_artifact_hashes(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, demo = _write_demo(tmp_path / "demo")

    phase_paths: dict[str, Path] = {}
    for phase_id in module.REQUIRED_PHASE_REPORT_IDS:
        path = tmp_path / f"{phase_id}.report"
        path.write_text(f"verified {phase_id}\n", encoding="utf-8")
        phase_paths[phase_id] = path
    wheel = tmp_path / "candidate.whl"
    sdist = tmp_path / "candidate.tar.gz"
    matrix = tmp_path / "capability-matrix.md"
    wheel.write_bytes(b"exact wheel")
    sdist.write_bytes(b"exact sdist")
    matrix.write_text("verified matrix\n", encoding="utf-8")
    demo["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    _rehash_report(report_path, demo)
    demo = json.loads(report_path.read_text(encoding="utf-8"))
    module.verify_demo_evidence(report_path, artifact_root)
    candidate: dict[str, object] = {
        "schema_version": 1,
        "source_commit": "1" * 40,
        "phase_report_hashes": {
            phase_id: hashlib.sha256(path.read_bytes()).hexdigest()
            for phase_id, path in phase_paths.items()
        },
        "demo_report_ref": "demo/demo-report.json",
        "demo_report_hash": demo["content_hash"],
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
        "capability_matrix_hash": hashlib.sha256(matrix.read_bytes()).hexdigest(),
        "automation_status": "passed",
        "visual_status": "user_approval_pending",
        "visual_approval_record_ref": None,
        "visual_approval_record_hash": None,
        "publication_status": "not_authorized",
        "deferred_work_ids": list(DEFERRED_IDS),
    }
    candidate["content_hash"] = _canonical_hash(candidate)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    module.verify_candidate_report(
        candidate_path,
        demo_report_path=report_path,
        phase_report_paths=phase_paths,
        wheel_path=wheel,
        sdist_path=sdist,
        capability_matrix_path=matrix,
    )

    candidate["deferred_work_ids"] = list(DEFERRED_IDS[:-1])
    candidate.pop("content_hash")
    candidate["content_hash"] = _canonical_hash(candidate)
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(module.EvidenceVerificationError, match="deferred work IDs"):
        module.verify_candidate_report(
            candidate_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )

    candidate["deferred_work_ids"] = list(DEFERRED_IDS)
    candidate["phase_report_hashes"]["s3_core"] = DIGEST_A
    candidate.pop("content_hash")
    candidate["content_hash"] = _canonical_hash(candidate)
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(module.EvidenceVerificationError, match="phase report hash"):
        module.verify_candidate_report(
            candidate_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )
