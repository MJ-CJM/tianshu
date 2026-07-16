from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from scripts.check_s3_core_evidence import (
    GateContext,
    GateEvidenceError,
    parse_report,
    render_report,
    validate_evidence,
)

_COMMIT = "a" * 40
_HASH = hashlib.sha256(b"verified evidence").hexdigest()
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


def _context(*, dirty_paths: tuple[str, ...] = ()) -> GateContext:
    hashes = {path: _HASH for path in _REQUIRED_FAULTS.values()}
    hashes["docs/reference/evidence-bundle-v1.schema.json"] = _HASH
    return GateContext(
        accepted_source_commits=(_COMMIT,),
        dirty_paths=dirty_paths,
        allowed_dirty_paths=(
            "docs/cc-fable-v1/reports/s3-core-governance-report.md",
            "docs/launch/capability-matrix.md",
            "docs/cc-fable-v1/PROGRESS.md",
        ),
        source_hashes=hashes,
    )


def _valid_evidence() -> dict[str, object]:
    commands = [
        {
            "id": command_id,
            "command": command,
            "exit_code": 0,
            "counts": {
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "deselected": 0,
            },
            "output_sha256": _HASH,
        }
        for command_id, command in _REQUIRED_COMMANDS.items()
    ]
    return {
        "schema_version": "s3-core-gate-v1",
        "status": "passed",
        "source_commit": _COMMIT,
        "scope": {
            "durability": "sqlite_single_node",
            "observability": "correlation_only",
            "notification_delivery": "internal_only",
            "replication": "none",
        },
        "commands": commands,
        "faults": [
            {
                "id": fault_id,
                "test_path": test_path,
                "status": "passed",
                "source_sha256": _HASH,
            }
            for fault_id, test_path in _REQUIRED_FAULTS.items()
        ],
        "bundle_validation": {
            "status": "passed",
            "schema_path": "docs/reference/evidence-bundle-v1.schema.json",
            "schema_sha256": _HASH,
            "valid_bundle_count": 1,
            "invalid_bundle_cases": 1,
            "artifact_hashes_verified": True,
        },
        "managed_effects": {
            "status": "passed",
            "effective_count": 1,
            "duplicate_effective_count": 0,
            "source_path": "tests/integration/test_side_effect_idempotency.py",
            "source_sha256": _HASH,
        },
        "fencing": {
            "status": "passed",
            "stale_success_count": 0,
            "source_path": "tests/integration/test_claim_lease_recovery.py",
            "source_sha256": _HASH,
        },
        "decision_recovery": {
            "status": "passed",
            "recovered_count": 1,
            "source_path": "tests/integration/test_decision_service_restart_race.py",
            "source_sha256": _HASH,
        },
    }


def test_required_fault_sources_exist() -> None:
    repository = Path(__file__).parents[2]

    assert all((repository / path).exists() for path in _REQUIRED_FAULTS.values())


def test_valid_report_round_trips_deterministically() -> None:
    evidence = _valid_evidence()

    rendered = render_report("# S3 Core Governance Gate\n", evidence)

    assert render_report("# S3 Core Governance Gate\n", parse_report(rendered)) == rendered
    validate_evidence(parse_report(rendered), _context())


@pytest.mark.parametrize("field", ["command", "counts", "output_sha256"])
def test_rejects_missing_command_count_or_hash(field: str) -> None:
    evidence = _valid_evidence()
    del evidence["commands"][0][field]  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match=field):
        validate_evidence(evidence, _context())


def test_rejects_missing_required_command() -> None:
    evidence = _valid_evidence()
    evidence["commands"] = evidence["commands"][1:]  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="focused_fault_matrix"):
        validate_evidence(evidence, _context())


def test_rejects_command_that_does_not_match_gate_contract() -> None:
    evidence = _valid_evidence()
    evidence["commands"][0]["command"] = "pytest a smaller subset"  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="Gate contract"):
        validate_evidence(evidence, _context())


def test_rejects_wrong_source_commit() -> None:
    evidence = _valid_evidence()
    evidence["source_commit"] = "b" * 40

    with pytest.raises(GateEvidenceError, match="source_commit"):
        validate_evidence(evidence, _context())


def test_rejects_dirty_unknown_file() -> None:
    with pytest.raises(GateEvidenceError, match="src/unknown.py"):
        validate_evidence(_valid_evidence(), _context(dirty_paths=("src/unknown.py",)))


def test_allows_only_report_dirty_paths() -> None:
    validate_evidence(
        _valid_evidence(),
        _context(dirty_paths=("docs/launch/capability-matrix.md",)),
    )


def test_rejects_skipped_required_fault() -> None:
    evidence = _valid_evidence()
    evidence["faults"][0]["status"] = "skipped"  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="idempotent_submission"):
        validate_evidence(evidence, _context())


def test_rejects_broken_bundle() -> None:
    evidence = _valid_evidence()
    evidence["bundle_validation"]["artifact_hashes_verified"] = False  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="bundle_validation"):
        validate_evidence(evidence, _context())


def test_rejects_duplicate_effective_managed_effect() -> None:
    evidence = _valid_evidence()
    evidence["managed_effects"]["duplicate_effective_count"] = 1  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="managed_effects"):
        validate_evidence(evidence, _context())


def test_rejects_stale_fencing_success() -> None:
    evidence = _valid_evidence()
    evidence["fencing"]["stale_success_count"] = 1  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="fencing"):
        validate_evidence(evidence, _context())


def test_rejects_missing_decision_recovery() -> None:
    evidence = _valid_evidence()
    del evidence["decision_recovery"]

    with pytest.raises(GateEvidenceError, match="decision_recovery"):
        validate_evidence(evidence, _context())


@pytest.mark.parametrize(
    ("field", "claim"),
    [
        ("observability", "full_otel"),
        ("notification_delivery", "external"),
        ("replication", "multi_replica"),
    ],
)
def test_rejects_forbidden_claims(field: str, claim: str) -> None:
    evidence = copy.deepcopy(_valid_evidence())
    evidence["scope"][field] = claim  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match=field):
        validate_evidence(evidence, _context())


def test_rejects_fault_source_hash_mismatch() -> None:
    evidence = _valid_evidence()
    evidence["faults"][0]["source_sha256"] = hashlib.sha256(b"other").hexdigest()  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="source_sha256"):
        validate_evidence(evidence, _context())
