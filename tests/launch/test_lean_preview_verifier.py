from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
VERIFIER_PATH = ROOT / "scripts" / "verify_lean_preview_evidence.py"
RUNNER_TEST_PATH = ROOT / "tests" / "launch" / "test_lean_preview_runner.py"
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
    spec = importlib.util.spec_from_file_location("lean_preview_runner_fixture", RUNNER_TEST_PATH)
    assert spec is not None and spec.loader is not None
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
    runner = fixture_module._module()
    scenario_path = root / "scenario.json"
    root.mkdir(parents=True, exist_ok=True)
    scenario_path.write_text(json.dumps(fixture_module._scenario()), encoding="utf-8")
    report_path = runner.run_demo(
        base_url="http://127.0.0.1:7998",
        scenario_path=scenario_path,
        batch_id="batch-verifier",
        output_root=root,
        transport=fixture_module._FakeTransport(runner),
        clock=fixture_module._Clock(),
        sleeper=lambda _seconds: None,
        environ={
            "TIANSHU_BOOTSTRAP_TOKEN": "secret",
            "TIANSHU_LEAN_FIXTURE": "false",
        },
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return report_path, report_path.parent / "artifacts", report


def _rehash_report(path: Path, report: dict[str, object]) -> None:
    report = copy.deepcopy(report)
    report.pop("content_hash", None)
    report["content_hash"] = _canonical_hash(report)
    path.write_text(json.dumps(report), encoding="utf-8")


def _rewrite_step(
    report_path: Path,
    artifact_root: Path,
    report: dict[str, object],
    step_index: int,
) -> tuple[Path, dict[str, object]]:
    step_id = STEP_IDS[step_index]
    path = artifact_root / f"{step_index + 1:02d}-{step_id}.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    return path, artifact


def _save_step(
    report_path: Path,
    report: dict[str, object],
    step_index: int,
    path: Path,
    artifact: dict[str, object],
) -> None:
    path.write_bytes(_canonical_bytes(artifact))
    report["steps"][step_index]["evidence_hashes"] = [hashlib.sha256(path.read_bytes()).hexdigest()]
    report["steps"][step_index]["observed_state_hash"] = _canonical_hash(artifact["observed"])
    _rehash_report(report_path, report)


def _verify_demo(module, report_path: Path, artifact_root: Path) -> dict[str, object]:
    return module.verify_demo_evidence(
        report_path,
        artifact_root,
        expected_source_commit="1" * 40,
        expected_wheel_sha256=DIGEST_A,
    )


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
        ("bundle_run_splice", "submitted-run-bound"),
        ("gate_digest_splice", "candidate/evidence-bound"),
        ("canary_memorial_splice", "canary-run-bound"),
        ("post_memorial_splice", "rollback-run-bound"),
    ],
)
def test_verifier_rejects_rehashed_cross_run_splices(
    tmp_path: Path, case: str, message: str
) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path)
    step_index = {
        "bundle_run_splice": 5,
        "gate_digest_splice": 7,
        "canary_memorial_splice": 10,
        "post_memorial_splice": 12,
    }[case]
    path, artifact = _rewrite_step(report_path, artifact_root, report, step_index)
    if case == "bundle_run_splice":
        bundle = artifact["observed"]["bundle"]
        bundle["edict_id"] = "edict:spliced"
        bundle["memorial_id"] = "memorial:spliced"
        bundle.pop("content_hash")
        bundle["content_hash"] = _canonical_hash(bundle)
        report["evidence_bundle_hash"] = bundle["content_hash"]
    elif case == "gate_digest_splice":
        gate = artifact["observed"]["gate_report"]
        gate["candidate_digest"] = DIGEST_A
        report["gate_hash"] = _canonical_hash(gate)
    elif case == "canary_memorial_splice":
        artifact["observed"]["assignment"]["memorial_id"] = "memorial:spliced"
    else:
        artifact["observed"]["assignment"]["memorial_id"] = "memorial:spliced"
    _save_step(report_path, report, step_index, path, artifact)

    with pytest.raises(module.EvidenceVerificationError, match=message):
        module.verify_demo_evidence(
            report_path,
            artifact_root,
            expected_source_commit="1" * 40,
            expected_wheel_sha256=DIGEST_A,
        )


