#!/usr/bin/env python3
"""Assemble and fail-closed verify the local Lean Preview Candidate."""

import argparse
import hashlib
import json
import re
import sys
import tarfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation
from tianshu.models.lean_preview import (
    REQUIRED_DEFERRED_WORK_IDS,
    LeanPreviewCandidateReportV1,
    lean_preview_content_hash,
)

try:
    from scripts.verify_lean_preview_evidence import (
        EvidenceVerificationError,
        verify_candidate_report,
        verify_demo_evidence,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from verify_lean_preview_evidence import (
        EvidenceVerificationError,
        verify_candidate_report,
        verify_demo_evidence,
    )

ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXTERNAL_AS_PASS = re.compile(
    r"external_pending\s*(?::|=|\|)\s*(?:passed|implemented)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class PhaseSpec:
    gate_id: str
    report_ref: str
    gate_source_commit: str
    report_commit: str
    pass_marker: str


PHASE_SPECS = {
    "s1_g1_5": PhaseSpec(
        "G1.5",
        "docs/cc-fable-v1/reports/g1.5-report.md",
        "bbf84451a40f8f3450e080c939c82fba52428271",
        "8c2303df525b05a69d1a6902c83b06c5fd50102d",
        "status: passed",
    ),
    "s2_lean": PhaseSpec(
        "S2 Lean",
        "docs/cc-fable-v1/reports/s2-lean-security-report.md",
        "bbf672e560ecd2c793a1a80d0cc262b41550a4db",
        "66e59943b91bc708344c69b895eaa8cfc3298721",
        "- status: passed",
    ),
    "s3_core": PhaseSpec(
        "S3 Core",
        "docs/cc-fable-v1/reports/s3-core-governance-report.md",
        "60d3c45b836de44b132dba186e5c9a3672592ea3",
        "2eb20d6dfd39b172f438c90aee5eaee507d8a227",
        "- status: passed",
    ),
    "s4_automation": PhaseSpec(
        "S4 Automation",
        "docs/cc-fable-v1/reports/s4-core-web-report.md",
        "303787916f1004362c3f250c07a8de179aa0885d",
        "303787916f1004362c3f250c07a8de179aa0885d",
        "s4_core_web_automation: automation_passed",
    ),
    "s5_lean_core": PhaseSpec(
        "S5 Lean Core",
        "docs/cc-fable-v1/reports/s5-lean-evolution-report.md",
        "f6777b435631ab3d5fa1aeac1a96cdbf2c424774",
        "f6777b435631ab3d5fa1aeac1a96cdbf2c424774",
        "Status: Lean Core Gate `passed`.",
    ),
}

REQUIRED_FINAL_COMMANDS = {
    "ruff_check": ".venv/bin/ruff check src tests",
    "ruff_format": ".venv/bin/ruff format --check src tests",
    "mypy": ".venv/bin/mypy",
    "import_linter": ".venv/bin/lint-imports",
    "backend_non_slow": 'env -u VIRTUAL_ENV .venv/bin/python -m pytest -m "not slow" -q',
    "packaging": (
        "env -u VIRTUAL_ENV .venv/bin/python -m pytest "
        "tests/resources/test_wheel_manifest.py "
        "tests/packaging/test_fresh_wheel_demo.py "
        "tests/launch/test_lean_preview_fresh_wheel.py -q -s"
    ),
    "web_npm_ci": "npm ci",
    "web_lint": "npm run lint",
    "web_typecheck": "npm run typecheck",
    "web_unit": "npm test -- --run",
    "web_build": "npm run build",
    "web_playwright": "npx playwright test",
}

REQUIRED_GATE_CWDS = {
    command_id: ("web" if command_id.startswith("web_") else ".")
    for command_id in REQUIRED_FINAL_COMMANDS
}
REQUIRED_GATE_ENVIRONMENTS = {
    command_id: (
        {"VIRTUAL_ENV": "unset"} if command_id in {"backend_non_slow", "packaging"} else {}
    )
    for command_id in REQUIRED_FINAL_COMMANDS
}
SDIST_BUILD_COMMAND = "python -m build --sdist --outdir dist/lean-preview-candidate"
WHEEL_BUILD_COMMAND = "python -m build --wheel --outdir ../../../from-sdist"
CANDIDATE_WHEEL_DIR = Path("dist/lean-preview-candidate/from-sdist")


def required_gate_environment(gate_id: str, *, batch_id: str, source_commit: str) -> dict[str, str]:
    """Return the exact environment context that a Gate record must bind."""

    environment = dict(REQUIRED_GATE_ENVIRONMENTS[gate_id])
    if gate_id == "packaging":
        environment.update(
            {
                "BATCH_ID": batch_id,
                "TIANSHU_LEAN_WHEEL_SOURCE_COMMIT": source_commit,
            }
        )
    return environment


_CAPABILITY_FACTS = (
    "desktop Web",
    "user_approval_pending",
    "VoiceOver",
    "external_pending",
    "full G4",
    "full G5",
    "remote MCP",
    "open stdio MCP",
    "disabled",
    "publication_status",
    "not_authorized",
)


class CandidateGateError(ValueError):
    """The supplied facts cannot support a Lean Preview Candidate."""


@dataclass(frozen=True)
class GateEvidence:
    """Verified final-Gate records derived from hashed raw logs."""

    content_hash: str
    results: dict[str, dict[str, Any]]
    wheel_sha256: str


@dataclass(frozen=True)
class BuildProvenance:
    """Verified source-to-sdist-to-Wheel provenance binding."""

    content_hash: str
    wheel_sha256: str


class PhaseReportInput:
    def __init__(
        self,
        *,
        phase_id: str,
        gate_id: str,
        report_ref: str,
        report_bytes: bytes,
        gate_source_commit: str,
        report_commit: str,
    ) -> None:
        self.phase_id = phase_id
        self.gate_id = gate_id
        self.report_ref = report_ref
        self.report_bytes = report_bytes
        self.gate_source_commit = gate_source_commit
        self.report_commit = report_commit


class CandidateContext:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    try:
        return _hash_bytes(path.read_bytes())
    except OSError as exc:
        raise CandidateGateError(f"missing candidate artifact: {path}") from exc


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _content_hash(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("content_hash", None)
    return _hash_bytes(_canonical_bytes(payload))


def _pytest_counts(text: str) -> tuple[int, int, int, int, int, str]:
    summaries = re.findall(
        r"(?m)^([^\n]*?\b(?:passed|failed|skipped|deselected|warnings?)\b[^\n]*?)"
        r"(?:\s+in\s+[0-9.]+s)?$",
        text,
    )
    if not summaries:
        raise CandidateGateError("Gate log has no pytest terminal summary")
    summary = summaries[-1].strip()

    def count(label: str) -> int:
        match = re.search(rf"\b(\d+)\s+{label}\b", summary)
        return int(match.group(1)) if match else 0

    return (
        count("passed"),
        count("failed"),
        count("skipped"),
        count("deselected"),
        count("warnings?"),
        summary,
    )


def _derived_gate_result(gate_id: str, raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-8", errors="replace")
    passed = failed = skipped = deselected = warnings = 0
    summary = ""
    if gate_id in {"backend_non_slow", "packaging"}:
        passed, failed, skipped, deselected, warnings, summary = _pytest_counts(text)
    elif gate_id == "ruff_check" and "All checks passed" in text:
        passed, summary = 1, "All checks passed"
    elif gate_id == "ruff_format":
        match = re.search(r"(\d+) files? already formatted", text)
        if match:
            passed, summary = int(match.group(1)), match.group(0)
    elif gate_id == "mypy":
        match = re.search(r"Success: no issues found in (\d+) source files", text)
        if match:
            passed, summary = int(match.group(1)), match.group(0)
    elif gate_id == "import_linter":
        match = re.search(r"Contracts:\s*(\d+) kept,\s*(\d+) broken", text)
        if match:
            passed, failed, summary = int(match.group(1)), int(match.group(2)), match.group(0)
    elif gate_id == "web_npm_ci" and "found 0 vulnerabilities" in text:
        passed, summary = 1, "npm ci completed; found 0 vulnerabilities"
    elif gate_id == "web_lint":
        match = re.search(r"\d+ problems \((\d+) errors?, (\d+) warnings?\)", text)
        if match:
            failed, warnings = int(match.group(1)), int(match.group(2))
            passed, summary = (1 if failed == 0 else 0), match.group(0)
    elif gate_id == "web_typecheck" and "tsc --noEmit" in text:
        passed, summary = 1, "tsc --noEmit completed"
    elif gate_id == "web_unit":
        match = re.search(r"Tests\s+(\d+) passed", text)
        if match:
            passed, summary = int(match.group(1)), match.group(0)
    elif gate_id == "web_build" and re.search(r"(?:✓|built)\s*built in|✓ built in", text):
        passed, summary = 1, "production build completed"
        warnings = int("Some chunks are larger than" in text)
    elif gate_id == "web_playwright":
        summaries = re.findall(r"(?m)^[^\n]*\b\d+\s+(?:passed|failed|skipped)\b[^\n]*$", text)
        if len(summaries) != 1:
            raise CandidateGateError("Playwright Gate requires exactly one terminal summary")
        summary = summaries[0].strip()
        passed_counts = [int(value) for value in re.findall(r"\b(\d+)\s+passed\b", summary)]
        failed_counts = [int(value) for value in re.findall(r"\b(\d+)\s+failed\b", summary)]
        skipped_counts = [int(value) for value in re.findall(r"\b(\d+)\s+skipped\b", summary)]
        if passed_counts != [41]:
            raise CandidateGateError("Playwright Gate requires exactly 41 passed once")
        if failed_counts not in ([], [0]):
            raise CandidateGateError("Playwright Gate requires 0 failed")
        if skipped_counts not in ([], [0]):
            raise CandidateGateError("Playwright Gate requires 0 skipped")
        passed = 41
        failed = skipped = 0
    if passed <= 0:
        raise CandidateGateError(f"required Gate needs a positive passed count: {gate_id}")
    return {
        "command": REQUIRED_FINAL_COMMANDS[gate_id],
        "exit_code": 0,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "deselected": deselected,
        "warnings": warnings,
        "required_skipped": skipped if gate_id == "packaging" else 0,
        "summary": summary,
    }


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CandidateGateError(f"{label} must be an object")
    return value


def load_gate_evidence(
    path: Path, *, artifact_root: Path, expected_source_commit: str
) -> GateEvidence:
    try:
        raw = path.read_bytes()
        manifest = _mapping(json.loads(raw), "Gate evidence manifest")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateGateError("Gate evidence manifest is missing or corrupt") from exc
    required = {
        "schema_version",
        "batch_id",
        "source_commit",
        "wheel_sha256",
        "commands",
        "content_hash",
    }
    if set(manifest) != required:
        raise CandidateGateError("Gate evidence manifest fields are not exact")
    if raw != _canonical_bytes(manifest) or manifest.get("content_hash") != _content_hash(manifest):
        raise CandidateGateError("Gate evidence manifest content hash mismatch")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("source_commit") != expected_source_commit
    ):
        raise CandidateGateError("Gate evidence source commit mismatch")
    if not isinstance(manifest.get("batch_id"), str) or not manifest["batch_id"]:
        raise CandidateGateError("Gate evidence batch id is missing")
    if not _SHA256.fullmatch(str(manifest.get("wheel_sha256"))):
        raise CandidateGateError("Gate evidence Wheel SHA-256 is invalid")
    commands = _mapping(manifest["commands"], "Gate evidence commands")
    if set(commands) != set(REQUIRED_FINAL_COMMANDS):
        raise CandidateGateError("required final Gate commands are incomplete")
    artifact_root = artifact_root.resolve()
    batch_root = path.parent.resolve()
    if not batch_root.is_relative_to(artifact_root):
        raise CandidateGateError("Gate evidence is outside the artifact root")
    results: dict[str, dict[str, Any]] = {}
    fields = {"command", "cwd", "environment", "exit_code", "log_ref", "log_sha256"}
    for gate_id, value in commands.items():
        record = _mapping(value, f"Gate evidence command {gate_id}")
        if set(record) != fields:
            raise CandidateGateError(f"Gate evidence command fields are not exact: {gate_id}")
        if (
            record["command"] != REQUIRED_FINAL_COMMANDS[gate_id]
            or record["cwd"] != REQUIRED_GATE_CWDS[gate_id]
            or record["environment"]
            != required_gate_environment(
                gate_id,
                batch_id=manifest["batch_id"],
                source_commit=manifest["source_commit"],
            )
        ):
            raise CandidateGateError(f"required Gate command context mismatch: {gate_id}")
        if record["exit_code"] != 0:
            raise CandidateGateError(f"required Gate failed: {gate_id}")
        log_ref = record["log_ref"]
        if not isinstance(log_ref, str):
            raise CandidateGateError("Gate log ref must be a string")
        log_path = (batch_root / log_ref).resolve()
        if not log_path.is_relative_to(batch_root):
            raise CandidateGateError("Gate log ref escapes its evidence batch")
        try:
            log_bytes = log_path.read_bytes()
        except OSError as exc:
            raise CandidateGateError(f"missing Gate log: {gate_id}") from exc
        if (
            not _SHA256.fullmatch(str(record["log_sha256"]))
            or _hash_bytes(log_bytes) != record["log_sha256"]
        ):
            raise CandidateGateError(f"Gate log hash mismatch: {gate_id}")
        results[gate_id] = _derived_gate_result(gate_id, log_bytes)
    _validate_command_results(results)
    return GateEvidence(
        content_hash=manifest["content_hash"],
        results=results,
        wheel_sha256=manifest["wheel_sha256"],
    )


def _verified_log(batch_root: Path, record: Mapping[str, Any], label: str) -> bytes:
    if set(record) < {"log_ref", "log_sha256"}:
        raise CandidateGateError(f"{label} build log binding is missing")
    log_ref = record["log_ref"]
    if not isinstance(log_ref, str):
        raise CandidateGateError(f"{label} build log ref is invalid")
    path = (batch_root / log_ref).resolve()
    if not path.is_relative_to(batch_root.resolve()):
        raise CandidateGateError(f"{label} build log escapes its batch")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CandidateGateError(f"missing {label} build log") from exc
    if _hash_bytes(raw) != record["log_sha256"]:
        raise CandidateGateError(f"{label} build log hash mismatch")
    return raw


def _sdist_payload(path: Path) -> tuple[str, dict[str, bytes]]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            all_members = archive.getmembers()
            if any(not member.isfile() and not member.isdir() for member in all_members):
                raise CandidateGateError("sdist contains an unsafe member type")
            members = [member for member in all_members if member.isfile()]
            roots = {PurePosixPath(member.name).parts[0] for member in members}
            if len(roots) != 1:
                raise CandidateGateError("sdist must contain a single root directory")
            root = next(iter(roots))
            if any(
                (pure := PurePosixPath(member.name)).is_absolute()
                or ".." in pure.parts
                or not pure.parts
                or pure.parts[0] != root
                for member in all_members
            ):
                raise CandidateGateError("sdist contains an unsafe member")
            payload: dict[str, bytes] = {}
            for member in members:
                pure = PurePosixPath(member.name)
                relative = PurePosixPath(*pure.parts[1:]).as_posix()
                if relative in payload:
                    raise CandidateGateError("sdist contains a duplicate member")
                extracted = archive.extractfile(member)
                if extracted is not None:
                    payload[relative] = extracted.read()
            return root, payload
    except (OSError, tarfile.TarError) as exc:
        raise CandidateGateError("sdist is missing or corrupt") from exc


def _wheel_payload(path: Path) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(path) as archive:
            payload: dict[str, bytes] = {}
            for name in archive.namelist():
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise CandidateGateError("Wheel contains an unsafe member")
                if not name.endswith("/"):
                    relative = pure.as_posix()
                    if relative in payload:
                        raise CandidateGateError("Wheel contains a duplicate member")
                    payload[relative] = archive.read(name)
            return payload
    except (OSError, zipfile.BadZipFile) as exc:
        raise CandidateGateError("Wheel is missing or corrupt") from exc


def verify_build_provenance(
    path: Path,
    *,
    artifact_root: Path,
    expected_source_commit: str,
    sdist_path: Path,
    wheel_path: Path,
    tracked_source_files: Mapping[str, bytes],
) -> BuildProvenance:
    try:
        raw = path.read_bytes()
        payload = _mapping(json.loads(raw), "build provenance")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateGateError("build provenance is missing or corrupt") from exc
    required = {
        "schema_version",
        "source_commit",
        "python_version",
        "frontend",
        "sdist",
        "wheel",
        "content_hash",
    }
    if set(payload) != required or raw != _canonical_bytes(payload):
        raise CandidateGateError("build provenance fields are not exact")
    if payload["content_hash"] != _content_hash(payload):
        raise CandidateGateError("build provenance hash mismatch")
    if payload["schema_version"] != 1 or payload["source_commit"] != expected_source_commit:
        raise CandidateGateError("build provenance source commit mismatch")
    if not isinstance(payload["python_version"], str) or not re.fullmatch(
        r"3\.12\.\d+", payload["python_version"]
    ):
        raise CandidateGateError("build provenance requires Python 3.12")
    frontend = _mapping(payload["frontend"], "build frontend")
    if frontend != {"name": "build", "version": "1.5.0"}:
        raise CandidateGateError("build frontend is not pinned")
    batch_root = path.parent.resolve()
    if not batch_root.is_relative_to(artifact_root.resolve()):
        raise CandidateGateError("build provenance is outside the artifact root")
    sdist_record = _mapping(payload["sdist"], "sdist provenance")
    wheel_record = _mapping(payload["wheel"], "Wheel provenance")
    expected_sdist_fields = {
        "command",
        "cwd",
        "exit_code",
        "log_ref",
        "log_sha256",
        "sha256",
    }
    expected_wheel_fields = expected_sdist_fields | {"source_sdist_sha256"}
    if set(sdist_record) != expected_sdist_fields or set(wheel_record) != expected_wheel_fields:
        raise CandidateGateError("artifact provenance fields are not exact")
    if (
        sdist_record["command"] != SDIST_BUILD_COMMAND
        or wheel_record["command"] != WHEEL_BUILD_COMMAND
    ):
        raise CandidateGateError("artifact build command mismatch")
    for label, record in (("sdist", sdist_record), ("Wheel", wheel_record)):
        exit_code = record["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or exit_code != 0:
            raise CandidateGateError(f"{label} build failed")
    if sdist_record["cwd"] != ".":
        raise CandidateGateError("sdist build cwd mismatch")
    sdist_log = _verified_log(batch_root, sdist_record, "sdist")
    wheel_log = _verified_log(batch_root, wheel_record, "Wheel")
    if re.search(rb"(?im)\b(?:ERROR|FAILED)\b", sdist_log):
        raise CandidateGateError("sdist build log records failure")
    if re.search(rb"(?im)\b(?:ERROR|FAILED)\b", wheel_log):
        raise CandidateGateError("Wheel build log records failure")
    if b"Successfully built" not in sdist_log:
        raise CandidateGateError("sdist build log does not record success")
    if b"Successfully built" not in wheel_log:
        raise CandidateGateError("Wheel build log does not record success")
    sdist_hash = _hash_file(sdist_path)
    wheel_hash = _hash_file(wheel_path)
    if sdist_record["sha256"] != sdist_hash:
        raise CandidateGateError("sdist artifact hash mismatch")
    if wheel_record["source_sdist_sha256"] != sdist_hash:
        raise CandidateGateError("Wheel source sdist hash mismatch")
    sdist_root, sdist_files = _sdist_payload(sdist_path)
    expected_wheel_cwd = f"dist/lean-preview-candidate/extracted/{sdist_hash}/{sdist_root}"
    if wheel_record["cwd"] != expected_wheel_cwd:
        raise CandidateGateError("Wheel build cwd mismatch")
    for relative, expected in tracked_source_files.items():
        if sdist_files.get(relative) != expected:
            raise CandidateGateError(f"sdist source payload mismatch: {relative}")
    visible = "src/tianshu/web/static/manifest.json"
    hidden = "src/tianshu/web/static/.vite/manifest.json"
    if visible not in sdist_files or hidden in sdist_files:
        raise CandidateGateError("sdist must contain the visible manifest only")
    wheel_files = _wheel_payload(wheel_path)
    wheel_roots = {PurePosixPath(relative).parts[0] for relative in wheel_files}
    dist_info_roots = {
        root for root in wheel_roots if re.fullmatch(r"tianshu-[^/]+\.dist-info", root)
    }
    if len(dist_info_roots) != 1 or any(
        root != "tianshu" and root not in dist_info_roots for root in wheel_roots
    ):
        raise CandidateGateError(
            "Wheel package payload has installable payload outside the allowlist"
        )
    wheel_visible = "tianshu/web/static/manifest.json"
    wheel_hidden = "tianshu/web/static/.vite/manifest.json"
    if wheel_visible not in wheel_files or wheel_hidden in wheel_files:
        raise CandidateGateError("Wheel must contain the visible manifest only")
    sdist_package = {
        relative.removeprefix("src/"): content
        for relative, content in sdist_files.items()
        if relative.startswith("src/tianshu/")
    }
    committed_package_paths = {
        relative.removeprefix("src/")
        for relative in tracked_source_files
        if relative.startswith("src/tianshu/")
    }
    if any(
        not relative.startswith("tianshu/web/static/")
        for relative in sdist_package.keys() - committed_package_paths
    ):
        raise CandidateGateError("sdist contains package source absent from the committed source")
    wheel_package = {
        relative: content
        for relative, content in wheel_files.items()
        if relative.startswith("tianshu/")
    }
    if wheel_package != sdist_package:
        raise CandidateGateError("Wheel package payload does not exactly match the sdist")
    if wheel_record["sha256"] != wheel_hash:
        raise CandidateGateError("Wheel package payload or artifact hash mismatch")
    return BuildProvenance(
        content_hash=payload["content_hash"],
        wheel_sha256=wheel_record["sha256"],
    )


def _validate_command_results(results: Mapping[str, Mapping[str, Any]]) -> None:
    if set(results) != set(REQUIRED_FINAL_COMMANDS):
        raise CandidateGateError("required final Gate commands are incomplete")
    for command_id, expected_command in REQUIRED_FINAL_COMMANDS.items():
        result = results[command_id]
        if result.get("command") != expected_command:
            raise CandidateGateError(f"required Gate command mismatch: {command_id}")
        integers = (
            "exit_code",
            "passed",
            "failed",
            "skipped",
            "deselected",
            "warnings",
            "required_skipped",
        )
        if any(
            isinstance(result.get(field), bool)
            or not isinstance(result.get(field), int)
            or result[field] < 0
            for field in integers
        ):
            raise CandidateGateError(f"required Gate counts are invalid: {command_id}")
        if result["exit_code"] != 0 or result["failed"] != 0:
            raise CandidateGateError(f"required Gate failed: {command_id}")
        if result["required_skipped"] != 0:
            raise CandidateGateError(f"required Gate skipped tests: {command_id}")
        if not isinstance(result.get("summary"), str) or not result["summary"].strip():
            raise CandidateGateError(f"required Gate summary is missing: {command_id}")


def validate_candidate_context(context: CandidateContext) -> None:
    """Reject any unbound, stale, skipped, or overstated candidate fact."""

    if not context.clean_source:
        raise CandidateGateError("candidate source must be a clean committed tree")
    if set(context.phase_reports) != set(PHASE_SPECS):
        raise CandidateGateError("phase reports are incomplete")
    for phase_id, spec in PHASE_SPECS.items():
        phase = context.phase_reports[phase_id]
        if (
            phase.phase_id != phase_id
            or phase.gate_id != spec.gate_id
            or phase.report_ref != spec.report_ref
            or phase.gate_source_commit != spec.gate_source_commit
            or phase.report_commit != spec.report_commit
            or spec.gate_source_commit not in context.phase_commits_in_history
            or spec.report_commit not in context.phase_commits_in_history
        ):
            raise CandidateGateError(f"phase commit binding mismatch: {phase_id}")
        text = phase.report_bytes.decode("utf-8")
        if spec.pass_marker not in text:
            raise CandidateGateError(f"phase report is not passed: {phase_id}")
        if _EXTERNAL_AS_PASS.search(text):
            raise CandidateGateError("external_pending cannot be counted as passed")
    if context.demo_report.get("source_commit") != context.source_commit:
        raise CandidateGateError("demo source commit does not match the Candidate")
    if (
        not context.verified_demo
        or context.demo_report.get("fixture") is not False
        or context.demo_report.get("wheel_sha256") != context.wheel_sha256
        or len(context.demo_report.get("steps", ())) != 13
        or any(step.get("status") != "passed" for step in context.demo_report["steps"])
    ):
        raise CandidateGateError("a source-bound verified non-fixture demo is required")
    if context.wheel_sha256 != context.observed_wheel_sha256:
        raise CandidateGateError("candidate Wheel hash mismatch")
    if context.sdist_sha256 != context.observed_sdist_sha256:
        raise CandidateGateError("candidate sdist hash mismatch")
    _validate_command_results(context.command_results)
    if context.screenshot_expected != context.screenshot_observed:
        raise CandidateGateError("stale or corrupt screenshot baseline")
    if any(fact not in context.capability_matrix for fact in _CAPABILITY_FACTS):
        raise CandidateGateError("capability matrix does not match the Candidate boundary")
    if tuple(context.deferred_work_ids) != REQUIRED_DEFERRED_WORK_IDS:
        raise CandidateGateError("deferred work ledger is incomplete")
    if context.tracked_idea_paths:
        raise CandidateGateError("tracked .idea paths violate D7 hygiene")
    if context.visual_status != "user_approval_pending":
        raise CandidateGateError("visual status cannot advance without a user approval record")
    if context.publication_status != "not_authorized":
        raise CandidateGateError("publication status must remain not_authorized")


def _parse_screenshot_manifest(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    manifest = root / "web/e2e/__screenshots__/SHA256SUMS"
    expected: dict[str, str] = {}
    observed: dict[str, str] = {}
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CandidateGateError("screenshot SHA256SUMS is missing") from exc
    for line in lines:
        digest, separator, name = line.partition("  ")
        if not separator or not _SHA256.fullmatch(digest) or not name:
            raise CandidateGateError("screenshot SHA256SUMS is malformed")
        path = manifest.parent / name
        expected[name] = digest
        observed[name] = _hash_file(path)
    if len(expected) != 24:
        raise CandidateGateError("screenshot manifest must bind exactly 24 baselines")
    return expected, observed


def _deferred_ids(root: Path) -> tuple[str, ...]:
    text = (root / "docs/cc-fable-v1/06-deferred-work-backlog.md").read_text(encoding="utf-8")
    present = tuple(work_id for work_id in REQUIRED_DEFERRED_WORK_IDS if f"| {work_id} |" in text)
    return present


def only_bound_candidate_evidence_is_dirty(
    dirty_paths: tuple[str, ...], evidence_refs: tuple[str, ...]
) -> bool:
    """Allow only the evidence batches bound into the Candidate assembly."""

    batch_roots = tuple(PurePosixPath(reference).parent for reference in evidence_refs)
    return all(
        ".." not in (path := PurePosixPath(value)).parts
        and any(path.is_relative_to(batch_root) for batch_root in batch_roots)
        for value in dirty_paths
    )


def _tracked_evidence_ref(path: Path, label: str) -> str:
    root = ROOT.resolve()
    evidence_root = (ROOT / "docs/cc-fable-v1/evidence").resolve()
    resolved = path.resolve()
    if not resolved.is_file() or not resolved.is_relative_to(evidence_root):
        raise CandidateGateError(f"{label} must be tracked under docs/cc-fable-v1/evidence")
    return resolved.relative_to(root).as_posix()


def _phase_inputs(root: Path) -> dict[str, PhaseReportInput]:
    backend = GitBackend()
    location = GitLocation(root)
    phases: dict[str, PhaseReportInput] = {}
    for phase_id, spec in PHASE_SPECS.items():
        current = (root / spec.report_ref).read_bytes()
        try:
            historical = backend.read_file_at_commit(location, spec.report_commit, spec.report_ref)
        except GitBackendError as exc:
            raise CandidateGateError(f"historical phase report is unavailable: {phase_id}") from exc
        if current != historical:
            raise CandidateGateError(f"historical phase report bytes changed: {phase_id}")
        phases[phase_id] = PhaseReportInput(
            phase_id=phase_id,
            gate_id=spec.gate_id,
            report_ref=spec.report_ref,
            report_bytes=current,
            gate_source_commit=spec.gate_source_commit,
            report_commit=spec.report_commit,
        )
    return phases


def _write_phase_manifests(
    root: Path, source_commit: str, phases: Mapping[str, PhaseReportInput]
) -> dict[str, Path]:
    manifest_root = root / "dist/lean-preview-candidate/phase-manifests"
    manifest_root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for phase_id, phase in phases.items():
        payload: dict[str, object] = {
            "schema_version": 1,
            "phase_id": phase_id,
            "gate_id": phase.gate_id,
            "status": "passed",
            "source_commit": source_commit,
            "gate_source_commit": phase.gate_source_commit,
            "report_commit": phase.report_commit,
            "report_ref": phase.report_ref,
            "report_sha256": _hash_bytes(phase.report_bytes),
            "external_pending": [],
        }
        payload["content_hash"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        path = manifest_root / f"{phase_id}.json"
        path.write_bytes(_canonical_bytes(payload))
        paths[phase_id] = path
    return paths


def _render_report(
    context: CandidateContext,
    candidate: LeanPreviewCandidateReportV1,
    demo_ref: str,
) -> str:
    rows = []
    for command_id in REQUIRED_FINAL_COMMANDS:
        result = context.command_results[command_id]
        rows.append(
            f"| `{result['command']}` | {result['summary']} | "
            f"{result['passed']} | {result['failed']} | {result['skipped']} | "
            f"{result['deselected']} | {result['warnings']} |"
        )
    phase_rows = [
        f"| `{phase_id}` | `{phase.gate_source_commit}` | `{phase.report_commit}` | "
        f"`{candidate.phase_report_hashes[phase_id]}` |"
        for phase_id, phase in context.phase_reports.items()
    ]
    return "\n".join(
        (
            "# Lean Developer Preview Candidate",
            "",
            f"- source_commit: `{candidate.source_commit}`",
            "- automation_status: `passed`",
            "- visual_status: `user_approval_pending`",
            "- publication_status: `not_authorized`",
            "- full G4: `external_pending`",
            "- full G5: `deferred`",
            "",
            "## Immutable phase bindings",
            "",
            "| Phase | Gate source commit | Report commit | Candidate manifest hash |",
            "|---|---|---|---|",
            *phase_rows,
            "",
            "Each manifest hashes the exact report bytes in the clean Candidate source; the",
            "historical evidence commit above remains the immutable Gate execution identity.",
            "No `external_pending` item is counted as an automated pass.",
            "",
            "## Exact candidate artifacts and demo",
            "",
            f"- gate_evidence: `{candidate.gate_evidence_ref}`",
            f"- gate_evidence_hash: `{candidate.gate_evidence_hash}`",
            f"- build_provenance: `{candidate.build_provenance_ref}`",
            f"- build_provenance_hash: `{candidate.build_provenance_hash}`",
            f"- demo_report: `{demo_ref}`",
            f"- demo_report_hash: `{candidate.demo_report_hash}`",
            f"- Wheel SHA-256: `{candidate.wheel_sha256}`",
            f"- sdist SHA-256: `{candidate.sdist_sha256}`",
            "- build frontend: Python 3.12 with `build==1.5.0`",
            "- Wheel origin: exact sdist extracted and built outside the repository",
            "",
            "## Final automated Gates",
            "",
            "| Exact command | Exact terminal summary | Passed | Failed | Skipped | Deselected | Warnings |",
            "|---|---|---:|---:|---:|---:|---:|",
            *rows,
            "",
            "## User final-approval checklist",
            "",
            "- [ ] Review the three desktop pages and interactions; no approval artifact exists yet.",
            "- [ ] Decide whether to authorize any publication action separately.",
            "- [ ] Select any D8 deferred work only through a new approved plan.",
            "",
            "No push, tag, release, upload, visibility change, or publication was performed.",
            "",
        )
    )


def assemble_candidate(
    *,
    output: Path,
    report: Path,
    gate_evidence: Path,
    build_provenance: Path,
    demo_report: Path,
) -> None:
    backend = GitBackend()
    location = GitLocation(ROOT)
    try:
        source_commit = backend.resolve_commit(location, "HEAD")
        dirty_paths = backend.worktree_status_paths(location)
        history = frozenset(entry.sha for entry in backend.list_log(location))
        tracked_idea = backend.tracked_paths(location, ".idea")
    except GitBackendError as exc:
        raise CandidateGateError("Git candidate source facts are unavailable") from exc
    gate_ref = _tracked_evidence_ref(gate_evidence, "Gate evidence")
    provenance_ref = _tracked_evidence_ref(build_provenance, "build provenance")
    demo_ref = _tracked_evidence_ref(demo_report, "demo report")
    verified_gates = load_gate_evidence(
        gate_evidence,
        artifact_root=ROOT,
        expected_source_commit=source_commit,
    )
    candidate_root = ROOT / "dist/lean-preview-candidate"
    sdists = tuple(candidate_root.glob("tianshu-*.tar.gz"))
    wheels = tuple((ROOT / CANDIDATE_WHEEL_DIR).glob("tianshu-*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise CandidateGateError("exactly one candidate sdist and from-sdist Wheel are required")
    sdist, wheel = sdists[0], wheels[0]
    try:
        committed_paths = backend.list_files_at_commit(location, source_commit)
        tracked_source_files = {
            relative: backend.read_file_at_commit(location, source_commit, relative)
            for relative in committed_paths
            if relative == "pyproject.toml" or relative.startswith("src/tianshu/")
        }
    except GitBackendError as exc:
        raise CandidateGateError("committed build source is unavailable") from exc
    verified_build = verify_build_provenance(
        build_provenance,
        artifact_root=ROOT,
        expected_source_commit=source_commit,
        sdist_path=sdist,
        wheel_path=wheel,
        tracked_source_files=tracked_source_files,
    )
    candidate_wheel_sha256 = _hash_file(wheel)
    if (
        verified_gates.wheel_sha256 != verified_build.wheel_sha256
        or verified_gates.wheel_sha256 != candidate_wheel_sha256
        or verified_build.wheel_sha256 != candidate_wheel_sha256
    ):
        raise CandidateGateError("Gate, build provenance, and Candidate Wheel identity mismatch")
    demo_path = demo_report
    demo_artifacts = demo_path.parent / "artifacts"
    try:
        demo = verify_demo_evidence(
            demo_path,
            demo_artifacts,
            expected_source_commit=source_commit,
            expected_wheel_sha256=candidate_wheel_sha256,
        )
    except EvidenceVerificationError as exc:
        raise CandidateGateError("verified non-fixture demo evidence was rejected") from exc
    phases = _phase_inputs(ROOT)
    screenshot_expected, screenshot_observed = _parse_screenshot_manifest(ROOT)
    capability_path = ROOT / "docs/launch/capability-matrix.md"
    context = CandidateContext(
        source_commit=source_commit,
        clean_source=only_bound_candidate_evidence_is_dirty(
            dirty_paths, (gate_ref, provenance_ref, demo_ref)
        ),
        phase_reports=phases,
        phase_commits_in_history=history,
        demo_report=demo,
        verified_demo=True,
        wheel_sha256=candidate_wheel_sha256,
        observed_wheel_sha256=candidate_wheel_sha256,
        sdist_sha256=_hash_file(sdist),
        observed_sdist_sha256=_hash_file(sdist),
        capability_matrix=capability_path.read_text(encoding="utf-8"),
        deferred_work_ids=_deferred_ids(ROOT),
        command_results=verified_gates.results,
        screenshot_expected=screenshot_expected,
        screenshot_observed=screenshot_observed,
        tracked_idea_paths=tracked_idea,
        visual_status="user_approval_pending",
        publication_status="not_authorized",
    )
    validate_candidate_context(context)
    phase_paths = _write_phase_manifests(ROOT, source_commit, phases)
    candidate_payload: dict[str, object] = {
        "schema_version": 1,
        "source_commit": source_commit,
        "gate_evidence_ref": gate_ref,
        "gate_evidence_hash": _hash_file(gate_evidence),
        "build_provenance_ref": provenance_ref,
        "build_provenance_hash": _hash_file(build_provenance),
        "phase_report_hashes": {
            phase_id: json.loads(path.read_bytes())["content_hash"]
            for phase_id, path in phase_paths.items()
        },
        "demo_report_ref": demo_ref,
        "demo_report_hash": demo["content_hash"],
        "wheel_sha256": context.wheel_sha256,
        "sdist_sha256": context.sdist_sha256,
        "capability_matrix_hash": _hash_file(capability_path),
        "automation_status": "passed",
        "visual_status": "user_approval_pending",
        "visual_approval_record_ref": None,
        "visual_approval_record_hash": None,
        "publication_status": "not_authorized",
        "deferred_work_ids": list(REQUIRED_DEFERRED_WORK_IDS),
    }
    candidate_payload["content_hash"] = lean_preview_content_hash(candidate_payload)
    candidate = LeanPreviewCandidateReportV1.model_validate_json(
        _canonical_bytes(candidate_payload)
    )
    if not verified_gates.content_hash or not verified_build.content_hash:
        raise CandidateGateError("candidate evidence content hash is missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_canonical_bytes(candidate_payload))
    try:
        verify_candidate_report(
            output,
            artifact_root=ROOT,
            demo_report_path=demo_path,
            phase_report_paths=phase_paths,
            wheel_path=wheel,
            sdist_path=sdist,
            capability_matrix_path=capability_path,
        )
    except EvidenceVerificationError as exc:
        output.unlink(missing_ok=True)
        raise CandidateGateError("strict candidate evidence verification failed") from exc
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_render_report(context, candidate, demo_ref), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--gate-evidence",
        required=True,
        type=Path,
    )
    parser.add_argument("--build-provenance", required=True, type=Path)
    parser.add_argument("--demo-report", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        assemble_candidate(
            output=args.output,
            report=args.report,
            gate_evidence=args.gate_evidence,
            build_provenance=args.build_provenance,
            demo_report=args.demo_report,
        )
    except (CandidateGateError, OSError, ValueError) as exc:
        print(f"Lean Preview Candidate rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Lean Preview Candidate verified: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
