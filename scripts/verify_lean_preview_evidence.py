#!/usr/bin/env python3
"""Strictly verify Lean Preview demo and candidate evidence using only stdlib."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

EXPECTED_STEP_IDS = (
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

REQUIRED_PHASE_REPORT_IDS = (
    "s1_g1_5",
    "s2_lean",
    "s3_core",
    "s4_automation",
    "s5_lean_core",
)

REQUIRED_DEFERRED_WORK_IDS = (
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

_DEMO_FIELDS = {
    "schema_version",
    "batch_id",
    "source_commit",
    "wheel_sha256",
    "environment_fingerprint",
    "fixture",
    "steps",
    "evidence_bundle_id",
    "evidence_bundle_hash",
    "candidate_id",
    "gate_hash",
    "assignment_id",
    "rollback_receipt_hash",
    "external_pending",
    "content_hash",
}
_STEP_FIELDS = {
    "step_id",
    "status",
    "started_at",
    "completed_at",
    "evidence_hashes",
    "observed_state_hash",
}
_ARTIFACT_FIELDS = {
    "schema_version",
    "step_id",
    "requests",
    "correlation_ids",
    "response_hashes",
    "observed",
}
_REQUEST_FIELDS = {"method", "path", "body_sha256"}
_CANDIDATE_FIELDS = {
    "schema_version",
    "source_commit",
    "phase_report_hashes",
    "demo_report_ref",
    "demo_report_hash",
    "wheel_sha256",
    "sdist_sha256",
    "capability_matrix_hash",
    "automation_status",
    "visual_status",
    "visual_approval_record_ref",
    "visual_approval_record_hash",
    "publication_status",
    "deferred_work_ids",
    "content_hash",
}


class EvidenceVerificationError(RuntimeError):
    """Evidence is missing, corrupt, stale, or semantically unbound."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceVerificationError("value is outside canonical JSON") from exc


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvidenceVerificationError(f"missing artifact or report: {path}") from exc


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes())
    except OSError as exc:
        raise EvidenceVerificationError(f"missing {label}: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceVerificationError(f"corrupt {label}: {path}") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise EvidenceVerificationError(f"{label} must be a JSON object")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise EvidenceVerificationError(
            f"{label} fields mismatch (missing={missing}, extra={extra})"
        )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise EvidenceVerificationError(f"{label} must be a JSON object")
    return value


