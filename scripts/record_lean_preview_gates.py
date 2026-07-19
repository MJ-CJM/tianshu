#!/usr/bin/env python3
"""After build provenance, run fixed Lean Preview Gates against its exact Wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation

try:
    from scripts.check_lean_preview_candidate import (
        CANDIDATE_WHEEL_DIR,
        REQUIRED_FINAL_COMMANDS,
        REQUIRED_GATE_CWDS,
        REQUIRED_GATE_ENVIRONMENTS,
        required_gate_environment,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from check_lean_preview_candidate import (
        CANDIDATE_WHEEL_DIR,
        REQUIRED_FINAL_COMMANDS,
        REQUIRED_GATE_CWDS,
        REQUIRED_GATE_ENVIRONMENTS,
        required_gate_environment,
    )

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "docs/cc-fable-v1/evidence/gates"
GATE_COMMANDS = REQUIRED_FINAL_COMMANDS
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class GateRecordingError(RuntimeError):
    """A Gate run could not produce complete passing evidence."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _content_hash(value: dict[str, object]) -> str:
    payload = dict(value)
    payload.pop("content_hash", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _argv(gate_id: str) -> list[str]:
    argv = shlex.split(GATE_COMMANDS[gate_id])
    if REQUIRED_GATE_ENVIRONMENTS[gate_id].get("VIRTUAL_ENV") == "unset":
        return argv[3:]
    return argv


def record_gate_evidence(*, output_root: Path, batch_id: str, source_commit: str) -> Path:
    """Execute every fixed Gate once and write a canonical evidence batch."""

    if not batch_id or Path(batch_id).name != batch_id:
        raise GateRecordingError("batch id must be one safe path component")
    if not _COMMIT.fullmatch(source_commit):
        raise GateRecordingError("source commit must be a full Git commit")
    wheels = tuple((ROOT / CANDIDATE_WHEEL_DIR).glob("tianshu-*.whl"))
    if len(wheels) != 1:
        raise GateRecordingError("exactly one candidate Wheel is required before Gate recording")
    wheel = wheels[0].resolve()
    wheel_sha256 = hashlib.sha256(wheel.read_bytes()).hexdigest()
    batch_root = output_root / batch_id
    if batch_root.exists():
        raise GateRecordingError(f"evidence batch already exists: {batch_root}")
    logs = batch_root / "logs"
    logs.mkdir(parents=True)
    records: dict[str, object] = {}
    for gate_id in GATE_COMMANDS:
        record_environment = required_gate_environment(
            gate_id,
            batch_id=batch_id,
            source_commit=source_commit,
        )
        environment = dict(os.environ)
        for name, value in record_environment.items():
            if value == "unset":
                environment.pop(name, None)
            else:
                environment[name] = value
        completed = subprocess.run(
            _argv(gate_id),
            cwd=ROOT / REQUIRED_GATE_CWDS[gate_id],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        raw = completed.stdout
        log_ref = f"logs/{gate_id}.log"
        (batch_root / log_ref).write_bytes(raw)
        if completed.returncode != 0:
            raise GateRecordingError(
                f"required Gate failed: {gate_id} (exit {completed.returncode})"
            )
        records[gate_id] = {
            "command": GATE_COMMANDS[gate_id],
            "cwd": REQUIRED_GATE_CWDS[gate_id],
            "environment": record_environment,
            "exit_code": completed.returncode,
            "log_ref": log_ref,
            "log_sha256": hashlib.sha256(raw).hexdigest(),
        }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "source_commit": source_commit,
        "wheel_sha256": wheel_sha256,
        "commands": records,
    }
    manifest["content_hash"] = _content_hash(manifest)
    path = batch_root / "manifest.json"
    path.write_bytes(_canonical_bytes(manifest))
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, default=EVIDENCE_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    backend = GitBackend()
    location = GitLocation(ROOT)
    try:
        source_commit = backend.resolve_commit(location, "HEAD")
        dirty_paths = backend.worktree_status_paths(location)
        build_evidence_root = PurePosixPath("docs/cc-fable-v1/evidence/builds")
        if any(not PurePosixPath(path).is_relative_to(build_evidence_root) for path in dirty_paths):
            raise GateRecordingError("Gate recording requires a clean source tree")
        path = record_gate_evidence(
            output_root=args.output_root,
            batch_id=args.batch_id,
            source_commit=source_commit,
        )
        if backend.resolve_commit(location, "HEAD") != source_commit:
            path.unlink(missing_ok=True)
            raise GateRecordingError("source commit changed during Gate recording")
    except (GitBackendError, GateRecordingError, OSError) as exc:
        print(f"Lean Preview Gate recording failed: {exc}", file=sys.stderr)
        return 1
    print(f"Lean Preview Gate evidence recorded: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
