from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from scripts.check_s3_core_evidence import (
    GateContext,
    GateEvidenceError,
    parse_report,
    render_log,
    render_report,
    validate_documents,
    validate_evidence,
)

_COMMIT = "a" * 40
_HASH = hashlib.sha256(b"verified evidence").hexdigest()
_EVIDENCE_DIRECTORY = "docs/cc-fable-v1/reports/s3-core-evidence"
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
    "notifier_all": "env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/notifier -q",
    "ruff_check": ".venv/bin/ruff check src tests",
    "ruff_format_check": ".venv/bin/ruff format --check src tests",
    "mypy": ".venv/bin/mypy",
    "import_linter": ".venv/bin/lint-imports",
    "full_non_slow": ('env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q'),
}
_LOG_PATHS = {
    command_id: f"{_EVIDENCE_DIRECTORY}/{command_id}.log" for command_id in _REQUIRED_COMMANDS
}
_OUTPUTS = {
    "focused_fault_matrix": b"133 passed, 4 warnings in 6.08s\n",
    "notifier_all": b"14 passed, 4 warnings in 0.67s\n",
    "ruff_check": b"All checks passed!\n",
    "ruff_format_check": b"824 files already formatted\n",
    "mypy": b"Success: no issues found in 125 source files\n",
    "import_linter": b"Contracts: 2 kept, 0 broken.\n",
    "full_non_slow": b"3759 passed, 2 skipped, 24 deselected, 7 warnings in 619.25s (0:10:19)\n",
}
_COUNTS = {
    "focused_fault_matrix": {"passed": 133, "failed": 0, "skipped": 0, "deselected": 0},
    "notifier_all": {"passed": 14, "failed": 0, "skipped": 0, "deselected": 0},
    "ruff_check": {"passed": 1, "failed": 0, "skipped": 0, "deselected": 0},
    "ruff_format_check": {"passed": 824, "failed": 0, "skipped": 0, "deselected": 0},
    "mypy": {"passed": 125, "failed": 0, "skipped": 0, "deselected": 0},
    "import_linter": {"passed": 2, "failed": 0, "skipped": 0, "deselected": 0},
    "full_non_slow": {"passed": 3759, "failed": 0, "skipped": 2, "deselected": 24},
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
_GOVERNANCE_DOCUMENTS = (
    "docs/cc-fable-v1/reports/s3-core-governance-report.md",
    "docs/launch/capability-matrix.md",
    "docs/cc-fable-v1/PROGRESS.md",
)


def _logs() -> dict[str, bytes]:
    return {
        _LOG_PATHS[command_id]: render_log(
            source_commit=_COMMIT,
            command=command,
            output=_OUTPUTS[command_id],
            exit_code=0,
        )
        for command_id, command in _REQUIRED_COMMANDS.items()
    }


def _context(
    *,
    dirty_paths: tuple[str, ...] = (),
    evidence_paths: tuple[str, ...] | None = None,
    logs: dict[str, bytes] | None = None,
) -> GateContext:
    retained = _logs() if logs is None else logs
    hashes = {path: _HASH for path in _REQUIRED_FAULTS.values()}
    return GateContext(
        accepted_source_commits=(_COMMIT,),
        dirty_paths=dirty_paths,
        allowed_dirty_paths=(
            "docs/cc-fable-v1/reports/s3-core-governance-report.md",
            "docs/launch/capability-matrix.md",
            "docs/cc-fable-v1/PROGRESS.md",
            *_LOG_PATHS.values(),
        ),
        evidence_paths=tuple(_LOG_PATHS.values()) if evidence_paths is None else evidence_paths,
        source_hashes=hashes,
        log_bytes=retained,
    )


def _valid_evidence() -> dict[str, object]:
    logs = _logs()
    commands = [
        {
            "id": command_id,
            "command": command,
            "log_path": _LOG_PATHS[command_id],
            "log_sha256": hashlib.sha256(logs[_LOG_PATHS[command_id]]).hexdigest(),
            "exit_code": 0,
            "counts": copy.deepcopy(_COUNTS[command_id]),
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
            {"id": fault_id, "test_path": test_path, "source_sha256": _HASH}
            for fault_id, test_path in _REQUIRED_FAULTS.items()
        ],
    }


def _command(evidence: dict[str, object], command_id: str) -> dict[str, object]:
    commands = evidence["commands"]
    assert isinstance(commands, list)
    return next(command for command in commands if command["id"] == command_id)


def test_required_fault_sources_exist() -> None:
    repository = Path(__file__).parents[2]

    assert all((repository / path).exists() for path in _REQUIRED_FAULTS.values())


def test_valid_report_round_trips_and_derives_every_command() -> None:
    evidence = _valid_evidence()
    rendered = render_report("# S3 Core Governance Gate\n", evidence)

    assert render_report("# S3 Core Governance Gate\n", parse_report(rendered)) == rendered
    validate_evidence(parse_report(rendered), _context())


@pytest.mark.parametrize("field", ["command", "counts", "log_path", "log_sha256", "exit_code"])
def test_rejects_missing_command_evidence_field(field: str) -> None:
    evidence = _valid_evidence()
    del evidence["commands"][0][field]  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match=field):
        validate_evidence(evidence, _context())


