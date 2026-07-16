#!/usr/bin/env python3
"""Validate the machine-readable S3 Core Governance Gate evidence block."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation

_SCHEMA_VERSION = "s3-core-gate-v1"
_BLOCK_START = "<!-- s3-core-evidence:v1 -->\n```json\n"
_BLOCK_END = "\n```\n<!-- /s3-core-evidence:v1 -->"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_COUNT_KEYS = ("passed", "failed", "skipped", "deselected")
_REQUIRED_COMMANDS = {
    "focused_fault_matrix": (
        "env -u VIRTUAL_ENV .venv/bin/python -m pytest "
        "tests/integration/test_edict_idempotency.py "
        "tests/integration/test_outbox_recovery.py "
        "tests/integration/test_decision_service_restart_race.py "
        "tests/integration/test_managed_production_recovery.py "
        "tests/integration/test_claim_lease_recovery.py "
        "tests/integration/test_side_effect_idempotency.py "
        "tests/integration/test_continuation_recovery.py "
        "tests/integration/test_replan_evidence.py tests/evidence "
        "tests/notifier/test_internal_delivery_recovery.py -q"
    ),
    "ruff_check": ".venv/bin/ruff check src tests",
    "ruff_format_check": ".venv/bin/ruff format --check src tests",
    "mypy": ".venv/bin/mypy",
    "import_linter": ".venv/bin/lint-imports",
    "full_non_slow": ('env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q'),
}
_REQUIRED_FAULTS = {
    "idempotent_submission": "tests/integration/test_edict_idempotency.py",
    "committed_outbox_restart": "tests/integration/test_outbox_recovery.py",
    "decision_restart_recovery": "tests/integration/test_decision_service_restart_race.py",
    "outer_loop_restart_recovery": "tests/integration/test_managed_production_recovery.py",
    "claim_lease_recovery": "tests/integration/test_claim_lease_recovery.py",
    "side_effect_idempotency": "tests/integration/test_side_effect_idempotency.py",
    "continuation_recovery": "tests/integration/test_continuation_recovery.py",
    "replan_evidence": "tests/integration/test_replan_evidence.py",
    "evidence_bundle_integrity": "tests/evidence",
    "internal_delivery_recovery": "tests/notifier/test_internal_delivery_recovery.py",
}
_DEFAULT_ALLOWED_DIRTY = (
    "docs/cc-fable-v1/reports/s3-core-governance-report.md",
    "docs/launch/capability-matrix.md",
    "docs/cc-fable-v1/PROGRESS.md",
)


class GateEvidenceError(ValueError):
    """The report cannot support the S3 Core Gate claim."""


@dataclass(frozen=True)
class GateContext:
    """Live repository facts used to prevent stale or unrelated evidence."""

    accepted_source_commits: tuple[str, ...]
    dirty_paths: tuple[str, ...]
    allowed_dirty_paths: tuple[str, ...]
    source_hashes: Mapping[str, str]


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateEvidenceError(f"{field} must be an object")
    return value


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise GateEvidenceError(f"{field} must be an array")
    return value


def _required(record: Mapping[str, Any], field: str, owner: str) -> Any:
    if field not in record:
        raise GateEvidenceError(f"{owner}.{field} is required")
    return record[field]


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value) or value == "0" * 64:
        raise GateEvidenceError(f"{field} must be a non-zero SHA-256")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateEvidenceError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, field: str) -> int:
    result = _non_negative_int(value, field)
    if result == 0:
        raise GateEvidenceError(f"{field} must be positive")
    return result


def _validate_source(
    record: Mapping[str, Any],
    owner: str,
    context: GateContext,
    *,
    expected_path: str | None = None,
) -> None:
    path = _required(record, "source_path", owner) if expected_path is None else expected_path
    if not isinstance(path, str) or not path:
        raise GateEvidenceError(f"{owner}.source_path must be non-empty")
    if expected_path is None and record.get("source_path") != path:
        raise GateEvidenceError(f"{owner}.source_path is invalid")
    actual = context.source_hashes.get(path)
    claimed = _sha256(_required(record, "source_sha256", owner), f"{owner}.source_sha256")
    if actual is None or claimed != actual:
        raise GateEvidenceError(f"{owner}.source_sha256 does not match {path}")


def parse_report(content: str) -> dict[str, object]:
    """Extract the single canonical JSON evidence block from a Markdown report."""

    if content.count(_BLOCK_START) != 1 or content.count(_BLOCK_END) != 1:
        raise GateEvidenceError("report must contain exactly one S3 evidence block")
    start = content.index(_BLOCK_START) + len(_BLOCK_START)
    end = content.index(_BLOCK_END, start)
    try:
        evidence = json.loads(content[start:end])
    except json.JSONDecodeError as exc:
        raise GateEvidenceError("S3 evidence block is not valid JSON") from exc
    if not isinstance(evidence, dict):
        raise GateEvidenceError("S3 evidence block must be a JSON object")
    return evidence


def render_report(markdown_body: str, evidence: Mapping[str, object]) -> str:
    """Render a stable Markdown report with canonical, reviewable JSON evidence."""

    body = markdown_body.rstrip()
    payload = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    return f"{body}\n\n{_BLOCK_START}{payload}{_BLOCK_END}\n"


def validate_evidence(evidence: Mapping[str, object], context: GateContext) -> None:
    """Fail closed unless the report proves the bounded S3 Core claim."""

    if evidence.get("schema_version") != _SCHEMA_VERSION:
        raise GateEvidenceError(f"schema_version must be {_SCHEMA_VERSION}")
    if evidence.get("status") != "passed":
        raise GateEvidenceError("status must be passed")

    source_commit = evidence.get("source_commit")
    if not isinstance(source_commit, str) or not _COMMIT.fullmatch(source_commit):
        raise GateEvidenceError("source_commit must be a full Git commit")
    if source_commit not in context.accepted_source_commits:
        raise GateEvidenceError("source_commit does not match the reviewed source state")

    unknown_dirty = sorted(set(context.dirty_paths) - set(context.allowed_dirty_paths))
    if unknown_dirty:
        raise GateEvidenceError(f"dirty unknown file: {', '.join(unknown_dirty)}")

    scope = _mapping(evidence.get("scope"), "scope")
    expected_scope = {
        "durability": "sqlite_single_node",
        "observability": "correlation_only",
        "notification_delivery": "internal_only",
        "replication": "none",
    }
    for field, expected in expected_scope.items():
        if scope.get(field) != expected:
            raise GateEvidenceError(f"scope.{field} must be {expected}")

    commands_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_command in enumerate(_sequence(evidence.get("commands"), "commands")):
        owner = f"commands[{index}]"
        command = _mapping(raw_command, owner)
        command_id = _required(command, "id", owner)
        if not isinstance(command_id, str) or not command_id:
            raise GateEvidenceError(f"{owner}.id must be non-empty")
        if command_id in commands_by_id:
            raise GateEvidenceError(f"duplicate command id: {command_id}")
        command_text = _required(command, "command", owner)
        if not isinstance(command_text, str) or not command_text.strip():
            raise GateEvidenceError(f"{owner}.command must be non-empty")
        counts = _mapping(_required(command, "counts", owner), f"{owner}.counts")
        for key in _COUNT_KEYS:
            _non_negative_int(_required(counts, key, f"{owner}.counts"), f"{owner}.counts.{key}")
        if sum(counts[key] for key in _COUNT_KEYS) == 0 or counts["passed"] == 0:
            raise GateEvidenceError(f"{owner}.counts must record executed passing checks")
        _sha256(_required(command, "output_sha256", owner), f"{owner}.output_sha256")
        if command.get("exit_code") != 0 or counts["failed"] != 0:
            raise GateEvidenceError(f"{owner} did not pass")
        commands_by_id[command_id] = command
    missing_commands = sorted(_REQUIRED_COMMANDS.keys() - commands_by_id.keys())
    if missing_commands:
        raise GateEvidenceError(f"missing required command: {', '.join(missing_commands)}")
    for command_id, expected_command in _REQUIRED_COMMANDS.items():
        if commands_by_id[command_id]["command"] != expected_command:
            raise GateEvidenceError(f"{command_id}.command does not match the Gate contract")
    focused_counts = _mapping(commands_by_id["focused_fault_matrix"]["counts"], "focused counts")
    if focused_counts["skipped"] != 0:
        raise GateEvidenceError("focused_fault_matrix skipped required faults")

    faults_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_fault in enumerate(_sequence(evidence.get("faults"), "faults")):
        owner = f"faults[{index}]"
        fault = _mapping(raw_fault, owner)
        fault_id = _required(fault, "id", owner)
        if not isinstance(fault_id, str) or fault_id not in _REQUIRED_FAULTS:
            raise GateEvidenceError(f"{owner}.id is not a required fault")
        if fault_id in faults_by_id:
            raise GateEvidenceError(f"duplicate fault id: {fault_id}")
        expected_path = _REQUIRED_FAULTS[fault_id]
        if fault.get("test_path") != expected_path:
            raise GateEvidenceError(f"{fault_id}.test_path must be {expected_path}")
        if fault.get("status") != "passed":
            raise GateEvidenceError(f"{fault_id} was skipped or failed")
        source_record = {
            "source_path": expected_path,
            "source_sha256": _required(fault, "source_sha256", owner),
        }
        _validate_source(source_record, fault_id, context)
        faults_by_id[fault_id] = fault
    missing_faults = sorted(_REQUIRED_FAULTS - faults_by_id.keys())
    if missing_faults:
        raise GateEvidenceError(f"missing required fault: {', '.join(missing_faults)}")

    bundle = _mapping(evidence.get("bundle_validation"), "bundle_validation")
    schema_path = "docs/reference/evidence-bundle-v1.schema.json"
    if bundle.get("status") != "passed" or bundle.get("schema_path") != schema_path:
        raise GateEvidenceError("bundle_validation is not passed against the published schema")
    schema_hash = _sha256(
        _required(bundle, "schema_sha256", "bundle_validation"),
        "bundle_validation.schema_sha256",
    )
    if context.source_hashes.get(schema_path) != schema_hash:
        raise GateEvidenceError("bundle_validation.schema_sha256 does not match the schema")
    _positive_int(bundle.get("valid_bundle_count"), "bundle_validation.valid_bundle_count")
    _positive_int(bundle.get("invalid_bundle_cases"), "bundle_validation.invalid_bundle_cases")
    if bundle.get("artifact_hashes_verified") is not True:
        raise GateEvidenceError("bundle_validation artifact hashes were not verified")

    managed_effects = _mapping(evidence.get("managed_effects"), "managed_effects")
    if (
        managed_effects.get("status") != "passed"
        or _positive_int(managed_effects.get("effective_count"), "managed_effects.effective_count")
        < 1
        or managed_effects.get("duplicate_effective_count") != 0
    ):
        raise GateEvidenceError("managed_effects contains duplicate effective work")
    _validate_source(managed_effects, "managed_effects", context)

    fencing = _mapping(evidence.get("fencing"), "fencing")
    if fencing.get("status") != "passed" or fencing.get("stale_success_count") != 0:
        raise GateEvidenceError("fencing records a stale-authority success")
    _validate_source(fencing, "fencing", context)

    decision = _mapping(evidence.get("decision_recovery"), "decision_recovery")
    if decision.get("status") != "passed":
        raise GateEvidenceError("decision_recovery must be passed")
    _positive_int(decision.get("recovered_count"), "decision_recovery.recovered_count")
    _validate_source(decision, "decision_recovery", context)


def _hash_path(path: Path) -> str:
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        raise GateEvidenceError(f"evidence source does not exist: {path}")
    digest = hashlib.sha256()
    for candidate in sorted(path.rglob("*")):
        if (
            not candidate.is_file()
            or "__pycache__" in candidate.parts
            or candidate.suffix == ".pyc"
        ):
            continue
        relative = candidate.relative_to(path).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        payload = candidate.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _repository_context(
    repo_root: Path,
    report_path: Path,
    source_commit: object,
) -> GateContext:
    try:
        report_relative = report_path.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise GateEvidenceError("report must be inside the repository") from exc
    allowed_dirty = tuple(sorted(set((*_DEFAULT_ALLOWED_DIRTY, report_relative))))
    backend = GitBackend()
    location = GitLocation(repo_root)
    head = backend.resolve_commit(location, "HEAD")
    accepted = [head]
    try:
        parent = backend.resolve_parent_commit(location)
    except GitBackendError:
        parent = ""
    if isinstance(source_commit, str) and source_commit == parent:
        changed = set(backend.changed_paths_between(location, parent, head))
        if changed <= set(allowed_dirty):
            accepted.append(parent)
    source_paths = set(_REQUIRED_FAULTS.values())
    source_paths.add("docs/reference/evidence-bundle-v1.schema.json")
    return GateContext(
        accepted_source_commits=tuple(accepted),
        dirty_paths=backend.worktree_status_paths(location),
        allowed_dirty_paths=allowed_dirty,
        source_hashes={path: _hash_path(repo_root / path) for path in sorted(source_paths)},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    report_path = args.report if args.report.is_absolute() else repo_root / args.report
    try:
        evidence = parse_report(report_path.read_text(encoding="utf-8"))
        context = _repository_context(
            repo_root, report_path.resolve(), evidence.get("source_commit")
        )
        validate_evidence(evidence, context)
    except (GateEvidenceError, GitBackendError, OSError) as exc:
        print(json.dumps({"error": str(exc), "status": "failed"}, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "report": report_path.relative_to(repo_root).as_posix(),
                "schema_version": _SCHEMA_VERSION,
                "source_commit": evidence["source_commit"],
                "status": "passed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
