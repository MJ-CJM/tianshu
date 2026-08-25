"""Pi runtime releases are immutable and rehydrate without PATH fallback."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tianshu.executor.adapters import ExecutorGenerationUnavailable
from tianshu.executor.keqing import generation as generation_module
from tianshu.executor.keqing import versions as versions_module
from tianshu.executor.keqing.generation import (
    PI_MATERIALIZER_VERSION,
    PiGenerationDelegate,
    PiReleaseMaterializationError,
    PiReleaseMaterializer,
)
from tianshu.executor.keqing.versions import detect_installed_version
from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import RuntimeReleaseV1


def _pi_install(tmp_path: Path, *, version: str = "0.83.0") -> Path:
    package = tmp_path / "pi-package"
    executable = package / "bin" / "pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    dependency = package / "node_modules" / "dependency" / "index.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("export const value = 1;\n")
    link = package / "node_modules" / "dependency-link"
    link.symlink_to("dependency")
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": version,
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    return executable


def _materializer(tmp_path: Path, *, root: Path | None = None) -> PiReleaseMaterializer:
    return PiReleaseMaterializer(
        execution_gateway=object(),
        release_root=tmp_path / "managed-releases",
        root=root,
    )


def _replace_release(release: RuntimeReleaseV1, **updates: object) -> RuntimeReleaseV1:
    material = release.model_dump(mode="json", exclude={"release_digest"})
    material.update(updates)
    return RuntimeReleaseV1(
        **material,
        release_digest=canonical_sha256(material),
    )


def test_existing_managed_release_root_is_hardened_before_use(tmp_path: Path) -> None:
    release_root = tmp_path / "managed-releases"
    release_root.mkdir()
    release_root.chmod(0o777)

    _materializer(tmp_path)

    assert release_root.stat().st_mode & 0o777 == 0o700


def test_release_root_symlink_swap_during_hardening_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_root = tmp_path / "managed-releases"
    moved_root = tmp_path / "moved-releases"
    real_fchmod = os.fchmod

    def swap_after_hardening(descriptor: int, mode: int) -> None:
        real_fchmod(descriptor, mode)
        release_root.rename(moved_root)
        release_root.symlink_to(moved_root)

    monkeypatch.setattr(os, "fchmod", swap_after_hardening)

    with pytest.raises(PiReleaseMaterializationError, match="permissions are unsafe"):
        _materializer(tmp_path)


def test_release_root_identity_swap_after_initialization_fails_closed(
    tmp_path: Path,
) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release_root = tmp_path / "managed-releases"
    moved_root = tmp_path / "moved-releases"
    release_root.rename(moved_root)
    release_root.symlink_to(moved_root)

    with pytest.raises(PiReleaseMaterializationError, match="managed release root"):
        materializer.create_release(binary=str(executable))


def test_bundle_pairs_single_and_session_to_same_release(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path, root=tmp_path / "runs")

    release = materializer.create_release(binary=str(executable))
    bundle = materializer.materialize(release)

    assert bundle.release is release
    assert bundle.binary_path == str(
        tmp_path / "managed-releases" / release.package_digest / "bin" / "pi"
    )
    assert Path(bundle.binary_path).is_symlink()
    assert not Path(bundle.binary_path).readlink().is_absolute()
    assert bundle.single_adapter.binary_path == bundle.binary_path
    assert bundle.session_adapter.binary_path == bundle.binary_path
    assert bundle.single_adapter.build_argv("test")[0] == bundle.binary_path
    assert bundle.session_adapter.build_session_argv()[0] == bundle.binary_path
    assert bundle.executor_adapter.delegate is bundle.generation_delegate
    assert bundle.generation_delegate.delegate is bundle.session_delegate
    assert bundle.single_delegate._adapter is bundle.single_adapter  # noqa: SLF001
    assert bundle.session_delegate._adapter is bundle.session_adapter  # noqa: SLF001


def test_rehydrate_does_not_consult_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))

    def reject_path_lookup(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("rehydrate consulted PATH")

    monkeypatch.setattr(
        "tianshu.executor.keqing.versions.shutil.which",
        reject_path_lookup,
    )

    bundle = materializer.rehydrate(release)
    assert bundle.binary_path == release.binary_path


def test_npm_symlink_keeps_canonical_absolute_pi_launch_path(tmp_path: Path) -> None:
    package = tmp_path / "pi-package"
    target = package / "dist" / "cli.js"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o700)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "dist/cli.js"},
            }
        )
    )
    launch_path = tmp_path / "bin" / "pi"
    launch_path.parent.mkdir()
    launch_path.symlink_to(target)

    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(launch_path))
    bundle = materializer.rehydrate(release)

    assert release.binary_path != str(launch_path.absolute())
    assert release.package_entrypoint == "dist/cli.js"
    assert bundle.single_adapter.is_canonical_argv(bundle.single_adapter.build_argv("probe"))
    assert bundle.single_adapter.is_canonical_argv(bundle.session_adapter.build_session_argv())


def test_managed_binary_and_package_drift_fail_closed(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))

    managed_executable = Path(release.binary_path).resolve()
    managed_executable.write_text("#!/bin/sh\nexit 7\n")
    with pytest.raises(PiReleaseMaterializationError, match="binary_digest drift"):
        materializer.rehydrate(release)

    managed_executable.write_text("#!/bin/sh\nexit 0\n")
    package = managed_executable.parent.parent / "package.json"
    package.write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
                "drift": True,
            }
        )
    )
    with pytest.raises(PiReleaseMaterializationError, match="package_digest drift"):
        materializer.rehydrate(release)


def test_retained_manifest_survives_but_wire_and_materializer_drift_fail_closed(
    tmp_path: Path,
) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))

    changed_manifest = release.model_dump(mode="json")["manifest"]
    assert isinstance(changed_manifest, dict)
    changed_manifest["display_name"] = "Drifted Pi"
    manifest_release = _replace_release(
        release,
        manifest=changed_manifest,
        manifest_hash=canonical_sha256(changed_manifest),
    )
    restored = materializer.rehydrate(manifest_release)
    assert restored.manifest.display_name == "Drifted Pi"

    wire_release = _replace_release(release, pi_wire_version=release.pi_wire_version + 1)
    with pytest.raises(PiReleaseMaterializationError, match="pi_wire_version drift"):
        materializer.rehydrate(wire_release)

    materializer_release = _replace_release(
        release,
        materializer_version=str(int(PI_MATERIALIZER_VERSION) + 1),
    )
    with pytest.raises(PiReleaseMaterializationError, match="materializer_version drift"):
        materializer.rehydrate(materializer_release)


def test_source_upgrade_keeps_old_and_new_managed_releases_side_by_side(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    old_release = materializer.create_release(binary=str(executable))

    executable.write_text("#!/bin/sh\nexit 2\n")
    dependency = executable.parent.parent / "node_modules" / "dependency" / "index.js"
    dependency.write_text("export const value = 2;\n")
    package = executable.parent.parent / "package.json"
    package.write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.84.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    new_release = materializer.create_release(binary=str(executable))

    assert old_release.package_digest != new_release.package_digest
    assert old_release.binary_path != new_release.binary_path
    assert materializer.rehydrate(old_release).release is old_release
    assert materializer.rehydrate(new_release).release is new_release


def test_dependency_drift_is_part_of_package_digest(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))
    managed_package = Path(release.binary_path).parent.parent / "package"
    dependency = managed_package / "node_modules" / "dependency" / "index.js"
    dependency.write_text("export const value = 999;\n")

    with pytest.raises(PiReleaseMaterializationError, match="package_digest drift"):
        materializer.rehydrate(release)


async def test_generation_delegate_checks_managed_tree_before_execute(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))
    bundle = materializer.materialize(release)
    managed_package = Path(release.binary_path).parent.parent / "package"
    dependency = managed_package / "node_modules" / "dependency" / "index.js"
    dependency.write_text("export const value = 999;\n")

    with pytest.raises(ExecutorGenerationUnavailable, match="pinned Pi release") as captured:
        await bundle.generation_delegate.execute(object())

    assert isinstance(captured.value.__cause__, PiReleaseMaterializationError)


async def test_generation_verification_does_not_block_the_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _pi_install(tmp_path)
    materializer = _materializer(tmp_path)
    release = materializer.create_release(binary=str(executable))
    started = threading.Event()
    release_verification = threading.Event()
    event_loop_thread = threading.get_ident()

    def blocking_verify(_release: RuntimeReleaseV1) -> None:
        assert threading.get_ident() != event_loop_thread
        started.set()
        if not release_verification.wait(timeout=1):
            raise AssertionError("verification thread was not released")

    monkeypatch.setattr(materializer, "verify_release", blocking_verify)
    delegate = AsyncMock()
    delegate.execute.return_value = "done"
    generation_delegate = PiGenerationDelegate(
        materializer=materializer,
        release=release,
        delegate=delegate,
    )
    execution = asyncio.create_task(generation_delegate.execute(object()))

    try:
        assert await asyncio.to_thread(started.wait, 1)
        ticked = asyncio.Event()

        async def tick_once() -> None:
            ticked.set()

        ticker = asyncio.create_task(tick_once())
        await asyncio.wait_for(ticked.wait(), timeout=0.2)
        await ticker
        assert not execution.done()
    finally:
        release_verification.set()

    assert await execution == "done"
    delegate.execute.assert_awaited_once()


def test_managed_copy_preserves_internal_relative_symlinks(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    release = _materializer(tmp_path).create_release(binary=str(executable))
    managed_package = Path(release.binary_path).parent.parent / "package"
    managed_link = managed_package / "node_modules" / "dependency-link"

    assert managed_link.is_symlink()
    assert managed_link.readlink() == Path("dependency")


def test_stage_rejects_superseded_pi_package_name(tmp_path: Path) -> None:
    executable = _pi_install(tmp_path)
    package = executable.parent.parent / "package.json"
    package.write_text(
        json.dumps(
            {
                "name": "@mariozechner/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )

    with pytest.raises(PiReleaseMaterializationError, match="package name"):
        _materializer(tmp_path).create_release(binary=str(executable))


def test_status_version_detection_is_lightweight_and_swallows_io_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _pi_install(tmp_path)
    monkeypatch.setenv("PATH", str(executable.parent))

    def tree_hash_forbidden(_root: Path) -> str:
        raise AssertionError("status hashed the complete package tree")

    monkeypatch.setattr(
        versions_module,
        "_canonical_package_tree_digest",
        tree_hash_forbidden,
    )
    assert detect_installed_version("pi", backend="pi") == "0.83.0"

    def metadata_io_failure(_binary: Path) -> object:
        raise OSError("unreadable package metadata")

    monkeypatch.setattr(versions_module, "_package_metadata", metadata_io_failure)
    assert detect_installed_version("pi", backend="pi") is None


def test_stage_requires_package_identity(tmp_path: Path) -> None:
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)

    with pytest.raises(PiReleaseMaterializationError, match="package name"):
        _materializer(tmp_path).create_release(binary=str(executable))


def test_generation_module_never_resolves_path_during_materialize(tmp_path: Path) -> None:
    """Static guard: only create_release may use the stage resolver."""

    source = Path(generation_module.__file__).read_text()
    assert source.count("resolve_stage_executable(") == 1
