#!/usr/bin/env python3
"""Strictly verify Lean Preview demo and candidate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.evolution.gates import EvolutionGateReportV1
from tianshu.evolution.promotion import PromotionReceiptV1, RollbackReceiptV1
from tianshu.models import Memorial, TaskStatus
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    EvolutionCandidateV1,
)
from tianshu.models.lean_preview import (
    LeanPreviewCandidateReportV1,
    LeanPreviewDemoReportV1,
    resolve_lean_preview_candidate_artifacts,
)
from tianshu.models.run_assignment import (
    EffectiveEvolutionOverlayV1,
    LegacyRunAssignmentV1,
    RunAssignmentV1,
)

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
_CANARY_REQUEST_BINDING_FIELDS = {
    "action",
    "expected_version",
    "idempotency_key",
    "decision_request_id",
    "allocation_basis_points",
    "allocation_seed_id",
    "body_sha256",
}
_ROLLBACK_REQUEST_BINDING_FIELDS = {
    "action",
    "expected_version",
    "idempotency_key",
    "decision_request_id",
    "body_sha256",
}
_PHASE_REPORT_FIELDS = {
    "schema_version",
    "phase_id",
    "gate_id",
    "status",
    "source_commit",
    "report_ref",
    "report_sha256",
    "external_pending",
    "content_hash",
}
_PHASE_GATE_IDS = {
    "s1_g1_5": "G1.5",
    "s2_lean": "S2 Lean",
    "s3_core": "S3 Core",
    "s4_automation": "S4 Automation",
    "s5_lean_core": "S5 Lean Core",
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


def _strict_model(model_type: type[BaseModel], value: object, label: str) -> BaseModel:
    try:
        return model_type.model_validate_json(_canonical_bytes(value))
    except ValidationError as exc:
        raise EvidenceVerificationError(f"{label} violates its strict public contract") from exc


def _real_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise EvidenceVerificationError(f"{label} must be a real directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise EvidenceVerificationError(f"{label} must exist") from exc


def _real_path_without_symlinks(path: Path, label: str, *, directory: bool) -> Path:
    if ".." in path.parts:
        raise EvidenceVerificationError(f"{label} path must not escape through '..'")
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            kind = "real directory" if directory else "real file"
            raise EvidenceVerificationError(f"{label} must be a {kind} without symlinks")
    try:
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise EvidenceVerificationError(f"{label} path must exist") from exc
    if (
        resolved != absolute
        or (directory and not resolved.is_dir())
        or (not directory and not resolved.is_file())
    ):
        kind = "directory" if directory else "file"
        raise EvidenceVerificationError(f"{label} path must resolve to a real {kind}")
    return resolved


def _demo_evidence_paths(report_path: Path, artifact_root: Path) -> tuple[Path, Path, Path]:
    report_absolute = report_path.absolute()
    artifact_absolute = artifact_root.absolute()
    if report_absolute.name != "demo-report.json":
        raise EvidenceVerificationError("demo report path leaf must be demo-report.json")
    if artifact_absolute.name != "artifacts":
        raise EvidenceVerificationError("artifact root path leaf must be artifacts")
    if report_absolute.parent != artifact_absolute.parent:
        raise EvidenceVerificationError("demo report and artifact root must share one batch root")
    batch_root = _real_path_without_symlinks(
        report_absolute.parent, "demo batch root", directory=True
    )
    report = _real_path_without_symlinks(report_absolute, "demo report", directory=False)
    artifacts = _real_path_without_symlinks(artifact_absolute, "artifact root", directory=True)
    if report.parent != batch_root or artifacts.parent != batch_root:
        raise EvidenceVerificationError("demo evidence paths escape their exact batch root")
    return report, artifacts, batch_root


def _confined_file(root: Path, relative: Path, label: str) -> Path:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise EvidenceVerificationError(f"{label} escapes its artifact root")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise EvidenceVerificationError(f"{label} must not contain symlinks")
    try:
        resolved = cursor.resolve(strict=True)
    except OSError as exc:
        raise EvidenceVerificationError(f"missing {label}: {cursor}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise EvidenceVerificationError(f"{label} escapes its artifact root")
    return resolved


def _supplied_confined_file(root: Path, path: Path, label: str) -> Path:
    lexical = path.absolute()
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise EvidenceVerificationError(f"{label} escapes its artifact root") from exc
    return _confined_file(root, relative, label)


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


def _integer(value: object, label: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise EvidenceVerificationError(f"{label} must be an integer >= {minimum}")
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
) -> tuple[dict[str, object], list[dict[str, object]]]:
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
    verified_requests: list[dict[str, object]] = []
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
        verified_requests.append(request)
    for correlation in correlations:
        _text(correlation, f"{step_id} correlation")
    for response_hash in response_hashes:
        _digest(response_hash, f"{step_id} response hash")

    observed = artifact.get("observed")
    expected_state_hash = _digest(step.get("observed_state_hash"), f"{step_id} observed state hash")
    if _canonical_hash(observed) != expected_state_hash:
        raise EvidenceVerificationError(f"observed state hash mismatch for {step_id}")
    return _mapping(observed, f"{step_id} observed state"), verified_requests


def _promotion_journal_id(principal_id: str, idempotency_key: str) -> str:
    identity = _canonical_hash({"principal_id": principal_id, "idempotency_key": idempotency_key})
    command_key = f"promotion:{identity}"
    return hashlib.sha256(f"{command_key}\0completed".encode()).hexdigest()


def _promotion_request_binding(
    observed: dict[str, object],
    requests: list[dict[str, object]],
    *,
    action: str,
    candidate_id: str,
    expected_key: str,
) -> dict[str, object]:
    binding = _mapping(observed.get("request_binding"), f"{action} request binding")
    fields = (
        _CANARY_REQUEST_BINDING_FIELDS
        if action == "start_canary"
        else _ROLLBACK_REQUEST_BINDING_FIELDS
    )
    _exact_fields(binding, fields, f"{action} request binding")
    quoted = urllib.parse.quote(candidate_id, safe="")
    suffix = "canary" if action == "start_canary" else "rollback"
    expected_path = f"/api/evolution/candidates/{quoted}/{suffix}"
    post = requests[-1]
    if (
        binding.get("action") != action
        or binding.get("idempotency_key") != expected_key
        or binding.get("decision_request_id") is not None
        or post.get("method") != "POST"
        or post.get("path") != expected_path
        or post.get("body_sha256") != binding.get("body_sha256")
    ):
        raise EvidenceVerificationError(f"{action} request is not batch/action-bound")
    _integer(binding.get("expected_version"), f"{action} expected version")
    _digest(binding.get("body_sha256"), f"{action} request body hash")
    if action == "start_canary":
        if len(requests) != 1:
            raise EvidenceVerificationError("start_canary request evidence is noncanonical")
        _integer(
            binding.get("allocation_basis_points"),
            "start_canary allocation basis points",
        )
        _text(binding.get("allocation_seed_id"), "start_canary allocation seed id")
    else:
        if (
            len(requests) != 2
            or requests[0].get("method") != "GET"
            or requests[0].get("path") != expected_path.removesuffix("/rollback")
            or requests[0].get("body_sha256") is not None
        ):
            raise EvidenceVerificationError("rollback request evidence is noncanonical")
    return binding


def _memorial(observed: dict[str, object], label: str) -> Memorial:
    model = _strict_model(Memorial, observed.get("memorial"), label)
    if not isinstance(model, Memorial):  # pragma: no cover - type narrowing
        raise TypeError("strict Memorial parser returned the wrong type")
    if model.status is not TaskStatus.COMPLETED:
        raise EvidenceVerificationError(f"{label} is not completed")
    return model


def _assignment(
    observed: dict[str, object], label: str
) -> tuple[RunAssignmentV1, EffectiveEvolutionOverlayV1]:
    assignment = _strict_model(RunAssignmentV1, observed.get("assignment"), f"{label} assignment")
    overlay = _strict_model(
        EffectiveEvolutionOverlayV1,
        observed.get("effective_overlay"),
        f"{label} effective overlay",
    )
    if not isinstance(assignment, RunAssignmentV1) or not isinstance(
        overlay, EffectiveEvolutionOverlayV1
    ):  # pragma: no cover - type narrowing
        raise TypeError("strict assignment parser returned the wrong type")
    if (
        overlay.assignment_id != assignment.assignment_id
        or overlay.artifact_digest != assignment.selected_ref.artifact_digest
        or overlay.canonical_digest != assignment.selected_ref.canonical_digest
    ):
        raise EvidenceVerificationError(f"{label} effective overlay mismatch")
    return assignment, overlay


def verify_demo_evidence(
    report_path: Path,
    artifact_root: Path,
    *,
    expected_source_commit: str,
    expected_wheel_sha256: str,
) -> dict[str, object]:
    """Verify a complete 13-step demo report and all bound artifacts."""

    report_path, artifact_root, batch_root = _demo_evidence_paths(report_path, artifact_root)
    report = _load_json(report_path, "demo report")
    demo_model = _strict_model(LeanPreviewDemoReportV1, report, "demo report")
    if not isinstance(demo_model, LeanPreviewDemoReportV1):  # pragma: no cover
        raise TypeError("strict demo parser returned the wrong type")
    source_commit = demo_model.source_commit
    wheel_sha256 = demo_model.wheel_sha256
    if source_commit != _commit(expected_source_commit, "expected source commit"):
        raise EvidenceVerificationError("demo source commit does not match expected source commit")
    if wheel_sha256 != _digest(expected_wheel_sha256, "expected Wheel hash"):
        raise EvidenceVerificationError("demo Wheel hash does not match expected Wheel")
    if demo_model.batch_id != batch_root.name:
        raise EvidenceVerificationError("demo report batch id does not match its batch root")
    steps = _sequence(report.get("steps"), "demo steps")
    artifact_entries = list(artifact_root.iterdir())
    symlink_entries = sorted(path.name for path in artifact_entries if path.is_symlink())
    if symlink_entries:
        raise EvidenceVerificationError(
            f"artifact root must not contain symlink entries: {symlink_entries}"
        )
    invalid_entries = sorted(path.name for path in artifact_entries if not path.is_file())
    if invalid_entries:
        raise EvidenceVerificationError(
            f"artifact root entries must be regular files: {invalid_entries}"
        )
    artifacts = sorted(path.name for path in artifact_entries)
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
    requests_by_step: dict[str, list[dict[str, object]]] = {}
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
        observed, requests = _verify_artifact(
            _confined_file(
                artifact_root,
                Path(_artifact_filename(index, step_id)),
                f"{step_id} artifact",
            ),
            step=step,
            step_id=step_id,
        )
        observed_by_step[step_id] = observed
        requests_by_step[step_id] = requests

    if observed_by_step["doctor_ready"].get("status") not in {"ready", "degraded"}:
        raise EvidenceVerificationError("doctor readiness proof is invalid")
    principal_id = _text(
        observed_by_step["doctor_ready"].get("principal_id"),
        "authenticated principal id",
    )

    initial = observed_by_step["submit_governed_edict"]
    initial_edict_id = _text(initial.get("edict_id"), "submitted governed edict id")
    initial_memorial_id = _text(initial.get("memorial_id"), "submitted governed memorial id")
    initial_memorial = _memorial(
        observed_by_step["observe_completed_run"], "completed governed Memorial"
    )
    if initial_memorial.id != initial_memorial_id or initial_memorial.edict_id != initial_edict_id:
        raise EvidenceVerificationError("completed governed Memorial is not submitted-run-bound")

    bundle_model = _strict_model(
        ClosedEvidenceBundleV1,
        observed_by_step["verify_evidence_bundle"].get("bundle"),
        "Evidence Bundle",
    )
    if not isinstance(bundle_model, ClosedEvidenceBundleV1):  # pragma: no cover
        raise TypeError("strict Evidence Bundle parser returned the wrong type")
    if (
        bundle_model.edict_id != initial_edict_id
        or bundle_model.memorial_id != initial_memorial_id
        or bundle_model.bundle_id != demo_model.evidence_bundle_id
        or bundle_model.content_hash != demo_model.evidence_bundle_hash
        or bundle_model.snapshot.environment.environment_fingerprint
        != demo_model.environment_fingerprint
        or bundle_model.snapshot.auditor.verdict != "pass"
        or bundle_model.snapshot.auditor.missing_evidence
    ):
        raise EvidenceVerificationError("Evidence Bundle is not submitted-run-bound")

    gate_observed = observed_by_step["evaluate_candidate_gate"]
    candidate_before_gate_model = _strict_model(
        EvolutionCandidateV1,
        gate_observed.get("candidate_before_gate"),
        "staged candidate",
    )
    candidate_model = _strict_model(
        EvolutionCandidateV1, gate_observed.get("candidate"), "evaluated candidate"
    )
    candidate_evidence = _mapping(gate_observed.get("candidate_evidence"), "candidate evidence")
    candidate_evidence_submitted = _mapping(
        candidate_evidence.get("submitted"), "candidate evidence submission"
    )
    candidate_evidence_edict_id = _text(
        candidate_evidence_submitted.get("edict_id"), "candidate evidence edict id"
    )
    candidate_evidence_memorial_id = _text(
        candidate_evidence_submitted.get("memorial_id"), "candidate evidence memorial id"
    )
    candidate_evidence_memorial = _strict_model(
        Memorial,
        candidate_evidence.get("memorial"),
        "candidate evidence Memorial",
    )
    candidate_bundle_model = _strict_model(
        ClosedEvidenceBundleV1,
        candidate_evidence.get("bundle"),
        "candidate Evidence Bundle",
    )
    gate_model = _strict_model(
        EvolutionGateReportV1, gate_observed.get("gate_report"), "gate report"
    )
    if not (
        isinstance(candidate_before_gate_model, EvolutionCandidateV1)
        and isinstance(candidate_model, EvolutionCandidateV1)
        and isinstance(candidate_evidence_memorial, Memorial)
        and isinstance(candidate_bundle_model, ClosedEvidenceBundleV1)
        and isinstance(gate_model, EvolutionGateReportV1)
    ):  # pragma: no cover
        raise TypeError("strict gate parser returned the wrong type")
    if (
        candidate_before_gate_model.kind is not CandidateKind.SKILL
        or candidate_model.kind is not CandidateKind.SKILL
    ):
        raise EvidenceVerificationError("golden demo candidate must be a skill")
    binding_check_name = (
        f"evolution.candidate.{candidate_before_gate_model.candidate_id}."
        f"{candidate_before_gate_model.version}."
        f"{candidate_before_gate_model.candidate.artifact_digest}"
    )
    if (
        candidate_before_gate_model.candidate_id != demo_model.candidate_id
        or candidate_before_gate_model.lifecycle is not CandidateLifecycle.STAGED
        or candidate_before_gate_model.gate_snapshot_version != 0
        or candidate_before_gate_model.evidence_bundle_ids
        or candidate_model.candidate_id != candidate_before_gate_model.candidate_id
        or candidate_model.subject_key != candidate_before_gate_model.subject_key
        or candidate_model.base != candidate_before_gate_model.base
        or candidate_model.candidate != candidate_before_gate_model.candidate
        or candidate_model.lifecycle is not CandidateLifecycle.READY
        or candidate_model.version != candidate_before_gate_model.version + 2
        or candidate_model.gate_snapshot_version
        != candidate_before_gate_model.gate_snapshot_version + 1
        or candidate_model.evidence_bundle_ids
        != (*candidate_before_gate_model.evidence_bundle_ids, candidate_bundle_model.bundle_id)
        or candidate_evidence_memorial.id != candidate_evidence_memorial_id
        or candidate_evidence_memorial.edict_id != candidate_evidence_edict_id
        or candidate_evidence_memorial.status is not TaskStatus.COMPLETED
        or candidate_bundle_model.edict_id != candidate_evidence_edict_id
        or candidate_bundle_model.memorial_id != candidate_evidence_memorial_id
        or candidate_bundle_model.closed_at < candidate_before_gate_model.updated_at
        or not any(
            check.name == binding_check_name and check.status == "passed"
            for check in candidate_bundle_model.snapshot.checks
        )
        or candidate_model.candidate == candidate_model.base
        or gate_model.candidate_id != candidate_model.candidate_id
        or gate_model.candidate_version != candidate_model.version
        or gate_model.candidate_digest != candidate_model.candidate.artifact_digest
        or gate_model.gate_snapshot_version != candidate_model.gate_snapshot_version
        or gate_model.evidence_bundle_ids != candidate_model.evidence_bundle_ids
        or gate_model.report_hash != demo_model.gate_hash
        or not gate_model.promotion_allowed
        or gate_model.blocking_gates
        or any(
            candidate_bundle_model.content_hash not in result.evidence_hashes
            for result in gate_model.results
        )
    ):
        raise EvidenceVerificationError("candidate gate is not candidate/evidence-bound")

    promotion_model = _strict_model(
        PromotionReceiptV1,
        observed_by_step["start_skill_canary"].get("promotion_receipt"),
        "canary promotion receipt",
    )
    if not isinstance(promotion_model, PromotionReceiptV1):  # pragma: no cover
        raise TypeError("strict promotion parser returned the wrong type")
    canary_key = f"lean-preview:{demo_model.batch_id}:canary"
    canary_request = _promotion_request_binding(
        observed_by_step["start_skill_canary"],
        requests_by_step["start_skill_canary"],
        action="start_canary",
        candidate_id=candidate_model.candidate_id,
        expected_key=canary_key,
    )
    canary_expected_version = _integer(
        canary_request.get("expected_version"), "canary expected version"
    )
    if (
        promotion_model.action != "start_canary"
        or promotion_model.idempotency_key != canary_key
        or promotion_model.journal_id != _promotion_journal_id(principal_id, canary_key)
        or promotion_model.candidate_id != candidate_model.candidate_id
        or canary_expected_version != gate_model.candidate_version
        or promotion_model.candidate_version != canary_expected_version + 1
        or promotion_model.gate_snapshot_version != gate_model.gate_snapshot_version
        or promotion_model.gate_report_hash != gate_model.report_hash
        or promotion_model.lifecycle is not CandidateLifecycle.CANARY
        or promotion_model.allocation_basis_points != canary_request.get("allocation_basis_points")
        or promotion_model.effect_artifact_digest is not None
    ):
        raise EvidenceVerificationError("canary receipt is not gate/journal-bound")

    canary_submit = observed_by_step["submit_canary_eligible_run"]
    canary_edict_id = _text(canary_submit.get("edict_id"), "canary edict id")
    canary_memorial_id = _text(canary_submit.get("memorial_id"), "canary memorial id")
    canary_observed = observed_by_step["verify_real_candidate_overlay"]
    canary_memorial = _memorial(canary_observed, "completed canary Memorial")
    canary_assignment, canary_overlay = _assignment(canary_observed, "candidate overlay")
    if (
        canary_memorial.id != canary_memorial_id
        or canary_memorial.edict_id != canary_edict_id
        or canary_assignment.memorial_id != canary_memorial.id
        or canary_assignment.assignment_id != demo_model.assignment_id
        or canary_assignment.candidate_id != candidate_model.candidate_id
        or canary_assignment.routing_version != promotion_model.routing_version
        or canary_assignment.champion_ref != candidate_model.base
        or canary_assignment.selected_ref != candidate_model.candidate
        or canary_overlay.kind is not CandidateKind.SKILL
        or canary_overlay.subject_key != candidate_model.subject_key
        or canary_overlay.artifact_digest != candidate_model.candidate.artifact_digest
        or canary_overlay.canonical_digest != candidate_model.candidate.canonical_digest
    ):
        raise EvidenceVerificationError("candidate overlay assignment is not canary-run-bound")

    rollback_model = _strict_model(
        RollbackReceiptV1,
        observed_by_step["rollback_candidate"].get("rollback_receipt"),
        "rollback receipt",
    )
    if not isinstance(rollback_model, RollbackReceiptV1):  # pragma: no cover
        raise TypeError("strict rollback parser returned the wrong type")
    rollback_observed = observed_by_step["rollback_candidate"]
    candidate_before_rollback = _strict_model(
        EvolutionCandidateV1,
        rollback_observed.get("candidate_before_rollback"),
        "pre-rollback candidate",
    )
    if not isinstance(candidate_before_rollback, EvolutionCandidateV1):  # pragma: no cover
        raise TypeError("strict pre-rollback parser returned the wrong type")
    rollback_key = f"lean-preview:{demo_model.batch_id}:rollback"
    rollback_request = _promotion_request_binding(
        rollback_observed,
        requests_by_step["rollback_candidate"],
        action="rollback",
        candidate_id=candidate_model.candidate_id,
        expected_key=rollback_key,
    )
    rollback_expected_version = _integer(
        rollback_request.get("expected_version"), "rollback expected version"
    )
    if (
        candidate_before_rollback.kind is not CandidateKind.SKILL
        or candidate_before_rollback.candidate_id != candidate_model.candidate_id
        or candidate_before_rollback.subject_key != candidate_model.subject_key
        or candidate_before_rollback.base != candidate_model.base
        or candidate_before_rollback.candidate != candidate_model.candidate
        or candidate_before_rollback.lifecycle is not CandidateLifecycle.CANARY
        or candidate_before_rollback.version != rollback_expected_version
        or rollback_model.idempotency_key != rollback_key
        or rollback_model.journal_id != _promotion_journal_id(principal_id, rollback_key)
        or rollback_model.candidate_id != candidate_model.candidate_id
        or rollback_model.candidate_version != rollback_expected_version + 2
        or rollback_model.routing_version <= promotion_model.routing_version
        or rollback_model.effect_artifact_digest != candidate_model.base.artifact_digest
        or canonical_sha256(rollback_model) != demo_model.rollback_receipt_hash
    ):
        raise EvidenceVerificationError("rollback receipt is not candidate/journal-bound")

    post = observed_by_step["verify_new_run_uses_champion"]
    post_submitted = _mapping(post.get("submitted"), "post-rollback submitted run")
    post_memorial = _memorial(post, "completed post-rollback Memorial")
    post_assignment = _strict_model(
        LegacyRunAssignmentV1,
        post.get("assignment"),
        "post-rollback champion assignment",
    )
    if not isinstance(post_assignment, LegacyRunAssignmentV1):  # pragma: no cover
        raise TypeError("strict legacy assignment parser returned the wrong type")
    final_candidate = _strict_model(
        EvolutionCandidateV1, post.get("candidate"), "post-rollback candidate"
    )
    if not isinstance(final_candidate, EvolutionCandidateV1):  # pragma: no cover
        raise TypeError("strict candidate parser returned the wrong type")
    if (
        post_memorial.id != post_submitted.get("memorial_id")
        or post_memorial.edict_id != post_submitted.get("edict_id")
        or post_assignment.memorial_id != post_memorial.id
        or post.get("effective_overlay") is not None
        or final_candidate.candidate_id != candidate_model.candidate_id
        or final_candidate.kind is not CandidateKind.SKILL
        or final_candidate.subject_key != candidate_model.subject_key
        or final_candidate.base != candidate_model.base
        or final_candidate.candidate != candidate_model.candidate
        or final_candidate.lifecycle is not CandidateLifecycle.ROLLED_BACK
        or final_candidate.version != rollback_model.candidate_version
        or final_candidate.routing is None
        or final_candidate.routing.routing_version != rollback_model.routing_version
        or final_candidate.routing.allocation_basis_points != 0
    ):
        raise EvidenceVerificationError("post-rollback champion proof is not rollback-run-bound")
    return report


def verify_candidate_report(
    candidate_report_path: Path,
    *,
    artifact_root: Path,
    demo_report_path: Path,
    phase_report_paths: Mapping[str, Path],
    wheel_path: Path,
    sdist_path: Path,
    capability_matrix_path: Path,
) -> dict[str, object]:
    """Recompute every candidate phase and release-artifact binding."""

    artifact_root = _real_directory(artifact_root, "candidate artifact root")
    candidate_report_path = _supplied_confined_file(
        artifact_root, candidate_report_path, "candidate report"
    )
    report = _load_json(candidate_report_path, "candidate report")
    candidate_model = _strict_model(LeanPreviewCandidateReportV1, report, "candidate report")
    if not isinstance(candidate_model, LeanPreviewCandidateReportV1):  # pragma: no cover
        raise TypeError("strict candidate parser returned the wrong type")
    source_commit = candidate_model.source_commit
    phase_hashes = candidate_model.phase_report_hashes
    if set(phase_hashes) != set(REQUIRED_PHASE_REPORT_IDS) or set(phase_report_paths) != set(
        REQUIRED_PHASE_REPORT_IDS
    ):
        raise EvidenceVerificationError("phase report bindings are incomplete or noncanonical")
    for phase_id in REQUIRED_PHASE_REPORT_IDS:
        phase_path = _supplied_confined_file(
            artifact_root,
            phase_report_paths[phase_id],
            f"{phase_id} structured phase report",
        )
        try:
            phase = _load_json(phase_path, f"{phase_id} structured phase report")
        except EvidenceVerificationError as exc:
            raise EvidenceVerificationError(
                f"{phase_id} must be a structured phase report"
            ) from exc
        _exact_fields(phase, _PHASE_REPORT_FIELDS, f"{phase_id} structured phase report")
        if phase.get("schema_version") != 1 or phase.get("phase_id") != phase_id:
            raise EvidenceVerificationError(f"{phase_id} phase identity mismatch")
        if phase.get("gate_id") != _PHASE_GATE_IDS[phase_id]:
            raise EvidenceVerificationError(f"{phase_id} gate identity mismatch")
        if phase.get("status") != "passed":
            raise EvidenceVerificationError(f"{phase_id} phase status is not passed")
        if phase.get("source_commit") != source_commit:
            raise EvidenceVerificationError(f"{phase_id} source commit mismatch")
        pending = _sequence(phase.get("external_pending"), f"{phase_id} external_pending")
        if pending:
            raise EvidenceVerificationError(f"{phase_id} external_pending cannot count as passed")
        phase_content_hash = _content_hash(phase, f"{phase_id} structured phase report")
        if phase_content_hash != phase_hashes[phase_id]:
            raise EvidenceVerificationError(f"phase report hash mismatch: {phase_id}")
        report_reference = Path(
            _text(phase.get("report_ref"), f"{phase_id} source report reference")
        )
        source_report = _confined_file(artifact_root, report_reference, f"{phase_id} source report")
        if _file_hash(source_report) != _digest(
            phase.get("report_sha256"), f"{phase_id} source report hash"
        ):
            raise EvidenceVerificationError(f"{phase_id} source report hash mismatch")

    demo_report_path = _supplied_confined_file(artifact_root, demo_report_path, "bound demo report")
    expected_demo_path = _confined_file(
        artifact_root, Path(candidate_model.demo_report_ref), "candidate demo report"
    )
    if expected_demo_path != demo_report_path:
        raise EvidenceVerificationError("demo report reference does not resolve to supplied report")
    try:
        resolve_lean_preview_candidate_artifacts(candidate_model, artifact_root)
    except ValueError as exc:
        raise EvidenceVerificationError(f"candidate artifact resolution failed: {exc}") from exc

    release_bindings = (
        (candidate_model.wheel_sha256, wheel_path, "Wheel"),
        (candidate_model.sdist_sha256, sdist_path, "sdist"),
        (candidate_model.capability_matrix_hash, capability_matrix_path, "capability matrix"),
    )
    for expected, path, label in release_bindings:
        resolved_path = _supplied_confined_file(artifact_root, path, f"candidate {label}")
        if _file_hash(resolved_path) != expected:
            raise EvidenceVerificationError(f"candidate {label} hash mismatch")
    if candidate_model.automation_status != "passed":
        raise EvidenceVerificationError("candidate automation status is not passed")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
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