def test_verifier_uses_strict_demo_schema_and_confined_real_artifacts(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path / "strict")
    report["fixture"] = True
    report["external_pending"] = []
    _rehash_report(report_path, report)
    with pytest.raises(module.EvidenceVerificationError, match="strict public contract"):
        _verify_demo(module, report_path, artifact_root)

    report_path, artifact_root, _report = _write_demo(tmp_path / "root-link-target")
    real_artifact_root = artifact_root.with_name("real-artifacts")
    artifact_root.rename(real_artifact_root)
    artifact_root.symlink_to(real_artifact_root, target_is_directory=True)
    with pytest.raises(module.EvidenceVerificationError, match="real directory"):
        _verify_demo(module, report_path, artifact_root)

    artifact_root.unlink()
    real_artifact_root.rename(artifact_root)
    path = artifact_root / "01-doctor_ready.json"
    outside = tmp_path / "outside-artifact.json"
    path.rename(outside)
    path.symlink_to(outside)
    with pytest.raises(module.EvidenceVerificationError, match="symlink"):
        _verify_demo(module, report_path, artifact_root)


def test_verifier_requires_expected_build_identity_keyword_arguments(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, _report = _write_demo(tmp_path)

    with pytest.raises(TypeError):
        module.verify_demo_evidence(report_path, artifact_root)


@pytest.mark.parametrize(
    "case",
    [
        "wrong_report_leaf",
        "unpaired_report_root",
        "report_leaf_symlink",
        "batch_component_symlink",
        "batch_id_root_mismatch",
        "wrong_artifact_leaf",
    ],
)
def test_verifier_requires_exact_real_paired_demo_paths(tmp_path: Path, case: str) -> None:
    module = _module()
    report_path, artifact_root, _report = _write_demo(tmp_path / case)
    supplied_report = report_path
    supplied_artifacts = artifact_root
    if case == "wrong_report_leaf":
        supplied_report = report_path.with_name("renamed-report.json")
        report_path.rename(supplied_report)
    elif case == "unpaired_report_root":
        other_root = report_path.parent.parent / "other-batch"
        other_root.mkdir()
        supplied_report = other_root / "demo-report.json"
        supplied_report.write_bytes(report_path.read_bytes())
    elif case == "report_leaf_symlink":
        target = report_path.with_name("real-report.json")
        report_path.rename(target)
        report_path.symlink_to(target)
    elif case == "batch_component_symlink":
        linked_root = report_path.parent.parent / "linked-batch"
        linked_root.symlink_to(report_path.parent, target_is_directory=True)
        supplied_report = linked_root / "demo-report.json"
        supplied_artifacts = linked_root / "artifacts"
    elif case == "batch_id_root_mismatch":
        renamed_root = report_path.parent.with_name("renamed-batch")
        report_path.parent.rename(renamed_root)
        supplied_report = renamed_root / "demo-report.json"
        supplied_artifacts = renamed_root / "artifacts"
    else:
        supplied_artifacts = artifact_root.with_name("evidence-artifacts")
        artifact_root.rename(supplied_artifacts)

    with pytest.raises(module.EvidenceVerificationError, match="path|root|symlink|batch"):
        _verify_demo(module, supplied_report, supplied_artifacts)


def test_verifier_rejects_rehashed_valid_code_candidate_and_overlays(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path)
    for step_index in (7, 12):
        path, artifact = _rewrite_step(report_path, artifact_root, report, step_index)
        candidate = artifact["observed"]["candidate"]
        candidate["kind"] = "code"
        candidate["evolution_contract"]["kind"] = "code"
        candidate["evolution_contract_hash"] = _canonical_hash(candidate["evolution_contract"])
        if step_index == 12:
            artifact["observed"]["effective_overlay"]["kind"] = "code"
        _save_step(report_path, report, step_index, path, artifact)
    path, artifact = _rewrite_step(report_path, artifact_root, report, 10)
    artifact["observed"]["effective_overlay"]["kind"] = "code"
    _save_step(report_path, report, 10, path, artifact)

    with pytest.raises(module.EvidenceVerificationError, match="skill"):
        _verify_demo(module, report_path, artifact_root)


@pytest.mark.parametrize(
    ("step_index", "field", "replacement"),
    [
        (10, "kind", "memory"),
        (10, "subject_key", "skill:spliced"),
        (12, "kind", "memory"),
        (12, "subject_key", "skill:spliced"),
    ],
)
def test_verifier_binds_overlay_domain_identity_to_the_skill_candidate(
    tmp_path: Path,
    step_index: int,
    field: str,
    replacement: str,
) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path)
    path, artifact = _rewrite_step(report_path, artifact_root, report, step_index)
    artifact["observed"]["effective_overlay"][field] = replacement
    _save_step(report_path, report, step_index, path, artifact)

    with pytest.raises(module.EvidenceVerificationError, match="overlay|rollback-run-bound"):
        _verify_demo(module, report_path, artifact_root)


