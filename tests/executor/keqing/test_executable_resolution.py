"""Strict Pi provenance and lightweight ordinary-execution resolution."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tianshu.executor.keqing import versions as versions_module
from tianshu.executor.keqing.versions import (
    ExecutableInspectionError,
    detect_installed_version,
    resolve_execution_executable,
    resolve_stage_executable,
)


@pytest.mark.parametrize(
    ("backend", "binary"),
    [
        ("claude-code", "claude"),
        ("codex", "codex"),
        ("opencode", "opencode"),
        ("pi", "pi"),
    ],
)
def test_builtin_backends_resolve_absolute_path_and_package_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    binary: str,
) -> None:
    package = tmp_path / backend
    executable = package / "bin" / binary
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    package_name = "@earendil-works/pi-coding-agent" if backend == "pi" else backend
    (package / "package.json").write_text(json.dumps({"name": package_name, "version": "1.2.3"}))
    monkeypatch.setenv("PATH", str(executable.parent))

    identity = resolve_stage_executable(binary, backend=backend)
    execution_identity = resolve_execution_executable(binary, backend=backend)

    assert identity.binary_path == str(executable.resolve())
    assert Path(identity.binary_path).is_absolute()
    assert identity.version == "1.2.3"
    assert identity.version_source == "package_json"
    assert identity.binary_digest is not None
    assert identity.package_digest is not None
    assert execution_identity.binary_path == identity.binary_path
    assert execution_identity.version == "1.2.3"
    assert execution_identity.version_source == "package_json"
    assert detect_installed_version(binary, backend=backend) == "1.2.3"


def test_missing_path_is_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", os.devnull)

    with pytest.raises(ExecutableInspectionError, match="not found"):
        resolve_stage_executable("pi", backend="pi")
    with pytest.raises(ExecutableInspectionError, match="not found"):
        resolve_execution_executable("pi", backend="pi")


def test_unknown_layout_is_unverified_but_not_rejected(tmp_path: Path) -> None:
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)

    identity = resolve_stage_executable(str(executable), backend="codex")

    assert identity.binary_path == str(executable.resolve())
    assert identity.version is None
    assert identity.version_source == "unverified"
    assert identity.package_digest is None


def test_invalid_package_metadata_does_not_block_ordinary_execution(tmp_path: Path) -> None:
    package = tmp_path / "pi-package"
    executable = package / "bin" / "pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (package / "package.json").write_text("{not-json")

    execution = resolve_execution_executable(str(executable), backend="pi")

    assert execution.binary_path == str(executable.resolve())
    assert execution.version is None
    assert execution.version_source == "unverified"
    assert detect_installed_version(str(executable), backend="pi") is None
    with pytest.raises(ExecutableInspectionError, match="package manifest"):
        resolve_stage_executable(str(executable), backend="pi")


def test_package_tree_escape_does_not_block_execution_but_strict_pi_stage_rejects(
    tmp_path: Path,
) -> None:
    package = tmp_path / "pi-package"
    executable = package / "bin" / "pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    outside = tmp_path / "outside-dependency"
    outside.mkdir()
    node_modules = package / "node_modules"
    node_modules.mkdir()
    (node_modules / "external").symlink_to(Path("../..") / outside.name)

    execution = resolve_execution_executable(str(executable), backend="pi")

    assert execution.binary_path == str(executable.resolve())
    assert execution.version == "0.83.0"
    assert execution.version_source == "package_json"
    with pytest.raises(ExecutableInspectionError, match="symlink escapes"):
        resolve_stage_executable(str(executable), backend="pi")


def test_unreadable_package_metadata_is_advisory_for_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)

    def unreadable(_binary: Path) -> object:
        raise OSError("metadata unreadable")

    monkeypatch.setattr(versions_module, "_package_metadata", unreadable)

    execution = resolve_execution_executable(str(executable), backend="pi")

    assert execution.binary_path == str(executable.resolve())
    assert execution.version is None
    assert execution.version_source == "unverified"
    assert detect_installed_version(str(executable), backend="pi") is None


def test_unknown_pi_install_is_not_reported_as_the_pinned_version(tmp_path: Path) -> None:
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)

    execution = resolve_execution_executable(str(executable), backend="pi")

    assert execution.version is None
    assert execution.version_source == "unverified"
    assert detect_installed_version(str(executable), backend="pi") is None
