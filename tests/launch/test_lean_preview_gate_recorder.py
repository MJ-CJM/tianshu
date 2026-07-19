from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(module, "ROOT", tmp_path)
    wheel = tmp_path / "dist/lean-preview-candidate/from-sdist/tianshu-0.4.2.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"one exact candidate Wheel\n")
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def run(argv, *, cwd, env):
        calls.append((argv, cwd, env))
        gate_id = tuple(module.GATE_COMMANDS)[len(calls) - 1]
        return SimpleNamespace(
            returncode=0,
            stdout=f"raw {gate_id}\n".encode(),
            stderr=f"stderr {gate_id}\n".encode(),
        )

    monkeypatch.setattr(module, "run_trusted_local_process", run)
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
    assert manifest["wheel_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert len(calls) == len(module.GATE_COMMANDS)
    calls_by_gate = dict(zip(module.GATE_COMMANDS, calls, strict=True))
    for gate_id, record in manifest["commands"].items():
        raw = f"raw {gate_id}\nstderr {gate_id}\n".encode()
        argv, cwd, actual_environment = calls_by_gate[gate_id]
        expected_environment = module.required_gate_environment(
            gate_id,
            batch_id="batch-1",
            source_commit=SOURCE_COMMIT,
        )
        assert argv == module._argv(gate_id)
        assert cwd == module.ROOT / module.REQUIRED_GATE_CWDS[gate_id]
        for name, value in expected_environment.items():
            if value == "unset":
                assert name not in actual_environment
            else:
                assert actual_environment[name] == value
        assert (manifest_path.parent / record["log_ref"]).read_bytes() == raw
        assert record["log_sha256"] == hashlib.sha256(raw).hexdigest()
        assert record["environment"] == expected_environment
        assert set(record) == {
            "command",
            "cwd",
            "environment",
            "exit_code",
            "log_ref",
            "log_sha256",
        }
    assert manifest["commands"]["packaging"]["environment"] == {
        "BATCH_ID": "batch-1",
        "TIANSHU_LEAN_WHEEL_SOURCE_COMMIT": SOURCE_COMMIT,
        "VIRTUAL_ENV": "unset",
    }


def test_recorder_stops_on_first_failure_without_writing_a_pass_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    wheel = tmp_path / "dist/lean-preview-candidate/from-sdist/tianshu-0.4.2.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"one exact candidate Wheel\n")
    calls = 0

    def run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=7 if calls == 2 else 0,
            stdout=b"unaltered failure output\n" if calls == 2 else b"first passed\n",
            stderr=b"failure stderr\n" if calls == 2 else b"",
        )

    monkeypatch.setattr(module, "run_trusted_local_process", run)
    with pytest.raises(module.GateRecordingError, match="failed: ruff_format"):
        module.record_gate_evidence(
            output_root=tmp_path,
            batch_id="batch-failed",
            source_commit=SOURCE_COMMIT,
        )

    batch = tmp_path / "batch-failed"
    assert calls == 2
    assert (batch / "logs/ruff_format.log").read_bytes() == (
        b"unaltered failure output\nfailure stderr\n"
    )
    assert not (batch / "manifest.json").exists()
    assert not (batch / "logs/mypy.log").exists()


@pytest.mark.parametrize("wheel_count", [0, 2])
def test_recorder_requires_one_candidate_wheel_before_running_any_gate(
    tmp_path: Path, monkeypatch, wheel_count: int
) -> None:
    module = _module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    wheel_root = tmp_path / "dist/lean-preview-candidate/from-sdist"
    wheel_root.mkdir(parents=True)
    for index in range(wheel_count):
        wheel_root.joinpath(f"tianshu-0.4.{index}.whl").write_bytes(
            f"candidate Wheel {index}".encode()
        )
    calls = 0

    def run(argv, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout=b"unexpected Gate run\n", stderr=b"")

    monkeypatch.setattr(module, "run_trusted_local_process", run)
    with pytest.raises(module.GateRecordingError, match="exactly one candidate Wheel"):
        module.record_gate_evidence(
            output_root=tmp_path / "evidence",
            batch_id="batch-no-wheel",
            source_commit=SOURCE_COMMIT,
        )

    assert calls == 0
    assert not tmp_path.joinpath("evidence/batch-no-wheel").exists()


@pytest.mark.parametrize(
    ("dirty_path", "expected_exit"),
    [
        (
            "docs/cc-fable-v1/evidence/builds/gate-1/provenance.json",
            0,
        ),
        ("docs/cc-fable-v1/evidence/builds/build-1/provenance.json", 1),
        ("docs/cc-fable-v1/evidence/builds/provenance.json", 1),
        ("src/tianshu/app.py", 1),
    ],
)
def test_recorder_cli_allows_only_prior_build_provenance_evidence(
    tmp_path: Path, monkeypatch, dirty_path: str, expected_exit: int
) -> None:
    module = _module()

    class Backend:
        def resolve_commit(self, _location, _ref):
            return SOURCE_COMMIT

        def worktree_status_paths(self, _location):
            return (dirty_path,)

    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(module, "GitBackend", Backend)
    monkeypatch.setattr(
        module,
        "record_gate_evidence",
        lambda **kwargs: recorded.append(kwargs) or tmp_path / "manifest.json",
    )

    result = module.main(["--batch-id", "gate-1", "--output-root", str(tmp_path / "gates")])

    assert result == expected_exit
    assert bool(recorded) is (expected_exit == 0)