@pytest.mark.parametrize(
    "case",
    [
        "canary_receipt_key",
        "rollback_receipt_key",
        "canary_journal_id",
        "rollback_journal_id",
        "canary_expected_version",
        "rollback_expected_version",
        "canary_request_path",
        "rollback_request_hash",
    ],
)
def test_verifier_binds_public_promotion_requests_and_receipts_to_batch(
    tmp_path: Path, case: str
) -> None:
    module = _module()
    report_path, artifact_root, report = _write_demo(tmp_path)
    step_index = 8 if case.startswith("canary") else 11
    path, artifact = _rewrite_step(report_path, artifact_root, report, step_index)
    observed = artifact["observed"]
    receipt_name = "promotion_receipt" if step_index == 8 else "rollback_receipt"
    receipt = observed[receipt_name]
    if case.endswith("receipt_key"):
        receipt["idempotency_key"] = f"{receipt['idempotency_key']}:spliced"
    elif case.endswith("journal_id"):
        receipt["journal_id"] = DIGEST_B
    elif case.endswith("expected_version"):
        observed["request_binding"]["expected_version"] += 1
    elif case == "canary_request_path":
        artifact["requests"][-1]["path"] = artifact["requests"][-1]["path"].replace(
            "/canary", "/rollback"
        )
    else:
        artifact["requests"][-1]["body_sha256"] = DIGEST_B
    if step_index == 11 and case in {
        "rollback_receipt_key",
        "rollback_journal_id",
    }:
        report["rollback_receipt_hash"] = _canonical_hash(receipt)
    _save_step(report_path, report, step_index, path, artifact)

    with pytest.raises(module.EvidenceVerificationError, match="request|receipt|journal|batch"):
        _verify_demo(module, report_path, artifact_root)


def test_verifier_cli_requires_expected_build_identity() -> None:
    module = _module()
    with pytest.raises(SystemExit):
        module._parser().parse_args(["--report", "report.json", "--artifact-root", "artifacts"])


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
        ("rollback_allocation", "rollback receipt"),
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


@pytest.mark.parametrize(
    "extra_entry",
    ["regular_file", "directory", "symlink", "fifo"],
)
def test_verifier_rejects_every_unexpected_artifact_root_entry(
    tmp_path: Path, extra_entry: str
) -> None:
    module = _module()
    report_path, artifact_root, _report = _write_demo(tmp_path)
    extra = artifact_root / f"extra-{extra_entry}.bin"
    if extra_entry == "regular_file":
        extra.write_text("unexpected", encoding="utf-8")
    elif extra_entry == "directory":
        extra.mkdir()
    elif extra_entry == "symlink":
        extra.symlink_to(artifact_root / "01-doctor_ready.json")
    else:
        os.mkfifo(extra)

    with pytest.raises(module.EvidenceVerificationError, match="artifact|regular|entry"):
        _verify_demo(module, report_path, artifact_root)


