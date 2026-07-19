#!/usr/bin/env python3
"""Build the Lean Preview distributions and record source-bound provenance."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import platform
import re
import sys
from pathlib import Path

from tianshu.executor.git_backend import GitBackend, GitBackendError, GitLocation

try:
    from scripts._trusted_local_process import run_trusted_local_process
    from scripts.check_lean_preview_candidate import (
        SDIST_BUILD_COMMAND,
        WHEEL_BUILD_COMMAND,
        _sdist_payload,
    )
except ModuleNotFoundError:  # direct ``python scripts/...`` execution
    from _trusted_local_process import run_trusted_local_process
    from check_lean_preview_candidate import (
        SDIST_BUILD_COMMAND,
        WHEEL_BUILD_COMMAND,
        _sdist_payload,
    )


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "docs/cc-fable-v1/evidence/builds"
ARTIFACT_ROOT = Path("dist/lean-preview-candidate")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class BuildRecordingError(RuntimeError):
    """A fixed build could not produce complete provenance."""


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


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_build(argv: list[str], *, cwd: Path, log_path: Path, label: str) -> None:
    completed = run_trusted_local_process(argv, cwd=cwd)
    log_path.write_bytes(completed.stdout + completed.stderr)
    if completed.returncode != 0:
        raise BuildRecordingError(f"{label} build failed (exit {completed.returncode})")


def _only_artifact(directory: Path, pattern: str, label: str) -> Path:
    artifacts = tuple(directory.glob(pattern))
    if len(artifacts) != 1:
        raise BuildRecordingError(f"expected exactly one {label} artifact")
    return artifacts[0]


def record_build_provenance(*, output_root: Path, batch_id: str, source_commit: str) -> Path:
    """Run the two fixed builds and write their canonical provenance record."""

    if not batch_id or Path(batch_id).name != batch_id:
        raise BuildRecordingError("batch id must be one safe path component")
    if not _COMMIT.fullmatch(source_commit):
        raise BuildRecordingError("source commit must be a full Git commit")
    if platform.python_version_tuple()[:2] != ("3", "12"):
        raise BuildRecordingError("build provenance requires Python 3.12")
    if metadata.version("build") != "1.5.0":
        raise BuildRecordingError("build frontend must be build 1.5.0")

    batch_root = output_root / batch_id
    artifact_root = ROOT / ARTIFACT_ROOT
    from_sdist = artifact_root / "from-sdist"
    if batch_root.exists():
        raise BuildRecordingError(f"evidence batch already exists: {batch_root}")
    if tuple(artifact_root.glob("*.tar.gz")) or tuple(from_sdist.glob("*.whl")):
        raise BuildRecordingError("candidate distribution output is not empty")
    logs = batch_root / "logs"
    logs.mkdir(parents=True)

    sdist_log = logs / "sdist.log"
    _run_build(
        [
            sys.executable,
            "-m",
            "build",
            "--sdist",
            "--outdir",
            ARTIFACT_ROOT.as_posix(),
        ],
        cwd=ROOT,
        log_path=sdist_log,
        label="sdist",
    )
    sdist = _only_artifact(artifact_root, "*.tar.gz", "sdist")
    sdist_hash = _hash_file(sdist)
    sdist_root, sdist_files = _sdist_payload(sdist)
    extracted_root = artifact_root / "extracted" / sdist_hash / sdist_root
    for relative, content in sdist_files.items():
        target = extracted_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    wheel_log = logs / "wheel.log"
    _run_build(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            "../../../from-sdist",
        ],
        cwd=extracted_root,
        log_path=wheel_log,
        label="Wheel",
    )
    wheel = _only_artifact(from_sdist, "*.whl", "Wheel")

    payload: dict[str, object] = {
        "schema_version": 1,
        "source_commit": source_commit,
        "python_version": platform.python_version(),
        "frontend": {"name": "build", "version": "1.5.0"},
        "sdist": {
            "command": SDIST_BUILD_COMMAND,
            "cwd": ".",
            "exit_code": 0,
            "log_ref": "logs/sdist.log",
            "log_sha256": _hash_file(sdist_log),
            "sha256": sdist_hash,
        },
        "wheel": {
            "command": WHEEL_BUILD_COMMAND,
            "cwd": extracted_root.relative_to(ROOT).as_posix(),
            "exit_code": 0,
            "log_ref": "logs/wheel.log",
            "log_sha256": _hash_file(wheel_log),
            "sha256": _hash_file(wheel),
            "source_sdist_sha256": sdist_hash,
        },
    }
    payload["content_hash"] = _content_hash(payload)
    path = batch_root / "provenance.json"
    path.write_bytes(_canonical_bytes(payload))
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
            raise BuildRecordingError("build recording requires a clean source tree")
        path = record_build_provenance(
            output_root=args.output_root,
            batch_id=args.batch_id,
            source_commit=source_commit,
        )
        if backend.resolve_commit(location, "HEAD") != source_commit:
            path.unlink(missing_ok=True)
            raise BuildRecordingError("source commit changed during build recording")
    except (BuildRecordingError, GitBackendError, OSError) as exc:
        print(f"Lean Preview build recording failed: {exc}", file=sys.stderr)
        return 1
    print(f"Lean Preview build provenance recorded: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
