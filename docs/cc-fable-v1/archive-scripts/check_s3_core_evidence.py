#!/usr/bin/env python3
"""Validate retained S3 Core Governance Gate evidence against repository facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation

_SCHEMA_VERSION = "s3-core-gate-v1"
_BLOCK_START = "<!-- s3-core-evidence:v1 -->\n```json\n"
_BLOCK_END = "\n```\n<!-- /s3-core-evidence:v1 -->"
_LOG_HEADER = "S3_CORE_GATE_LOG_V1"
_OUTPUT_MARKER = "--- output ---\n"
_EXIT_MARKER = "--- exit ---\n"
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
    "notifier_all": "env -u VIRTUAL_ENV .venv/bin/python -m pytest tests/notifier -q",
    "ruff_check": ".venv/bin/ruff check src tests",
    "ruff_format_check": ".venv/bin/ruff format --check src tests",
    "mypy": ".venv/bin/mypy",
    "import_linter": ".venv/bin/lint-imports",
    "full_non_slow": ('env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q'),
}
_EVIDENCE_DIRECTORY = "docs/cc-fable-v1/reports/s3-core-evidence"
_REQUIRED_LOGS = {
    command_id: f"{_EVIDENCE_DIRECTORY}/{command_id}.log" for command_id in _REQUIRED_COMMANDS
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
_GOVERNANCE_DOCS = (
    "docs/cc-fable-v1/reports/s3-core-governance-report.md",
    "docs/launch/capability-matrix.md",
    "docs/cc-fable-v1/PROGRESS.md",
)
_DEFAULT_ALLOWED_DIRTY = (*_GOVERNANCE_DOCS, *_REQUIRED_LOGS.values())
_ALLOWED_NEGATIVE_BOUNDARY = re.compile(
    r"(?:does\s+not\s+claim|"
    r"(?:is|are|remain|remains)\s+(?:explicitly\s+)?(?:deferred|unsupported|"
    r"not\s+supported|not\s+guaranteed|not\s+claimed|out\s+of\s+scope)|"
    r"(?:is|are)\s+not\s+in\s+(?:this|the)\s+Gate|"
    r"\|\s*\*{0,2}(?:Deferred|Not\s+claimed|Unsupported)\b|"
    r"不保证|不承诺|尚不支持|未支持|仍属延期|保持延期|"
    r"不属于|不在.{0,20}保证内)",
    re.IGNORECASE,
)
_NEGATION_REVERSAL = re.compile(
    r"(?:\bno\s+longer\s+deferred\b|\bnot\s+unsupported\b|"
    r"不再延期|并非不受支持)",
    re.IGNORECASE,
)
_INDEPENDENT_CLAUSE_SPLIT = re.compile(
    r"[;；](?!\s*(?:but\b|however\b|yet\b|但是|但|然而))", re.IGNORECASE
)
_ADVERSATIVE_SPLIT = re.compile(
    r"\s*(?:[,，;；]\s*)?(?:but\b|however\b|yet\b|但是|但|然而)\s*[,，]?\s*",
    re.IGNORECASE,
)
_STATUS_ASSERTION = re.compile(
    r"\b(?:support(?:s|ed|ing)?|provide(?:s|d)?|guarantee(?:s|d)?|"
    r"claim(?:s|ed|ing)?|defer(?:s|red|ring)?)\b|支持|保证|承诺|延期",
    re.IGNORECASE,
)
_MARKDOWN_STANDALONE_LINE = re.compile(
    r"^(?:#{1,6}\s|\| |```|<!--|===|[A-Za-z][^:\n]{0,80}:)",
)
_MARKDOWN_LIST_LINE = re.compile(r"^[-*+]\s")
_FORBIDDEN_TOPICS = {
    "full OTel": re.compile(
        r"(?:full|complete)[-\s]+(?:OTel|OpenTelemetry)|"
        r"(?:完整|全量).{0,12}(?:OTel|OpenTelemetry)|"
        r"(?:OTel|OpenTelemetry).{0,12}(?:完整|全量)",
        re.IGNORECASE | re.DOTALL,
    ),
    "external notification delivery": re.compile(
        r"(?=[\s\S]*\bexternal\b)(?=[\s\S]*\b(?:notification|channel|message)s?\b)"
        r"(?=[\s\S]*\bdeliver(?:y|ed)\b)|"
        r"(?=[\s\S]*外部)(?=[\s\S]*(?:通知|消息|渠道))(?=[\s\S]*(?:送达|交付))",
        re.IGNORECASE | re.DOTALL,
    ),
    "multi-replica governance": re.compile(
        r"\bmulti[- ]replica\b|\bmultiple\s+replicas\b|多副本|多个副本",
        re.IGNORECASE,
    ),
}


class GateEvidenceError(ValueError):
    """The report cannot support the bounded S3 Core Gate claim."""


def _iter_markdown_blocks(content: str) -> Iterable[tuple[int, str]]:
    """Yield prose blocks without joining distinct Markdown records."""

    pending: list[str] = []
    pending_offset = 0
    pending_is_list = False
    offset = 0

    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        is_blank = not stripped.strip()
        is_standalone = bool(_MARKDOWN_STANDALONE_LINE.match(stripped))
        is_list_start = bool(_MARKDOWN_LIST_LINE.match(stripped))
        is_indented_continuation = pending_is_list and line[:1].isspace()

        if is_blank:
            if pending:
                yield pending_offset, "".join(pending).rstrip()
                pending = []
                pending_is_list = False
            offset += len(line)
            continue

        if is_indented_continuation:
            pending.append(line)
            offset += len(line)
            continue

        if pending and (pending_is_list or is_list_start or is_standalone):
            yield pending_offset, "".join(pending).rstrip()
            pending = []
            pending_is_list = False

        if is_standalone:
            yield offset, stripped
            offset += len(line)
            continue

        if not pending:
            pending_offset = offset
            pending_is_list = is_list_start
        pending.append(line)
        offset += len(line)

    if pending:
        yield pending_offset, "".join(pending).rstrip()


@dataclass(frozen=True)
class GateContext:
    """Live repository facts used to prevent stale or unrelated evidence."""

    accepted_source_commits: tuple[str, ...]
    dirty_paths: tuple[str, ...]
    allowed_dirty_paths: tuple[str, ...]
    evidence_paths: tuple[str, ...]
    source_hashes: Mapping[str, str]
    log_bytes: Mapping[str, bytes]


@dataclass(frozen=True)
class _LogResult:
    source_commit: str
    command: str
    exit_code: int
    counts: Mapping[str, int]


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


def _exact_keys(record: Mapping[str, Any], expected: set[str], owner: str) -> None:
    missing = sorted(expected - record.keys())
    extra = sorted(record.keys() - expected)
    if missing:
        raise GateEvidenceError(f"{owner}.{missing[0]} is required")
    if extra:
        raise GateEvidenceError(f"{owner}.{extra[0]} is not allowed")


def _sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value) or value == "0" * 64:
        raise GateEvidenceError(f"{field} must be a non-zero SHA-256")
    return value


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GateEvidenceError(f"{field} must be a non-negative integer")
    return value


def _canonical_json(evidence: Mapping[str, object]) -> str:
    return json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)


def parse_report(content: str) -> dict[str, object]:
    """Extract one evidence block and reject noncanonical JSON bytes."""

    if content.count(_BLOCK_START) != 1 or content.count(_BLOCK_END) != 1:
        raise GateEvidenceError("report must contain exactly one S3 evidence block")
    start = content.index(_BLOCK_START) + len(_BLOCK_START)
    end = content.index(_BLOCK_END, start)
    payload = content[start:end]
    try:
        evidence = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise GateEvidenceError("S3 evidence block is not valid JSON") from exc
    if not isinstance(evidence, dict):
        raise GateEvidenceError("S3 evidence block must be a JSON object")
    if payload != _canonical_json(evidence):
        raise GateEvidenceError("S3 evidence block must use canonical JSON")
    return evidence


def render_report(markdown_body: str, evidence: Mapping[str, object]) -> str:
    """Render a stable Markdown report with canonical, reviewable JSON evidence."""

    body = markdown_body.rstrip()
    return f"{body}\n\n{_BLOCK_START}{_canonical_json(evidence)}{_BLOCK_END}\n"


def render_log(*, source_commit: str, command: str, output: bytes, exit_code: int) -> bytes:
    """Render a retained raw command log with source, command, and exit bindings."""

    if not _COMMIT.fullmatch(source_commit):
        raise GateEvidenceError("log source_commit must be a full Git commit")
    if "\n" in command or "\r" in command:
        raise GateEvidenceError("log command must occupy one line")
    if not output.endswith(b"\n"):
        output += b"\n"
    prefix = (
        f"{_LOG_HEADER}\nsource_commit={source_commit}\ncommand={command}\n{_OUTPUT_MARKER}"
    ).encode()
    suffix = f"{_EXIT_MARKER}exit_code={exit_code}\n".encode()
    return prefix + output + suffix


def _pytest_counts(output: str, owner: str) -> Mapping[str, int]:
    summary = None
    for line in reversed(output.splitlines()):
        normalized = line.strip().strip("=").strip()
        if re.search(r"\b\d+\s+passed\b", normalized) and re.search(
            r"\bin\s+\d+(?:\.\d+)?s(?:\s+\([^)]+\))?$", normalized
        ):
            summary = normalized
            break
    if summary is None:
        raise GateEvidenceError(f"{owner} log has no terminal pytest success summary")
    pairs = {
        key: int(value)
        for value, key in re.findall(r"(\d+)\s+(passed|failed|skipped|deselected)\b", summary)
    }
    return {key: pairs.get(key, 0) for key in _COUNT_KEYS}


def _command_counts(command_id: str, output: str) -> Mapping[str, int]:
    owner = f"{command_id} retained"
    if command_id in {"focused_fault_matrix", "notifier_all", "full_non_slow"}:
        return _pytest_counts(output, owner)
    if command_id == "ruff_check":
        if "All checks passed!" not in output:
            raise GateEvidenceError(f"{owner} log has no Ruff success summary")
        passed, failed = 1, 0
    elif command_id == "ruff_format_check":
        match = re.search(r"\b(\d+) files? already formatted\b", output)
        if match is None:
            raise GateEvidenceError(f"{owner} log has no Ruff format success summary")
        passed, failed = int(match.group(1)), 0
    elif command_id == "mypy":
        match = re.search(r"Success: no issues found in (\d+) source files?", output)
        if match is None:
            raise GateEvidenceError(f"{owner} log has no mypy success summary")
        passed, failed = int(match.group(1)), 0
    elif command_id == "import_linter":
        match = re.search(r"Contracts:\s*(\d+) kept,\s*(\d+) broken\.", output)
        if match is None:
            raise GateEvidenceError(f"{owner} log has no import-linter success summary")
        passed, failed = int(match.group(1)), int(match.group(2))
    else:  # pragma: no cover - caller is bounded by _REQUIRED_COMMANDS
        raise GateEvidenceError(f"unknown retained command: {command_id}")
    return {"passed": passed, "failed": failed, "skipped": 0, "deselected": 0}


def _parse_log(command_id: str, payload: bytes) -> _LogResult:
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateEvidenceError(f"{command_id} retained log must be UTF-8") from exc
    prefix, marker, tail = content.partition(_OUTPUT_MARKER)
    if not marker:
        raise GateEvidenceError(f"{command_id} retained log is missing output marker")
    output, exit_marker, exit_record = tail.rpartition(_EXIT_MARKER)
    if not exit_marker or not exit_record.endswith("\n"):
        raise GateEvidenceError(f"{command_id} retained log is missing exit evidence")
    prefix_lines = prefix.splitlines()
    if len(prefix_lines) != 3 or prefix_lines[0] != _LOG_HEADER:
        raise GateEvidenceError(f"{command_id} retained log header is invalid")
    if not prefix_lines[1].startswith("source_commit=") or not prefix_lines[2].startswith(
        "command="
    ):
        raise GateEvidenceError(f"{command_id} retained log bindings are invalid")
    source_commit = prefix_lines[1].removeprefix("source_commit=")
    command = prefix_lines[2].removeprefix("command=")
    exit_match = re.fullmatch(r"exit_code=(-?\d+)\n", exit_record)
    if exit_match is None:
        raise GateEvidenceError(f"{command_id} retained log exit evidence is invalid")
    return _LogResult(
        source_commit=source_commit,
        command=command,
        exit_code=int(exit_match.group(1)),
        counts=_command_counts(command_id, output),
    )


def validate_documents(documents: Mapping[str, str]) -> None:
    """Reject unbounded positive S3 claims in every supplied governance document."""

    for path, content in documents.items():
        content = content.rstrip()
        if not content:
            continue
        segments: list[tuple[int, str]] = []
        for paragraph_offset, paragraph_text in _iter_markdown_blocks(content):
            for sentence in re.finditer(
                r"\S.*?(?:[.!?。！？](?=\s|\Z)|\Z)", paragraph_text, re.DOTALL
            ):
                segments.append((paragraph_offset + sentence.start(), sentence.group()))
        for offset, segment in segments:
            line_number = content.count("\n", 0, offset) + 1
            claim_text = re.sub(r"\]\([^)]+\)", "]", segment)
            for statement in _INDEPENDENT_CLAUSE_SPLIT.split(claim_text):
                statement_topics = {
                    topic
                    for topic, pattern in _FORBIDDEN_TOPICS.items()
                    if pattern.search(statement)
                }
                adversative_clauses = _ADVERSATIVE_SPLIT.split(statement)
                for clause in adversative_clauses:
                    clause_topics = {
                        topic
                        for topic, pattern in _FORBIDDEN_TOPICS.items()
                        if pattern.search(clause)
                    }
                    if (
                        not clause_topics
                        and len(adversative_clauses) > 1
                        and _STATUS_ASSERTION.search(clause)
                    ):
                        clause_topics = statement_topics
                    for topic in clause_topics:
                        if _NEGATION_REVERSAL.search(
                            clause
                        ) or not _ALLOWED_NEGATIVE_BOUNDARY.search(clause):
                            raise GateEvidenceError(
                                f"{path}:{line_number} makes forbidden positive {topic} claim"
                            )


def _validate_source(record: Mapping[str, Any], owner: str, context: GateContext) -> None:
    path = _required(record, "test_path", owner)
    if not isinstance(path, str) or not path:
        raise GateEvidenceError(f"{owner}.test_path must be non-empty")
    actual = context.source_hashes.get(path)
    claimed = _sha256(_required(record, "source_sha256", owner), f"{owner}.source_sha256")
    if actual is None or claimed != actual:
        raise GateEvidenceError(f"{owner}.source_sha256 does not match {path}")


def validate_evidence(evidence: Mapping[str, object], context: GateContext) -> None:
    """Fail closed unless retained logs and source hashes prove the bounded claim."""

    _exact_keys(
        evidence,
        {"schema_version", "status", "source_commit", "scope", "commands", "faults"},
        "evidence",
    )
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
    _exact_keys(scope, set(expected_scope), "scope")
    for field, expected in expected_scope.items():
        if scope.get(field) != expected:
            raise GateEvidenceError(f"scope.{field} must be {expected}")

    commands_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_command in enumerate(_sequence(evidence.get("commands"), "commands")):
        owner = f"commands[{index}]"
        command = _mapping(raw_command, owner)
        _exact_keys(
            command,
            {"id", "command", "log_path", "log_sha256", "exit_code", "counts"},
            owner,
        )
        command_id = _required(command, "id", owner)
        if not isinstance(command_id, str) or command_id not in _REQUIRED_COMMANDS:
            raise GateEvidenceError(f"{owner}.id is not a required command")
        if command_id in commands_by_id:
            raise GateEvidenceError(f"duplicate command id: {command_id}")
        expected_command = _REQUIRED_COMMANDS[command_id]
        if command.get("command") != expected_command:
            raise GateEvidenceError(f"{command_id}.command does not match the Gate contract")
        expected_log_path = _REQUIRED_LOGS[command_id]
        if command.get("log_path") != expected_log_path:
            raise GateEvidenceError(f"{command_id}.log_path must be {expected_log_path}")
        if expected_log_path not in context.evidence_paths:
            raise GateEvidenceError(f"{command_id}.log_path is not retained for this source commit")
        payload = context.log_bytes.get(expected_log_path)
        if payload is None:
            raise GateEvidenceError(f"{command_id}.log_path is missing")
        actual_hash = hashlib.sha256(payload).hexdigest()
        claimed_hash = _sha256(command.get("log_sha256"), f"{command_id}.log_sha256")
        if claimed_hash != actual_hash:
            raise GateEvidenceError(f"{command_id}.log_sha256 does not match retained bytes")
        derived = _parse_log(command_id, payload)
        if derived.source_commit != source_commit:
            raise GateEvidenceError(f"{command_id} retained source_commit does not match report")
        if derived.command != expected_command:
            raise GateEvidenceError(f"{command_id} retained command does not match Gate contract")
        counts = _mapping(command.get("counts"), f"{owner}.counts")
        _exact_keys(counts, set(_COUNT_KEYS), f"{owner}.counts")
        normalized_counts = {
            key: _non_negative_int(counts.get(key), f"{owner}.counts.{key}") for key in _COUNT_KEYS
        }
        if normalized_counts != dict(derived.counts):
            raise GateEvidenceError(f"{command_id}.counts do not match retained log")
        exit_code = command.get("exit_code")
        if exit_code != derived.exit_code:
            raise GateEvidenceError(f"{command_id}.exit_code does not match retained log")
        if derived.exit_code != 0 or derived.counts["failed"] != 0:
            raise GateEvidenceError(f"{command_id} retained command did not pass")
        if derived.counts["passed"] == 0:
            raise GateEvidenceError(f"{command_id} retained command ran no passing checks")
        commands_by_id[command_id] = command
    missing_commands = sorted(_REQUIRED_COMMANDS.keys() - commands_by_id.keys())
    if missing_commands:
        raise GateEvidenceError(f"missing required command: {', '.join(missing_commands)}")
    if commands_by_id["focused_fault_matrix"]["counts"]["skipped"] != 0:
        raise GateEvidenceError("focused_fault_matrix skipped required faults")

    faults_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_fault in enumerate(_sequence(evidence.get("faults"), "faults")):
        owner = f"faults[{index}]"
        fault = _mapping(raw_fault, owner)
        _exact_keys(fault, {"id", "test_path", "source_sha256"}, owner)
        fault_id = _required(fault, "id", owner)
        if not isinstance(fault_id, str) or fault_id not in _REQUIRED_FAULTS:
            raise GateEvidenceError(f"{owner}.id is not a required fault")
        if fault_id in faults_by_id:
            raise GateEvidenceError(f"duplicate fault id: {fault_id}")
        expected_path = _REQUIRED_FAULTS[fault_id]
        if fault.get("test_path") != expected_path:
            raise GateEvidenceError(f"{fault_id}.test_path must be {expected_path}")
        _validate_source(fault, fault_id, context)
        faults_by_id[fault_id] = fault
    missing_faults = sorted(_REQUIRED_FAULTS.keys() - faults_by_id.keys())
    if missing_faults:
        raise GateEvidenceError(f"missing required fault: {', '.join(missing_faults)}")


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
    dirty_paths = backend.worktree_status_paths(location)
    evidence_paths = set(dirty_paths)
    try:
        parent = backend.resolve_parent_commit(location)
    except GitBackendError:
        parent = ""
    if isinstance(source_commit, str) and source_commit == parent:
        changed = set(backend.changed_paths_between(location, parent, head))
        if changed <= set(allowed_dirty):
            accepted.append(parent)
            evidence_paths.update(changed)
    source_paths = set(_REQUIRED_FAULTS.values())
    log_bytes: dict[str, bytes] = {}
    for log_path in _REQUIRED_LOGS.values():
        candidate = repo_root / log_path
        if candidate.is_file():
            log_bytes[log_path] = candidate.read_bytes()
    return GateContext(
        accepted_source_commits=tuple(accepted),
        dirty_paths=dirty_paths,
        allowed_dirty_paths=allowed_dirty,
        evidence_paths=tuple(sorted(evidence_paths)),
        source_hashes={path: _hash_path(repo_root / path) for path in sorted(source_paths)},
        log_bytes=log_bytes,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    report_path = args.report if args.report.is_absolute() else repo_root / args.report
    try:
        report_content = report_path.read_text(encoding="utf-8")
        evidence = parse_report(report_content)
        documents = {
            path: (
                report_content
                if (repo_root / path).resolve() == report_path.resolve()
                else (repo_root / path).read_text(encoding="utf-8")
            )
            for path in _GOVERNANCE_DOCS
        }
        validate_documents(documents)
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