def _sequence(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise EvidenceVerificationError(f"{label} must be an array")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceVerificationError(f"{label} must be non-blank")
    return value


def _digest(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise EvidenceVerificationError(f"{label} is not a lowercase SHA-256 digest")
    return text


def _commit(value: object, label: str) -> str:
    text = _text(value, label)
    if len(text) not in {40, 64} or any(character not in "0123456789abcdef" for character in text):
        raise EvidenceVerificationError(f"{label} is not a full lowercase commit digest")
    return text


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    if "T" not in text or not (text.endswith("Z") or "+" in text[10:] or "-" in text[10:]):
        raise EvidenceVerificationError(f"{label} must be timezone-aware RFC3339")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EvidenceVerificationError(f"{label} must be valid RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceVerificationError(f"{label} must be timezone-aware RFC3339")
    return parsed


def _content_hash(value: Mapping[str, object], label: str) -> str:
    expected = _digest(value.get("content_hash"), f"{label} content hash")
    payload = dict(value)
    payload.pop("content_hash", None)
    actual = _canonical_hash(payload)
    if actual != expected:
        raise EvidenceVerificationError(f"{label} content hash mismatch")
    return expected


def _artifact_filename(index: int, step_id: str) -> str:
    return f"{index:02d}-{step_id}.json"


def _verify_artifact(
    artifact_path: Path,
    *,
    step: dict[str, object],
    step_id: str,
) -> dict[str, object]:
    evidence_hashes = _sequence(step.get("evidence_hashes"), f"{step_id} evidence hashes")
    if len(evidence_hashes) != 1:
        raise EvidenceVerificationError(f"{step_id} must bind exactly one artifact hash")
    expected_hash = _digest(evidence_hashes[0], f"{step_id} artifact hash")
    if not artifact_path.is_file():
        raise EvidenceVerificationError(f"missing artifact for {step_id}: {artifact_path}")
    if _file_hash(artifact_path) != expected_hash:
        raise EvidenceVerificationError(f"artifact hash mismatch for {step_id}")
    artifact = _load_json(artifact_path, f"{step_id} artifact")
    _exact_fields(artifact, _ARTIFACT_FIELDS, f"{step_id} artifact")
    if artifact.get("schema_version") != 1 or artifact.get("step_id") != step_id:
        raise EvidenceVerificationError(f"{step_id} artifact identity mismatch")

    requests = _sequence(artifact.get("requests"), f"{step_id} requests")
    correlations = _sequence(artifact.get("correlation_ids"), f"{step_id} correlations")
    response_hashes = _sequence(artifact.get("response_hashes"), f"{step_id} response hashes")
    if not requests or len(requests) != len(correlations) or len(requests) != len(response_hashes):
        raise EvidenceVerificationError(f"{step_id} request/correlation evidence is incomplete")
    for request_value in requests:
        request = _mapping(request_value, f"{step_id} request")
        _exact_fields(request, _REQUEST_FIELDS, f"{step_id} redacted request")
        if request.get("method") not in {"GET", "POST"}:
            raise EvidenceVerificationError(f"{step_id} request method is invalid")
        path = _text(request.get("path"), f"{step_id} request path")
        if not path.startswith(("/api/", "/health/")) or "://" in path:
            raise EvidenceVerificationError(f"{step_id} request is not a public relative path")
        body_hash = request.get("body_sha256")
        if body_hash is not None:
            _digest(body_hash, f"{step_id} redacted body hash")
    for correlation in correlations:
        _text(correlation, f"{step_id} correlation")
    for response_hash in response_hashes:
        _digest(response_hash, f"{step_id} response hash")

    observed = artifact.get("observed")
    expected_state_hash = _digest(step.get("observed_state_hash"), f"{step_id} observed state hash")
    if _canonical_hash(observed) != expected_state_hash:
        raise EvidenceVerificationError(f"observed state hash mismatch for {step_id}")
    return _mapping(observed, f"{step_id} observed state")


def _verify_bundle(report: dict[str, object], observed: dict[str, object]) -> None:
    bundle = _mapping(observed.get("bundle"), "Evidence Bundle")
    if bundle.get("status") != "closed":
        raise EvidenceVerificationError("Evidence Bundle is not closed")
    bundle_id = _text(bundle.get("bundle_id"), "Evidence Bundle id")
    if bundle_id != report.get("evidence_bundle_id"):
        raise EvidenceVerificationError("Evidence Bundle id mismatch")
    content_hash = _digest(bundle.get("content_hash"), "Evidence Bundle hash")
    unhashed = dict(bundle)
    unhashed.pop("content_hash", None)
    if _canonical_hash(unhashed) != content_hash:
        raise EvidenceVerificationError("Evidence Bundle content hash mismatch")
    if content_hash != report.get("evidence_bundle_hash"):
        raise EvidenceVerificationError("Evidence Bundle report hash mismatch")


def _ref(value: object, label: str) -> dict[str, object]:
    result = _mapping(value, label)
    _text(result.get("version"), f"{label} version")
    _digest(result.get("artifact_digest"), f"{label} artifact digest")
    _digest(result.get("canonical_digest"), f"{label} canonical digest")
    return result


def _verify_assignment(
    report: dict[str, object], observed: dict[str, object], *, candidate_expected: bool
) -> None:
    label = "candidate overlay" if candidate_expected else "post-rollback champion"
    assignment = _mapping(observed.get("assignment"), f"{label} assignment")
    overlay = _mapping(observed.get("effective_overlay"), f"{label} effective overlay")
    assignment_id = _text(assignment.get("assignment_id"), f"{label} assignment id")
    champion = _ref(assignment.get("champion_ref"), f"{label} champion ref")
    selected = _ref(assignment.get("selected_ref"), f"{label} selected ref")
    if overlay.get("assignment_id") != assignment_id:
        raise EvidenceVerificationError(f"{label} overlay assignment mismatch")
    if overlay.get("artifact_digest") != selected.get("artifact_digest") or overlay.get(
        "canonical_digest"
    ) != selected.get("canonical_digest"):
        raise EvidenceVerificationError(f"{label} effective overlay mismatch")
    if assignment.get("candidate_id") != report.get("candidate_id"):
        raise EvidenceVerificationError(f"{label} candidate identity mismatch")
    if candidate_expected:
        if assignment_id != report.get("assignment_id"):
            raise EvidenceVerificationError("candidate overlay report assignment mismatch")
        if selected == champion:
            raise EvidenceVerificationError("candidate overlay did not differ from champion")
    elif selected != champion:
        raise EvidenceVerificationError("post-rollback champion was not selected")


def verify_demo_evidence(
    report_path: Path,
    artifact_root: Path,
    *,
    expected_source_commit: str | None = None,
    expected_wheel_sha256: str | None = None,
) -> dict[str, object]:
    """Verify a complete 13-step demo report and all bound artifacts."""

    report = _load_json(report_path, "demo report")
    _exact_fields(report, _DEMO_FIELDS, "demo report")
    if report.get("schema_version") != 1:
        raise EvidenceVerificationError("demo report schema_version must be 1")
    source_commit = _commit(report.get("source_commit"), "demo source commit")
    wheel_sha256 = _digest(report.get("wheel_sha256"), "demo Wheel hash")
    _digest(report.get("environment_fingerprint"), "environment fingerprint")
    if not isinstance(report.get("fixture"), bool):
        raise EvidenceVerificationError("demo fixture must be boolean")
    if expected_source_commit is not None and source_commit != _commit(
        expected_source_commit, "expected source commit"
    ):
        raise EvidenceVerificationError("demo source commit does not match expected source commit")
    if expected_wheel_sha256 is not None and wheel_sha256 != _digest(
        expected_wheel_sha256, "expected Wheel hash"
    ):
        raise EvidenceVerificationError("demo Wheel hash does not match expected Wheel")
    _content_hash(report, "demo report")

    steps = _sequence(report.get("steps"), "demo steps")
    if len(steps) != len(EXPECTED_STEP_IDS):
        raise EvidenceVerificationError("demo steps are missing or duplicated")
    artifacts = sorted(path.name for path in artifact_root.glob("*.json"))
    expected_artifacts = [
        _artifact_filename(index, step_id) for index, step_id in enumerate(EXPECTED_STEP_IDS, 1)
    ]
    if artifacts != expected_artifacts:
        missing = sorted(set(expected_artifacts) - set(artifacts))
        extra = sorted(set(artifacts) - set(expected_artifacts))
        if missing:
            raise EvidenceVerificationError(f"missing artifact files: {missing}")
        raise EvidenceVerificationError(f"unexpected artifact files: {extra}")

    observed_by_step: dict[str, dict[str, object]] = {}
    for index, (step_value, step_id) in enumerate(zip(steps, EXPECTED_STEP_IDS, strict=True), 1):
        step = _mapping(step_value, f"{step_id} step")
        _exact_fields(step, _STEP_FIELDS, f"{step_id} step")
        if step.get("step_id") != step_id:
            raise EvidenceVerificationError("demo step order is not canonical")
        if step.get("status") != "passed":
            raise EvidenceVerificationError(f"step status is not passed: {step_id}")
        started_at = _timestamp(step.get("started_at"), f"{step_id} started_at")
        completed_at = _timestamp(step.get("completed_at"), f"{step_id} completed_at")
        if completed_at < started_at:
            raise EvidenceVerificationError(f"{step_id} completed before it started")
        observed_by_step[step_id] = _verify_artifact(
            artifact_root / _artifact_filename(index, step_id),
            step=step,
            step_id=step_id,
        )

    if observed_by_step["doctor_ready"].get("status") not in {"ready", "degraded"}:
        raise EvidenceVerificationError("doctor readiness proof is invalid")
    _verify_bundle(report, observed_by_step["verify_evidence_bundle"])
    gate = _mapping(observed_by_step["evaluate_candidate_gate"].get("gate_report"), "gate report")
    if gate.get("promotion_allowed") is not True or gate.get("blocking_gates") != []:
        raise EvidenceVerificationError("candidate gate is not green")
    if _canonical_hash(gate) != report.get("gate_hash"):
        raise EvidenceVerificationError("candidate gate report hash mismatch")
    if gate.get("candidate_id") != report.get("candidate_id"):
        raise EvidenceVerificationError("candidate gate identity mismatch")
    _verify_assignment(
        report,
        observed_by_step["verify_real_candidate_overlay"],
        candidate_expected=True,
    )

    rollback = _mapping(
        observed_by_step["rollback_candidate"].get("rollback_receipt"), "rollback receipt"
    )
    if (
        rollback.get("action") != "rollback"
        or rollback.get("status") != "completed"
        or rollback.get("candidate_id") != report.get("candidate_id")
        or rollback.get("allocation_basis_points") != 0
    ):
        raise EvidenceVerificationError("rollback receipt does not prove allocation zero")
    if _canonical_hash(rollback) != report.get("rollback_receipt_hash"):
        raise EvidenceVerificationError("rollback receipt hash mismatch")

    post = observed_by_step["verify_new_run_uses_champion"]
    _verify_assignment(report, post, candidate_expected=False)
    candidate = _mapping(post.get("candidate"), "post-rollback candidate")
    routing = _mapping(candidate.get("routing"), "post-rollback routing")
    if candidate.get("lifecycle") != "rolled_back" or routing.get("allocation_basis_points") != 0:
        raise EvidenceVerificationError("post-rollback allocation is not zero")
    return report


def verify_candidate_report(
    candidate_report_path: Path,
    *,
    demo_report_path: Path,
    phase_report_paths: Mapping[str, Path],
    wheel_path: Path,
    sdist_path: Path,
    capability_matrix_path: Path,
) -> dict[str, object]:
    """Recompute every candidate phase and release-artifact binding."""

    report = _load_json(candidate_report_path, "candidate report")
    _exact_fields(report, _CANDIDATE_FIELDS, "candidate report")
    if report.get("schema_version") != 1:
        raise EvidenceVerificationError("candidate report schema_version must be 1")
    _content_hash(report, "candidate report")
    source_commit = _commit(report.get("source_commit"), "candidate source commit")
    phase_hashes = _mapping(report.get("phase_report_hashes"), "phase report hashes")
    if set(phase_hashes) != set(REQUIRED_PHASE_REPORT_IDS) or set(phase_report_paths) != set(
        REQUIRED_PHASE_REPORT_IDS
    ):
        raise EvidenceVerificationError("phase report bindings are incomplete or noncanonical")
    for phase_id in REQUIRED_PHASE_REPORT_IDS:
        expected = _digest(phase_hashes.get(phase_id), f"{phase_id} phase report hash")
        if _file_hash(phase_report_paths[phase_id]) != expected:
            raise EvidenceVerificationError(f"phase report hash mismatch: {phase_id}")

    demo = _load_json(demo_report_path, "bound demo report")
    demo_hash = _content_hash(demo, "bound demo report")
    if demo_hash != report.get("demo_report_hash"):
        raise EvidenceVerificationError("candidate demo report hash mismatch")
    if demo.get("fixture") is not False:
        raise EvidenceVerificationError("fixture demo cannot qualify candidate evidence")
    if demo.get("source_commit") != source_commit:
        raise EvidenceVerificationError("candidate/demo source commit mismatch")
    reference = Path(_text(report.get("demo_report_ref"), "demo report reference"))
    if reference.is_absolute() or ".." in reference.parts:
        raise EvidenceVerificationError("demo report reference escapes candidate root")
    expected_demo_path = (candidate_report_path.parent / reference).resolve()
    if expected_demo_path != demo_report_path.resolve():
        raise EvidenceVerificationError("demo report reference does not resolve to supplied report")

    release_bindings = (
        ("wheel_sha256", wheel_path, "Wheel"),
        ("sdist_sha256", sdist_path, "sdist"),
        ("capability_matrix_hash", capability_matrix_path, "capability matrix"),
    )
    for field, path, label in release_bindings:
        expected = _digest(report.get(field), f"candidate {label} hash")
        if _file_hash(path) != expected:
            raise EvidenceVerificationError(f"candidate {label} hash mismatch")
    if report.get("wheel_sha256") != demo.get("wheel_sha256"):
        raise EvidenceVerificationError("candidate Wheel is not the demo Wheel")
    if report.get("automation_status") != "passed":
        raise EvidenceVerificationError("candidate automation status is not passed")
    if report.get("publication_status") != "not_authorized":
        raise EvidenceVerificationError("candidate publication status is not authorized")
    deferred = report.get("deferred_work_ids")
    if not isinstance(deferred, list) or tuple(deferred) != REQUIRED_DEFERRED_WORK_IDS:
        raise EvidenceVerificationError(
            "candidate deferred work IDs are incomplete or noncanonical"
        )
    visual_status = report.get("visual_status")
    approval_ref = report.get("visual_approval_record_ref")
    approval_hash = report.get("visual_approval_record_hash")
    if visual_status == "user_approval_pending":
        if approval_ref is not None or approval_hash is not None:
            raise EvidenceVerificationError("pending visual status cannot bind an approval record")
    elif visual_status == "user_approved":
        _text(approval_ref, "visual approval record reference")
        _digest(approval_hash, "visual approval record hash")
    else:
        raise EvidenceVerificationError("candidate visual status is invalid")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-wheel-sha256")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_demo_evidence(
            args.report,
            args.artifact_root,
            expected_source_commit=args.expected_source_commit,
            expected_wheel_sha256=args.expected_wheel_sha256,
        )
    except EvidenceVerificationError as exc:
        print(f"Lean Preview evidence rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Lean Preview evidence verified: {report['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