def test_candidate_verifier_recomputes_phase_and_release_artifact_hashes(tmp_path: Path) -> None:
    module = _module()
    report_path, artifact_root, demo = _write_demo(tmp_path / "demo")

    phase_paths: dict[str, Path] = {}
    for phase_id in module.REQUIRED_PHASE_REPORT_IDS:
        source_path = tmp_path / "reports" / f"{phase_id}.md"
        source_path.parent.mkdir(exist_ok=True)
        source_path.write_text(f"verified {phase_id}\n", encoding="utf-8")
        phase: dict[str, object] = {
            "schema_version": 1,
            "phase_id": phase_id,
            "gate_id": module._PHASE_GATE_IDS[phase_id],
            "status": "passed",
            "source_commit": "1" * 40,
            "report_ref": f"reports/{phase_id}.md",
            "report_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "external_pending": [],
        }
        phase["content_hash"] = _canonical_hash(phase)
        path = tmp_path / f"{phase_id}.json"
        path.write_bytes(_canonical_bytes(phase))
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
    module.verify_demo_evidence(
        report_path,
        artifact_root,
        expected_source_commit="1" * 40,
        expected_wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )
    candidate: dict[str, object] = {
        "schema_version": 1,
        "source_commit": "1" * 40,
        "phase_report_hashes": {
            phase_id: json.loads(path.read_text(encoding="utf-8"))["content_hash"]
            for phase_id, path in phase_paths.items()
        },
        "demo_report_ref": "demo/batch-verifier/demo-report.json",
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
        artifact_root=tmp_path,
        demo_report_path=report_path,
        phase_report_paths=phase_paths,
        wheel_path=wheel,
        sdist_path=sdist,
        capability_matrix_path=matrix,
    )

    linked_root = tmp_path.parent / f"{tmp_path.name}-linked-root"
    linked_root.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(module.EvidenceVerificationError, match="real directory"):
        module.verify_candidate_report(
            linked_root / "candidate.json",
            artifact_root=linked_root,
            demo_report_path=linked_root / "demo/batch-verifier/demo-report.json",
            phase_report_paths={
                phase_id: linked_root / path.name for phase_id, path in phase_paths.items()
            },
            wheel_path=linked_root / wheel.name,
            sdist_path=linked_root / sdist.name,
            capability_matrix_path=linked_root / matrix.name,
        )

    phase_path = phase_paths["s1_g1_5"]
    outside_phase = tmp_path.parent / f"{tmp_path.name}-outside-phase.json"
    phase_path.rename(outside_phase)
    phase_path.symlink_to(outside_phase)
    with pytest.raises(module.EvidenceVerificationError, match="symlink"):
        module.verify_candidate_report(
            candidate_path,
            artifact_root=tmp_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )
    phase_path.unlink()
    outside_phase.rename(phase_path)

    candidate["deferred_work_ids"] = list(DEFERRED_IDS[:-1])
    candidate.pop("content_hash")
    candidate["content_hash"] = _canonical_hash(candidate)
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(module.EvidenceVerificationError, match="strict public contract"):
        module.verify_candidate_report(
            candidate_path,
            artifact_root=tmp_path,
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
            artifact_root=tmp_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )

    candidate["phase_report_hashes"]["s3_core"] = json.loads(
        phase_paths["s3_core"].read_text(encoding="utf-8")
    )["content_hash"]
    approval: dict[str, object] = {
        "schema_version": 1,
        "approval_id": "approval-1",
        "approval_kind": "explicit_user_review",
        "decision": "approved",
        "approved_by": "preview-owner",
        "approved_at": "2026-07-18T12:00:00Z",
        "source_commit": "1" * 40,
        "demo_report_hash": demo["content_hash"],
    }
    approval["content_hash"] = _canonical_hash(approval)
    approval_path = tmp_path / "approval.json"
    approval_path.write_bytes(_canonical_bytes(approval))
    candidate["visual_status"] = "user_approved"
    candidate["visual_approval_record_ref"] = "approval.json"
    candidate["visual_approval_record_hash"] = approval["content_hash"]
    candidate.pop("content_hash")
    candidate["content_hash"] = _canonical_hash(candidate)
    candidate_path.write_bytes(_canonical_bytes(candidate))
    module.verify_candidate_report(
        candidate_path,
        artifact_root=tmp_path,
        demo_report_path=report_path,
        phase_report_paths=phase_paths,
        wheel_path=wheel,
        sdist_path=sdist,
        capability_matrix_path=matrix,
    )

    approval_path.unlink()
    with pytest.raises(module.EvidenceVerificationError, match="approval record"):
        module.verify_candidate_report(
            candidate_path,
            artifact_root=tmp_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("arbitrary_text", "structured phase report"),
        ("wrong_gate", "gate identity"),
        ("wrong_commit", "source commit"),
        ("external_pending", "external_pending"),
    ],
)
def test_candidate_verifier_requires_structured_passed_phase_evidence(
    tmp_path: Path, mutation: str, message: str
) -> None:
    module = _module()
    report_path, _artifact_root, demo = _write_demo(tmp_path / "demo")
    wheel = tmp_path / "candidate.whl"
    sdist = tmp_path / "candidate.tar.gz"
    matrix = tmp_path / "capability-matrix.md"
    wheel.write_bytes(b"exact wheel")
    sdist.write_bytes(b"exact sdist")
    matrix.write_text("verified matrix\n", encoding="utf-8")
    demo["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    _rehash_report(report_path, demo)
    demo = json.loads(report_path.read_text(encoding="utf-8"))

    phase_paths: dict[str, Path] = {}
    phase_hashes: dict[str, str] = {}
    for phase_id in module.REQUIRED_PHASE_REPORT_IDS:
        source_path = tmp_path / "reports" / f"{phase_id}.md"
        source_path.parent.mkdir(exist_ok=True)
        source_path.write_text(f"verified {phase_id}\n", encoding="utf-8")
        phase: dict[str, object] = {
            "schema_version": 1,
            "phase_id": phase_id,
            "gate_id": module._PHASE_GATE_IDS[phase_id],
            "status": "passed",
            "source_commit": "1" * 40,
            "report_ref": f"reports/{phase_id}.md",
            "report_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "external_pending": [],
        }
        phase["content_hash"] = _canonical_hash(phase)
        path = tmp_path / f"{phase_id}.json"
        path.write_bytes(_canonical_bytes(phase))
        phase_paths[phase_id] = path
        phase_hashes[phase_id] = str(phase["content_hash"])

    target = phase_paths["s3_core"]
    if mutation == "arbitrary_text":
        target.write_text("passed\n", encoding="utf-8")
    else:
        phase = json.loads(target.read_text(encoding="utf-8"))
        if mutation == "wrong_gate":
            phase["gate_id"] = "S4 Automation"
        elif mutation == "wrong_commit":
            phase["source_commit"] = "2" * 40
        else:
            phase["external_pending"] = ["unverified external evidence"]
        phase.pop("content_hash")
        phase["content_hash"] = _canonical_hash(phase)
        target.write_bytes(_canonical_bytes(phase))
        phase_hashes["s3_core"] = phase["content_hash"]

    candidate: dict[str, object] = {
        "schema_version": 1,
        "source_commit": "1" * 40,
        "phase_report_hashes": phase_hashes,
        "demo_report_ref": "demo/batch-verifier/demo-report.json",
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
    candidate_path.write_bytes(_canonical_bytes(candidate))

    with pytest.raises(module.EvidenceVerificationError, match=message):
        module.verify_candidate_report(
            candidate_path,
            artifact_root=tmp_path,
            demo_report_path=report_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=matrix,
        )
