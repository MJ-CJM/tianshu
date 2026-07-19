from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
RECORDER_PATH = ROOT / "scripts" / "record_lean_preview_build_provenance.py"
SOURCE_COMMIT = "1" * 40


def _module():
    spec = importlib.util.spec_from_file_location(
        "lean_preview_build_provenance_recorder", RECORDER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tar_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _write_sdist(path: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        _tar_member(archive, "tianshu-0.4.2/pyproject.toml", b"[build-system]\n")
        _tar_member(archive, "tianshu-0.4.2/src/tianshu/__init__.py", b"VERSION = 1\n")


def _write_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("tianshu/__init__.py", b"VERSION = 1\n")
        archive.writestr("tianshu-0.4.2.dist-info/WHEEL", b"Wheel-Version: 1.0\n")


def test_recorder_builds_wheel_from_hash_bound_extracted_sdist(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.metadata, "version", lambda name: "1.5.0")
    monkeypatch.setattr(module.platform, "python_version", lambda: "3.12.12")
    calls: list[tuple[list[str], Path]] = []

    def run(argv, *, cwd):
        calls.append((argv, cwd))
        if len(calls) == 1:
            target = tmp_path / "dist/lean-preview-candidate/tianshu-0.4.2.tar.gz"
            target.parent.mkdir(parents=True)
            _write_sdist(target)
            return SimpleNamespace(
                returncode=0,
                stdout=b"Successfully built tianshu-0.4.2.tar.gz\n",
                stderr=b"sdist warning\n",
            )
        target = tmp_path / "dist/lean-preview-candidate/from-sdist"
        target.mkdir(parents=True)
        _write_wheel(target / "tianshu-0.4.2-py3-none-any.whl")
        return SimpleNamespace(
            returncode=0,
            stdout=b"Successfully built tianshu-0.4.2-py3-none-any.whl\n",
            stderr=b"wheel warning\n",
        )

    monkeypatch.setattr(module, "run_trusted_local_process", run)
    path = module.record_build_provenance(
        output_root=tmp_path / "evidence/builds",
        batch_id="batch-1",
        source_commit=SOURCE_COMMIT,
    )

    sdist = tmp_path / "dist/lean-preview-candidate/tianshu-0.4.2.tar.gz"
    sdist_hash = hashlib.sha256(sdist.read_bytes()).hexdigest()
    extracted_root = (
        tmp_path / "dist/lean-preview-candidate/extracted" / sdist_hash / "tianshu-0.4.2"
    )
    assert calls == [
        (
            [
                module.sys.executable,
                "-m",
                "build",
                "--sdist",
                "--outdir",
                "dist/lean-preview-candidate",
            ],
            tmp_path,
        ),
        (
            [
                module.sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                "../../../from-sdist",
            ],
            extracted_root,
        ),
    ]
    assert extracted_root.joinpath("pyproject.toml").read_bytes() == b"[build-system]\n"
    payload = json.loads(path.read_bytes())
    assert payload["sdist"] == {
        "command": module.SDIST_BUILD_COMMAND,
        "cwd": ".",
        "exit_code": 0,
        "log_ref": "logs/sdist.log",
        "log_sha256": hashlib.sha256(
            b"Successfully built tianshu-0.4.2.tar.gz\nsdist warning\n"
        ).hexdigest(),
        "sha256": sdist_hash,
    }
    assert payload["wheel"]["command"] == module.WHEEL_BUILD_COMMAND
    assert payload["wheel"]["cwd"] == extracted_root.relative_to(tmp_path).as_posix()
    assert payload["wheel"]["exit_code"] == 0
    assert payload["wheel"]["source_sdist_sha256"] == sdist_hash


def test_run_build_keeps_stdout_and_stderr_before_raising_on_nonzero_exit(
    tmp_path: Path, monkeypatch
) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "run_trusted_local_process",
        lambda argv, *, cwd: SimpleNamespace(
            returncode=9,
            stdout=b"build stdout\n",
            stderr=b"build stderr\n",
        ),
    )
    log_path = tmp_path / "failed.log"

    with pytest.raises(module.BuildRecordingError, match=r"Wheel build failed \(exit 9\)"):
        module._run_build(["python", "-m", "build"], cwd=tmp_path, log_path=log_path, label="Wheel")

    assert log_path.read_bytes() == b"build stdout\nbuild stderr\n"
