#!/usr/bin/env python3
"""Run the fixed Lean Preview Gates and record hashed raw logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation

try:
    from scripts.check_lean_preview_candidate import (
        REQUIRED_FINAL_COMMANDS,
        REQUIRED_GATE_CWDS,
        REQUIRED_GATE_ENVIRONMENTS,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from check_lean_preview_candidate import (
        REQUIRED_FINAL_COMMANDS,
        REQUIRED_GATE_CWDS,
        REQUIRED_GATE_ENVIRONMENTS,
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
    if REQUIRED_GATE_ENVIRONMENTS[gate_id] == {"VIRTUAL_ENV": "unset"}:
        return argv[3:]
    return argv


def record_gate_evidence(*, output_root: Path, batch_id: str, source_commit: str) -> Path:
    """Execute every fixed Gate once and write a canonical evidence batch."""

    if not batch_id or Path(batch_id).name != batch_id:
        raise GateRecordingError("batch id must be one safe path component")
    if not _COMMIT.fullmatch(source_commit):
        raise GateRecordingError("source commit must be a full Git commit")
    batch_root = output_root / batch_id
    if batch_root.exists():
        raise GateRecordingError(f"evidence batch already exists: {batch_root}")
    logs = batch_root / "logs"
    logs.mkdir(parents=True)
    records: dict[str, object] = {}
    for gate_id in GATE_COMMANDS:
        environment = dict(os.environ)
        if REQUIRED_GATE_ENVIRONMENTS[gate_id] == {"VIRTUAL_ENV": "unset"}:
            environment.pop("VIRTUAL_ENV", None)
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
            "environment": REQUIRED_GATE_ENVIRONMENTS[gate_id],
            "exit_code": completed.returncode,
            "log_ref": log_ref,
            "log_sha256": hashlib.sha256(raw).hexdigest(),
        }
    manifest: dict[str, object] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "source_commit": source_commit,
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
        if backend.worktree_status_paths(location):
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
