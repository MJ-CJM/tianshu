from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
RECORDER_PATH = ROOT / "scripts" / "record_lean_preview_gates.py"
SOURCE_COMMIT = "1" * 40


def _module():
    spec = importlib.util.spec_from_file_location("lean_preview_gate_recorder", RECORDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def test_recorder_executes_fixed_gates_and_hashes_unmodified_combined_logs(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def run(argv, *, cwd, env, stdout, stderr, check):
        assert stdout is subprocess.PIPE
        assert stderr is subprocess.STDOUT
        assert check is False
        calls.append((argv, cwd, env))
        gate_id = tuple(module.GATE_COMMANDS)[len(calls) - 1]
        return subprocess.CompletedProcess(argv, 0, stdout=f"raw {gate_id}\n".encode())

    monkeypatch.setattr(module.subprocess, "run", run)
    manifest_path = module.record_gate_evidence(
        output_root=tmp_path,
        batch_id="batch-1",
        source_commit=SOURCE_COMMIT,
    )

    manifest = json.loads(manifest_path.read_bytes())
    assert manifest_path.read_bytes() == _canonical_bytes(manifest)
    assert (
        manifest["content_hash"]
        == hashlib.sha256(
            _canonical_bytes(
                {key: value for key, value in manifest.items() if key != "content_hash"}
            )
        ).hexdigest()
    )
    assert list(manifest["commands"]) == sorted(module.GATE_COMMANDS)
    assert len(calls) == len(module.GATE_COMMANDS)
    for gate_id, record in manifest["commands"].items():
        raw = f"raw {gate_id}\n".encode()
        assert (manifest_path.parent / record["log_ref"]).read_bytes() == raw
        assert record["log_sha256"] == hashlib.sha256(raw).hexdigest()
        assert set(record) == {
            "command",
            "cwd",
            "environment",
            "exit_code",
            "log_ref",
            "log_sha256",
        }


def test_recorder_stops_on_first_failure_without_writing_a_pass_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    calls = 0

    def run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            argv,
            7 if calls == 2 else 0,
            stdout=b"unaltered failure output\n" if calls == 2 else b"first passed\n",
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.GateRecordingError, match="failed: ruff_format"):
        module.record_gate_evidence(
            output_root=tmp_path,
            batch_id="batch-failed",
            source_commit=SOURCE_COMMIT,
        )

    batch = tmp_path / "batch-failed"
    assert calls == 2
    assert (batch / "logs/ruff_format.log").read_bytes() == b"unaltered failure output\n"
    assert not (batch / "manifest.json").exists()
    assert not (batch / "logs/mypy.log").exists()
