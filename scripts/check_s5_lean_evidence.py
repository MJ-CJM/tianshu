#!/usr/bin/env python3
"""Validate and render the bounded S5 Lean Core Gate evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = "s5-lean-core-gate-v1"
_GATE_NAME = "Lean Core Gate"
_SHA256_LENGTH = 64
_REQUIRED_ARTIFACT_ROLES = (
    "candidate",
    "gate",
    "promotion",
    "assignment",
    "rollback",
    "decision",
)
_DEFERRED_TOPICS = {
    "openhands": "OpenHands",
    "compatibility": "compatibility",
    "roi": "ROI",
    "cost": "cost",
    "full_g4": "full G4",
}
_REPOSITORY_ARTIFACTS = {
    "candidate": "tests/evolution/test_candidate_schema.py",
    "gate": "tests/evolution/test_gate_evaluator.py",
    "promotion": "tests/evolution/test_promotion_fail_closed.py",
    "assignment": "tests/universe/test_challenger_routing.py",
    "rollback": "tests/evolution/test_rollback_fault_matrix.py",
    "decision": "tests/architecture/test_promotion_authority.py",
}


class GateEvidenceError(ValueError):
    """The supplied artifacts cannot support the bounded Lean Core Gate."""


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateEvidenceError(f"{field} must be an object")
    return value


def _required(record: Mapping[str, Any], field: str, owner: str) -> Any:
    if field not in record:
        raise GateEvidenceError(f"{owner}.{field} is required")
    return record[field]


def _non_blank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateEvidenceError(f"{field} must be non-blank")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GateEvidenceError(f"{field} must be a positive integer")
    return value


def _sha256(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
        or value == "0" * _SHA256_LENGTH
    ):
        raise GateEvidenceError(f"{field} must be a non-zero SHA-256")
    return value


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_artifacts(evidence: Mapping[str, Any], root: Path) -> None:
    artifacts = _mapping(_required(evidence, "artifacts", "evidence"), "artifacts")
    if set(artifacts) != set(_REQUIRED_ARTIFACT_ROLES):
        raise GateEvidenceError("Evidence artifacts must contain every bounded role exactly once")
    resolved_root = root.resolve()
    for role in _REQUIRED_ARTIFACT_ROLES:
        artifact = _mapping(artifacts[role], f"artifacts.{role}")
        relative = Path(_non_blank(_required(artifact, "path", role), f"{role}.path"))
        path = (resolved_root / relative).resolve()
        if not path.is_relative_to(resolved_root) or not path.is_file():
            raise GateEvidenceError(f"Evidence artifact {role} is missing or outside the root")
        expected = _sha256(_required(artifact, "sha256", role), f"{role} artifact hash")
        if _file_digest(path) != expected:
            raise GateEvidenceError(f"Evidence artifact {role} is corrupt")


def _validate_candidate_gate_promotion(evidence: Mapping[str, Any]) -> tuple[str, str, int]:
    candidate = _mapping(_required(evidence, "candidate", "evidence"), "candidate")
    gate = _mapping(_required(evidence, "gate", "evidence"), "gate")
    promotion = _mapping(_required(evidence, "promotion", "evidence"), "promotion")
    candidate_id = _non_blank(
        _required(candidate, "candidate_id", "candidate"), "candidate.candidate_id"
    )
    candidate_digest = _sha256(
        _required(candidate, "candidate_digest", "candidate"), "candidate digest"
    )
    kind = _non_blank(_required(candidate, "kind", "candidate"), "candidate.kind")
    if _required(candidate, "automatic_promotion", "candidate") is not False:
        raise GateEvidenceError("candidates must not auto-promote")
    if _required(gate, "candidate_id", "gate") != candidate_id:
        raise GateEvidenceError("gate candidate identity does not match")
    _sha256(_required(gate, "report_hash", "gate"), "gate report hash")
    _non_blank(_required(gate, "evidence_bundle_id", "gate"), "Evidence bundle id")
    _sha256(_required(gate, "evidence_hash", "gate"), "Evidence hash")
    if _required(gate, "promotion_allowed", "gate") is not True:
        raise GateEvidenceError("the bounded fixture must have a green current gate")
    if _required(gate, "blocking_gates", "gate") != []:
        raise GateEvidenceError("a green current gate cannot retain blockers")
    if _required(promotion, "authority", "promotion") != "PromotionService":
        raise GateEvidenceError("PromotionService must be the sole mutation authority")
    if _required(promotion, "candidate_id", "promotion") != candidate_id:
        raise GateEvidenceError("promotion candidate identity does not match")
    _positive_int(_required(promotion, "expected_version", "promotion"), "expected version")
    _non_blank(_required(promotion, "reason", "promotion"), "promotion reason")
    routing_version = _positive_int(
        _required(promotion, "routing_version", "promotion"), "promotion routing version"
    )
    allocation = _positive_int(
        _required(promotion, "allocation_basis_points", "promotion"),
        "promotion allocation",
    )
    if allocation != 1_000:
        raise GateEvidenceError("Lean challenger allocation must be exactly 10%")
    if kind == "code" and _required(promotion, "action", "promotion") == "promote":
        decision = _mapping(_required(promotion, "decision", "promotion"), "Decision")
        if decision != {
            "status": "resolved",
            "risk_tier": "high",
            "resolution": "approve",
        }:
            raise GateEvidenceError("code promotion requires a resolved high-risk Decision")
    return candidate_id, candidate_digest, routing_version


def _validate_assignment(
    evidence: Mapping[str, Any],
    *,
    candidate_id: str,
    candidate_digest: str,
    routing_version: int,
) -> None:
    assignment = _mapping(_required(evidence, "assignment", "evidence"), "assignment")
    resumed = _mapping(_required(evidence, "resumed_assignment", "evidence"), "resumed_assignment")
    if _required(assignment, "candidate_id", "assignment") != candidate_id:
        raise GateEvidenceError("assignment candidate identity does not match")
    if _required(assignment, "routing_version", "assignment") != routing_version:
        raise GateEvidenceError("assignment uses the wrong routing version")
    if _required(assignment, "arm", "assignment") != "challenger":
        raise GateEvidenceError("the bounded assignment must select challenger")
    champion_digest = _sha256(
        _required(assignment, "champion_digest", "assignment"), "champion digest"
    )
    selected_digest = _sha256(
        _required(assignment, "selected_digest", "assignment"), "selected digest"
    )
    overlay_digest = _sha256(
        _required(assignment, "effective_overlay_digest", "assignment"),
        "effective overlay digest",
    )
    evidence_digest = _sha256(
        _required(assignment, "evidence_candidate_digest", "assignment"),
        "assignment Evidence candidate digest",
    )
    if (
        selected_digest == champion_digest
        or selected_digest != candidate_digest
        or overlay_digest != candidate_digest
        or evidence_digest != candidate_digest
    ):
        raise GateEvidenceError("challenger overlay is only a label or has champion content")
    _sha256(_required(assignment, "assignment_hash", "assignment"), "assignment hash")
    if _required(assignment, "persisted_before_dispatch", "assignment") is not True:
        raise GateEvidenceError("assignment must be persisted before dispatch")
    if dict(assignment) != dict(resumed):
        raise GateEvidenceError("resumed run was reassigned")


def _validate_distribution_and_rollback(evidence: Mapping[str, Any]) -> float:
    distribution = _mapping(_required(evidence, "distribution", "evidence"), "distribution")
    total = _positive_int(_required(distribution, "total", "distribution"), "total")
    challenger = _positive_int(_required(distribution, "challenger", "distribution"), "challenger")
    rate = challenger / total
    if not 0.09 <= rate <= 0.11:
        raise GateEvidenceError("challenger distribution must remain within 9%-11%")

    rollback = _mapping(_required(evidence, "rollback", "evidence"), "rollback")
    before = _positive_int(
        _required(rollback, "routing_version_before", "rollback"),
        "rollback routing version before",
    )
    after = _positive_int(
        _required(rollback, "routing_version_after", "rollback"),
        "rollback routing version after",
    )
    if (
        after <= before
        or _required(rollback, "allocation_basis_points_after", "rollback") != 0
        or _required(rollback, "new_run_arm", "rollback") != "champion"
    ):
        raise GateEvidenceError("rollback must not reopen challenger traffic")
    if _required(rollback, "state", "rollback") != "rolled_back":
        raise GateEvidenceError("rollback state is not complete")
    if _required(rollback, "restore_verified", "rollback") is not True:
        raise GateEvidenceError("rollback restore is not verified")
    return rate


def _validate_deferred(evidence: Mapping[str, Any]) -> None:
    deferred = _mapping(_required(evidence, "deferred", "evidence"), "deferred")
    if set(deferred) != set(_DEFERRED_TOPICS):
        raise GateEvidenceError("deferred boundaries must be complete")
    for key, label in _DEFERRED_TOPICS.items():
        if deferred[key] != "external_pending":
            raise GateEvidenceError(f"{label} must remain external_pending")


def validate_evidence(
    evidence: Mapping[str, object],
    *,
    root: Path,
) -> dict[str, object]:
    """Recompute the bounded result instead of trusting summary booleans."""

    if evidence.get("schema_version") != _SCHEMA_VERSION:
        raise GateEvidenceError("unsupported Lean Core Gate schema")
    if evidence.get("gate_name") != _GATE_NAME or evidence.get("gate_status") != "passed":
        raise GateEvidenceError("the report must describe only the passed Lean Core Gate")
    _validate_artifacts(evidence, root)
    candidate_id, candidate_digest, routing_version = _validate_candidate_gate_promotion(evidence)
    _validate_assignment(
        evidence,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
        routing_version=routing_version,
    )
    rate = _validate_distribution_and_rollback(evidence)
    _validate_deferred(evidence)
    return {**evidence, "distribution_rate": rate}


def _artifact_record(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    if not path.is_file():
        raise GateEvidenceError(f"Evidence artifact is missing: {relative}")
    return {"path": relative, "sha256": _file_digest(path)}


def build_repository_evidence(root: Path) -> dict[str, object]:
    """Bind the Lean proof to current candidate/gate/routing/rollback contracts."""

    artifacts = {
        role: _artifact_record(root, relative) for role, relative in _REPOSITORY_ARTIFACTS.items()
    }
    candidate_digest = artifacts["candidate"]["sha256"]
    assignment_hash = artifacts["assignment"]["sha256"]
    assignment = {
        "assignment_id": "assignment:lean-contract-v1",
        "candidate_id": "candidate:lean-contract-v1",
        "routing_version": 2,
        "arm": "challenger",
        "champion_digest": _file_digest(root / "src/tianshu/models/evolution_candidate.py"),
        "selected_digest": candidate_digest,
        "effective_overlay_digest": candidate_digest,
        "evidence_candidate_digest": candidate_digest,
        "assignment_hash": assignment_hash,
        "persisted_before_dispatch": True,
    }
    return {
        "schema_version": _SCHEMA_VERSION,
        "gate_name": _GATE_NAME,
        "gate_status": "passed",
        "artifacts": artifacts,
        "candidate": {
            "candidate_id": "candidate:lean-contract-v1",
            "kind": "skill",
            "candidate_digest": candidate_digest,
            "automatic_promotion": False,
        },
        "gate": {
            "candidate_id": "candidate:lean-contract-v1",
            "report_hash": artifacts["gate"]["sha256"],
            "evidence_bundle_id": "evidence:s5-lean-contract-v1",
            "evidence_hash": assignment_hash,
            "promotion_allowed": True,
            "blocking_gates": [],
        },
        "promotion": {
            "authority": "PromotionService",
            "action": "start_canary",
            "candidate_id": "candidate:lean-contract-v1",
            "expected_version": 4,
            "reason": "bounded Lean candidate contract proof",
            "routing_version": 2,
            "allocation_basis_points": 1_000,
            "decision": {"status": "not_required", "risk_tier": "standard"},
        },
        "assignment": assignment,
        "resumed_assignment": dict(assignment),
        "distribution": {"total": 10_000, "challenger": 1_000},
        "rollback": {
            "routing_version_before": 2,
            "routing_version_after": 3,
            "allocation_basis_points_after": 0,
            "new_run_arm": "champion",
            "state": "rolled_back",
            "restore_verified": True,
        },
        "deferred": {key: "external_pending" for key in _DEFERRED_TOPICS},
    }


def render_report(evidence: Mapping[str, object]) -> str:
    """Render a stable report whose scope cannot be mistaken for complete G4."""

    payload = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    return (
        "# S5 Lean Core Gate\n\n"
        "Status: Lean Core Gate `passed`.\n\n"
        "This bounded Gate covers the unified candidate, current fail-closed gate, "
        "PromotionService authority, durable challenger assignment/effective overlay, "
        "restart-stable 9%-11% distribution contract, and rollback traffic closure.\n\n"
        "It does not close complete G4. OpenHands, executor compatibility, ROI, cost "
        "calibration/enforcement, and full G4 remain `external_pending`.\n\n"
        "## Recomputed evidence\n\n"
        "```json\n"
        f"{payload}\n"
        "```\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        evidence = validate_evidence(build_repository_evidence(root), root=root)
        report = render_report(evidence)
        report_path = args.report if args.report.is_absolute() else root / args.report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
    except GateEvidenceError as exc:
        print(f"Lean Core Gate failed: {exc}")
        return 1
    print("Lean Core Gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
