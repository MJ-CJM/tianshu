#!/usr/bin/env python3
"""Assemble and fail-closed verify the local Lean Preview Candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

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


class PhaseSpec(NamedTuple):
    gate_id: str
    report_ref: str
    evidence_commit: str
    pass_marker: str


PHASE_SPECS = {
    "s1_g1_5": PhaseSpec(
        "G1.5",
        "docs/cc-fable-v1/reports/g1.5-report.md",
        "bbf84451a40f8f3450e080c939c82fba52428271",
        "status: passed",
    ),
    "s2_lean": PhaseSpec(
        "S2 Lean",
        "docs/cc-fable-v1/reports/s2-lean-security-report.md",
        "bbf672e560ecd2c793a1a80d0cc262b41550a4db",
        "- status: passed",
    ),
    "s3_core": PhaseSpec(
        "S3 Core",
        "docs/cc-fable-v1/reports/s3-core-governance-report.md",
        "60d3c45b836de44b132dba186e5c9a3672592ea3",
        "- status: passed",
    ),
    "s4_automation": PhaseSpec(
        "S4 Automation",
        "docs/cc-fable-v1/reports/s4-core-web-report.md",
        "303787916f1004362c3f250c07a8de179aa0885d",
        "s4_core_web_automation: automation_passed",
    ),
    "s5_lean_core": PhaseSpec(
        "S5 Lean Core",
        "docs/cc-fable-v1/reports/s5-lean-evolution-report.md",
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

_COMMAND_FIELDS = {
    "command",
    "exit_code",
    "passed",
    "failed",
    "skipped",
    "deselected",
    "warnings",
    "required_skipped",
    "summary",
}
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


class PhaseReportInput:
    def __init__(
        self,
        *,
        phase_id: str,
        gate_id: str,
        report_ref: str,
        report_bytes: bytes,
        evidence_commit: str,
    ) -> None:
        self.phase_id = phase_id
        self.gate_id = gate_id
        self.report_ref = report_ref
        self.report_bytes = report_bytes
        self.evidence_commit = evidence_commit


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


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CandidateGateError(f"{label} must be an object")
    return value


def _load_gate_manifest(path: Path) -> tuple[str, str, dict[str, dict[str, Any]]]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateGateError("final Gate manifest is missing or corrupt") from exc
    manifest = _mapping(payload, "final Gate manifest")
    if set(manifest) != {"schema_version", "source_commit", "demo_report_ref", "commands"}:
        raise CandidateGateError("final Gate manifest fields are not exact")
    if raw != _canonical_bytes(manifest):
        raise CandidateGateError("final Gate manifest must be canonical JSON")
    if manifest["schema_version"] != 1:
        raise CandidateGateError("final Gate manifest schema is unsupported")
    commands = _mapping(manifest["commands"], "final Gate commands")
    for command_id, result in commands.items():
        record = _mapping(result, f"final Gate command {command_id}")
        if set(record) != _COMMAND_FIELDS:
            raise CandidateGateError(f"final Gate command fields are not exact: {command_id}")
    source_commit = manifest["source_commit"]
    demo_report_ref = manifest["demo_report_ref"]
    if not isinstance(source_commit, str) or not isinstance(demo_report_ref, str):
        raise CandidateGateError("final Gate manifest bindings must be strings")
    return source_commit, demo_report_ref, commands


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
            or phase.evidence_commit != spec.evidence_commit
            or spec.evidence_commit not in context.phase_commits_in_history
        ):
            raise CandidateGateError(f"phase evidence commit mismatch: {phase_id}")
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


def only_bound_demo_evidence_is_dirty(dirty_paths: tuple[str, ...], demo_ref: str) -> bool:
    """Allow generated evidence only after the source commit was built cleanly."""

    reference = PurePosixPath(demo_ref)
    if reference.name != "demo-report.json" or ".." in reference.parts:
        return False
    batch_root = reference.parent
    return all(
        (path := PurePosixPath(value)).is_relative_to(batch_root) and ".." not in path.parts
        for value in dirty_paths
    )


def _phase_inputs(root: Path) -> dict[str, PhaseReportInput]:
    return {
        phase_id: PhaseReportInput(
            phase_id=phase_id,
            gate_id=spec.gate_id,
            report_ref=spec.report_ref,
            report_bytes=(root / spec.report_ref).read_bytes(),
            evidence_commit=spec.evidence_commit,
        )
        for phase_id, spec in PHASE_SPECS.items()
    }


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
        f"| `{phase_id}` | `{phase.evidence_commit}` | "
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
            "| Phase | Immutable evidence commit | Candidate manifest hash |",
            "|---|---|---|",
            *phase_rows,
            "",
            "Each manifest hashes the exact report bytes in the clean Candidate source; the",
            "historical evidence commit above remains the immutable Gate execution identity.",
            "No `external_pending` item is counted as an automated pass.",
            "",
            "## Exact candidate artifacts and demo",
            "",
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


def assemble_candidate(*, output: Path, report: Path, gate_manifest: Path) -> None:
    backend = GitBackend()
    location = GitLocation(ROOT)
    try:
        source_commit = backend.resolve_commit(location, "HEAD")
        dirty_paths = backend.worktree_status_paths(location)
        history = frozenset(entry.sha for entry in backend.list_log(location))
        tracked_idea = backend.tracked_paths(location, ".idea")
    except GitBackendError as exc:
        raise CandidateGateError("Git candidate source facts are unavailable") from exc
    manifest_source, demo_ref, command_results = _load_gate_manifest(gate_manifest)
    if manifest_source != source_commit:
        raise CandidateGateError("final Gate manifest source commit mismatch")
    candidate_root = ROOT / "dist/lean-preview-candidate"
    sdists = tuple(candidate_root.glob("tianshu-*.tar.gz"))
    wheels = tuple((candidate_root / "from-sdist").glob("tianshu-*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise CandidateGateError("exactly one candidate sdist and from-sdist Wheel are required")
    sdist, wheel = sdists[0], wheels[0]
    demo_path = ROOT / demo_ref
    demo_artifacts = demo_path.parent / "artifacts"
    try:
        demo = verify_demo_evidence(
            demo_path,
            demo_artifacts,
            expected_source_commit=source_commit,
            expected_wheel_sha256=_hash_file(wheel),
        )
    except EvidenceVerificationError as exc:
        raise CandidateGateError("verified non-fixture demo evidence was rejected") from exc
    phases = _phase_inputs(ROOT)
    screenshot_expected, screenshot_observed = _parse_screenshot_manifest(ROOT)
    capability_path = ROOT / "docs/launch/capability-matrix.md"
    context = CandidateContext(
        source_commit=source_commit,
        clean_source=only_bound_demo_evidence_is_dirty(dirty_paths, demo_ref),
        phase_reports=phases,
        phase_commits_in_history=history,
        demo_report=demo,
        verified_demo=True,
        wheel_sha256=_hash_file(wheel),
        observed_wheel_sha256=_hash_file(wheel),
        sdist_sha256=_hash_file(sdist),
        observed_sdist_sha256=_hash_file(sdist),
        capability_matrix=capability_path.read_text(encoding="utf-8"),
        deferred_work_ids=_deferred_ids(ROOT),
        command_results=command_results,
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
        "--gate-manifest",
        type=Path,
        default=ROOT / "dist/lean-preview-candidate/final-gates.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        assemble_candidate(
            output=args.output,
            report=args.report,
            gate_manifest=args.gate_manifest,
        )
    except (CandidateGateError, OSError, ValueError) as exc:
        print(f"Lean Preview Candidate rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Lean Preview Candidate verified: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
