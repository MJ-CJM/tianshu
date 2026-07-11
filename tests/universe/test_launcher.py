"""Unit tests for launcher.resolve_boot_plan — pure function, no execvpe."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.universe.deployer import DeployPointer, DeployRecord
from tianshu.universe.launcher import SecurityBoundaryError, resolve_boot_plan


def test_resolve_boot_plan_main_repo(tmp_path: Path) -> None:
    """Empty pointer → cwd is None and env is returned unchanged."""
    pointer = DeployPointer(tmp_path / "deploy.json")
    base_env = {"PATH": "/usr/bin", "HOME": "/home/user"}

    cwd, env = resolve_boot_plan(pointer, base_env)

    assert cwd is None
    assert env == base_env
    assert "PYTHONPATH" not in env


def test_resolve_boot_plan_variant(tmp_path: Path) -> None:
    """Pointer with worktree → cwd=worktree and PYTHONPATH first segment == worktree/src."""
    pointer = DeployPointer(tmp_path / "deploy.json")
    worktree = str(tmp_path / "worktrees" / "uni-abc")
    pointer.write(DeployRecord(ref="universe/uni-abc", worktree=worktree), None)

    base_env: dict[str, str] = {}
    cwd, env = resolve_boot_plan(pointer, base_env)

    assert cwd == worktree
    expected_src = str(Path(worktree) / "src")
    assert env["PYTHONPATH"] == expected_src


def test_resolve_boot_plan_variant_prepends_to_existing_pythonpath(tmp_path: Path) -> None:
    """Existing PYTHONPATH is preserved after worktree/src."""
    pointer = DeployPointer(tmp_path / "deploy.json")
    worktree = str(tmp_path / "worktrees" / "uni-xyz")
    pointer.write(DeployRecord(ref="universe/uni-xyz", worktree=worktree), None)

    base_env = {"PYTHONPATH": "/old/path"}
    cwd, env = resolve_boot_plan(pointer, base_env)

    expected_src = str(Path(worktree) / "src")
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == expected_src
    assert "/old/path" in parts


def test_resolve_boot_plan_no_worktree_in_record(tmp_path: Path) -> None:
    """Record with worktree=None behaves like main repo (no PYTHONPATH injection)."""
    pointer = DeployPointer(tmp_path / "deploy.json")
    pointer.write(DeployRecord(ref="abc123", worktree=None), None)

    base_env: dict[str, str] = {}
    cwd, env = resolve_boot_plan(pointer, base_env)

    assert cwd is None
    assert "PYTHONPATH" not in env


def test_secure_remote_refuses_variant_without_security_manifest(tmp_path: Path) -> None:
    pointer = DeployPointer(tmp_path / "deploy.json")
    worktree = tmp_path / "worktrees" / "unsafe"
    worktree.mkdir(parents=True)
    pointer.write(DeployRecord(ref="universe/unsafe", worktree=str(worktree)), None)

    with pytest.raises(SecurityBoundaryError, match="disabled"):
        resolve_boot_plan(pointer, {"TIANSHU_SECURITY_MODE": "secure-remote"})


@pytest.mark.parametrize(
    "manifest",
    [
        "{}",
        '{"security_boundary_version": 0}',
        '{"security_boundary_version": "1"}',
        "not-json",
    ],
)
def test_secure_remote_refuses_invalid_security_manifest(
    tmp_path: Path,
    manifest: str,
) -> None:
    pointer = DeployPointer(tmp_path / "deploy.json")
    worktree = tmp_path / "worktrees" / "unsafe"
    worktree.mkdir(parents=True)
    (worktree / ".tianshu-security.json").write_text(manifest, encoding="utf-8")
    pointer.write(DeployRecord(ref="universe/unsafe", worktree=str(worktree)), None)

    with pytest.raises(SecurityBoundaryError, match="disabled"):
        resolve_boot_plan(pointer, {"TIANSHU_SECURITY_MODE": "secure-remote"})


def test_secure_remote_refuses_variant_even_with_current_security_manifest(
    tmp_path: Path,
) -> None:
    pointer = DeployPointer(tmp_path / "deploy.json")
    worktree = tmp_path / "worktrees" / "safe"
    worktree.mkdir(parents=True)
    (worktree / ".tianshu-security.json").write_text(
        '{"security_boundary_version": 1}',
        encoding="utf-8",
    )
    pointer.write(DeployRecord(ref="universe/safe", worktree=str(worktree)), None)

    with pytest.raises(SecurityBoundaryError, match="disabled"):
        resolve_boot_plan(
            pointer,
            {"TIANSHU_SECURITY_MODE": "secure-remote"},
        )


def test_launcher_main_uses_settings_resolved_security_mode(monkeypatch, tmp_path: Path) -> None:
    import tianshu.universe.launcher as launcher

    pointer_path = tmp_path / "deploy.json"
    pointer = DeployPointer(pointer_path)
    worktree = tmp_path / "worktrees" / "selected"
    worktree.mkdir(parents=True)
    pointer.write(DeployRecord(ref="universe/selected", worktree=str(worktree)), None)
    monkeypatch.setattr(launcher, "DEFAULT_POINTER", pointer_path)
    monkeypatch.setattr(
        launcher,
        "TianshuSettings",
        lambda: SimpleNamespace(
            security_mode="secure-remote",
            host="127.0.0.1",
            port=8000,
        ),
    )
    monkeypatch.delenv("TIANSHU_SECURITY_MODE", raising=False)

    with pytest.raises(SecurityBoundaryError, match="disabled"):
        launcher.main()