def test_rejects_missing_or_extra_required_command() -> None:
    evidence = _valid_evidence()
    evidence["commands"] = evidence["commands"][1:]  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match="focused_fault_matrix"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    evidence["commands"].append(copy.deepcopy(evidence["commands"][0]))  # type: ignore[union-attr,index]
    evidence["commands"][-1]["id"] = "invented"  # type: ignore[index]
    with pytest.raises(GateEvidenceError, match="required command"):
        validate_evidence(evidence, _context())


def test_notifier_command_is_required() -> None:
    evidence = _valid_evidence()
    evidence["commands"] = [
        command
        for command in evidence["commands"]
        if command["id"] != "notifier_all"  # type: ignore[union-attr]
    ]

    with pytest.raises(GateEvidenceError, match="notifier_all"):
        validate_evidence(evidence, _context())


def test_rejects_report_or_retained_command_that_does_not_match_contract() -> None:
    evidence = _valid_evidence()
    _command(evidence, "focused_fault_matrix")["command"] = "pytest a smaller subset"
    with pytest.raises(GateEvidenceError, match="Gate contract"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    logs = _logs()
    path = _LOG_PATHS["focused_fault_matrix"]
    logs[path] = render_log(
        source_commit=_COMMIT,
        command="pytest a smaller subset",
        output=_OUTPUTS["focused_fault_matrix"],
        exit_code=0,
    )
    _command(evidence, "focused_fault_matrix")["log_sha256"] = hashlib.sha256(
        logs[path]
    ).hexdigest()
    with pytest.raises(GateEvidenceError, match="retained command"):
        validate_evidence(evidence, _context(logs=logs))


def test_rejects_missing_unretained_or_tampered_log() -> None:
    evidence = _valid_evidence()
    path = _LOG_PATHS["focused_fault_matrix"]
    logs = _logs()
    del logs[path]
    with pytest.raises(GateEvidenceError, match="missing"):
        validate_evidence(evidence, _context(logs=logs))

    with pytest.raises(GateEvidenceError, match="not retained"):
        validate_evidence(
            evidence,
            _context(evidence_paths=tuple(value for value in _LOG_PATHS.values() if value != path)),
        )

    logs = _logs()
    logs[path] += b"tampered\n"
    with pytest.raises(GateEvidenceError, match="log_sha256"):
        validate_evidence(evidence, _context(logs=logs))


def test_rejects_forged_log_hash_count_and_exit() -> None:
    evidence = _valid_evidence()
    command = _command(evidence, "focused_fault_matrix")
    command["log_sha256"] = hashlib.sha256(b"forged").hexdigest()
    with pytest.raises(GateEvidenceError, match="log_sha256"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    _command(evidence, "focused_fault_matrix")["counts"]["passed"] = 999  # type: ignore[index]
    with pytest.raises(GateEvidenceError, match="counts"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    _command(evidence, "focused_fault_matrix")["exit_code"] = 1
    with pytest.raises(GateEvidenceError, match="exit_code"):
        validate_evidence(evidence, _context())


def test_rejects_nonzero_retained_exit_even_when_report_matches() -> None:
    evidence = _valid_evidence()
    logs = _logs()
    path = _LOG_PATHS["focused_fault_matrix"]
    logs[path] = render_log(
        source_commit=_COMMIT,
        command=_REQUIRED_COMMANDS["focused_fault_matrix"],
        output=_OUTPUTS["focused_fault_matrix"],
        exit_code=1,
    )
    command = _command(evidence, "focused_fault_matrix")
    command["log_sha256"] = hashlib.sha256(logs[path]).hexdigest()
    command["exit_code"] = 1

    with pytest.raises(GateEvidenceError, match="did not pass"):
        validate_evidence(evidence, _context(logs=logs))


def test_rejects_retained_log_bound_to_other_source_commit() -> None:
    evidence = _valid_evidence()
    logs = _logs()
    path = _LOG_PATHS["focused_fault_matrix"]
    logs[path] = render_log(
        source_commit="b" * 40,
        command=_REQUIRED_COMMANDS["focused_fault_matrix"],
        output=_OUTPUTS["focused_fault_matrix"],
        exit_code=0,
    )
    _command(evidence, "focused_fault_matrix")["log_sha256"] = hashlib.sha256(
        logs[path]
    ).hexdigest()

    with pytest.raises(GateEvidenceError, match="retained source_commit"):
        validate_evidence(evidence, _context(logs=logs))


def test_rejects_wrong_source_commit_or_dirty_unknown_file() -> None:
    evidence = _valid_evidence()
    evidence["source_commit"] = "b" * 40
    with pytest.raises(GateEvidenceError, match="source_commit"):
        validate_evidence(evidence, _context())

    with pytest.raises(GateEvidenceError, match="src/unknown.py"):
        validate_evidence(_valid_evidence(), _context(dirty_paths=("src/unknown.py",)))


def test_allows_only_declared_evidence_dirty_paths() -> None:
    validate_evidence(
        _valid_evidence(),
        _context(dirty_paths=("docs/launch/capability-matrix.md",)),
    )


def test_fault_conclusions_are_only_exact_source_bindings() -> None:
    evidence = _valid_evidence()
    evidence["faults"][0]["status"] = "passed"  # type: ignore[index]
    with pytest.raises(GateEvidenceError, match="status is not allowed"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    evidence["managed_effects"] = {"status": "passed", "duplicate_effective_count": 0}
    with pytest.raises(GateEvidenceError, match="managed_effects is not allowed"):
        validate_evidence(evidence, _context())


def test_rejects_missing_fault_or_source_hash_mismatch() -> None:
    evidence = _valid_evidence()
    evidence["faults"] = evidence["faults"][1:]  # type: ignore[index]
    with pytest.raises(GateEvidenceError, match="idempotent_submission"):
        validate_evidence(evidence, _context())

    evidence = _valid_evidence()
    evidence["faults"][0]["source_sha256"] = hashlib.sha256(b"other").hexdigest()  # type: ignore[index]
    with pytest.raises(GateEvidenceError, match="source_sha256"):
        validate_evidence(evidence, _context())


@pytest.mark.parametrize(
    ("field", "claim"),
    [
        ("observability", "full_otel"),
        ("notification_delivery", "external"),
        ("replication", "multi_replica"),
    ],
)
def test_rejects_forbidden_machine_scope(field: str, claim: str) -> None:
    evidence = copy.deepcopy(_valid_evidence())
    evidence["scope"][field] = claim  # type: ignore[index]

    with pytest.raises(GateEvidenceError, match=field):
        validate_evidence(evidence, _context())


def test_rejects_noncanonical_json_block() -> None:
    rendered = render_report("# S3 Core Governance Gate\n", _valid_evidence())
    noncanonical = rendered.replace("{\n  ", "{", 1)

    with pytest.raises(GateEvidenceError, match="canonical"):
        parse_report(noncanonical)


@pytest.mark.parametrize(
    "path",
    _GOVERNANCE_DOCUMENTS,
)
@pytest.mark.parametrize(
    ("claim", "topic"),
    [
        (
            "Complete OTel coverage is no longer deferred and is fully supported.",
            "full OTel",
        ),
        ("S3 guarantees delivery to external notification channels.", "external"),
        ("S3 supports full-OTel coverage.", "full OTel"),
        ("S3 provides governance semantics across multiple replicas.", "multi-replica"),
        ("Full OpenTelemetry coverage is not unsupported.", "full OTel"),
        ("External channel delivery is guaranteed by S3.", "external"),
        ("Governance semantics are supported across multiple replicas.", "multi-replica"),
        ("S3 已完整支持 OpenTelemetry 覆盖。", "full OTel"),
        ("S3 保证外部通知渠道送达。", "external"),
        ("S3 支持跨多个副本的治理语义。", "multi-replica"),
        ("完整 OTel 覆盖不再延期，现已完全支持。", "full OTel"),
        ("完整 OpenTelemetry 覆盖并非不受支持。", "full OTel"),
        ("S3 支持全量 OTel 覆盖。", "full OTel"),
        (
            "Complete OTel remains deferred, but S3 fully supports it.",
            "full OTel",
        ),
        (
            "External notification delivery is not guaranteed in theory, but S3 guarantees "
            "it in production.",
            "external",
        ),
        (
            "Multiple replicas are not claimed by the old Gate, but S3 now supports them.",
            "multi-replica",
        ),
        (
            "Complete OTel remains deferred; however, S3 fully supports it.",
            "full OTel",
        ),
        (
            "External notification delivery is not guaranteed, yet S3 guarantees it in production.",
            "external",
        ),
        ("完整 OpenTelemetry 覆盖仍属延期，但 S3 现已完全支持。", "full OTel"),
        ("S3 不保证外部通知渠道送达，但是 S3 在生产环境保证送达。", "external"),
    ],
)
def test_rejects_positive_claim_anywhere_in_governance_docs(
    path: str, claim: str, topic: str
) -> None:
    repository = Path(__file__).parents[2]
    original = (repository / path).read_text(encoding="utf-8")
    assert original.endswith("\n")

    with pytest.raises(GateEvidenceError, match=rf"{path}.*{topic}"):
        validate_documents({path: f"{original}{claim}\n"})


def test_allows_explicitly_deferred_or_unsupported_claim_boundaries() -> None:
    validate_documents(
        {
            "report.md": (
                "Complete OTel remains deferred. Full OpenTelemetry coverage is not supported "
                "by this Gate. 完整 OpenTelemetry 覆盖仍属延期。"
            ),
            "capability-matrix.md": (
                "External notification-channel delivery is unsupported by S3. "
                "S3 不保证外部通知渠道送达。"
            ),
            "PROGRESS.md": (
                "Governance semantics across multiple replicas are not claimed. "
                "S3 不承诺跨多个副本的治理语义。"
            ),
        }
    )


def test_negative_claim_for_one_topic_does_not_mask_positive_other_topic() -> None:
    with pytest.raises(GateEvidenceError, match="full OTel"):
        validate_documents(
            {
                "report.md": (
                    "S3 provides complete OTel coverage; "
                    "external notification delivery remains deferred."
                )
            }
        )


def test_current_governance_documents_have_no_positive_scope_expansion() -> None:
    repository = Path(__file__).parents[2]

    validate_documents(
        {path: (repository / path).read_text(encoding="utf-8") for path in _GOVERNANCE_DOCUMENTS}
    )
