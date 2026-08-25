"""Immutable Pi runtime-generation materialization and paired executor bundle."""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tianshu.executor.adapters import ExecutorGenerationUnavailable
from tianshu.executor.adapters.protocol import DelegatingExecutorAdapter
from tianshu.executor.capabilities import ExecutorCapabilityManifestV1, pi_manifest
from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.executor.keqing.executor import KeqingExecutor
from tianshu.executor.keqing.pi_adapter import PiAdapter, PiSessionAdapter
from tianshu.executor.keqing.pi_wire import VERIFIED_SESSION_VERSION
from tianshu.executor.keqing.session_executor import KeqingSessionExecutor
from tianshu.executor.keqing.versions import (
    PI_PACKAGE_NAME,
    ExecutableInspectionError,
    ExecutableProvenance,
    inspect_persisted_executable,
    resolve_stage_executable,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import RuntimeReleaseV1

PI_GENERATION_SCOPE = "executor:keqing:pi"
PI_ADAPTER_ID = "keqing:pi"
PI_SINGLE_ARGV_SHAPE = "pi --mode json --no-session <prompt> [--model <model>]"
PI_SESSION_ARGV_SHAPE = (
    "pi --mode rpc (--session-dir <dir> [--continue] | --no-session) [--model <model>]"
)
PI_MATERIALIZER_ID = "tianshu.executor.keqing.pi"
PI_MATERIALIZER_VERSION = "1"


class PiReleaseMaterializationError(RuntimeError):
    """Persisted Pi release material is unavailable or has drifted."""


@dataclass(frozen=True, slots=True)
class PiGenerationDelegate:
    """Verify immutable generation material immediately before every execution."""

    materializer: PiReleaseMaterializer
    release: RuntimeReleaseV1
    delegate: KeqingSessionExecutor

    async def execute(self, edict: Any, **kwargs: Any) -> Any:
        try:
            await asyncio.to_thread(self.materializer.verify_release, self.release)
        except PiReleaseMaterializationError as exc:
            raise ExecutorGenerationUnavailable(
                f"pinned Pi release is unavailable: {self.release.release_digest}"
            ) from exc
        return await self.delegate.execute(edict, **kwargs)

    async def cancel(self, run_id: str) -> bool:
        cancel = getattr(self.delegate, "cancel", None)
        if cancel is None:
            return False
        return bool(await cancel(run_id))


@dataclass(frozen=True, slots=True)
class PiGenerationBundle:
    """Single-shot and RPC delegates pinned to one immutable Pi release."""

    release: RuntimeReleaseV1
    manifest: ExecutorCapabilityManifestV1
    single_adapter: PiAdapter
    session_adapter: PiSessionAdapter
    single_delegate: KeqingExecutor
    session_delegate: KeqingSessionExecutor
    generation_delegate: PiGenerationDelegate
    executor_adapter: DelegatingExecutorAdapter

    @property
    def scope(self) -> str:
        return self.release.scope

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def binary_path(self) -> str:
        return self.release.binary_path

    @property
    def manifest_content_hash(self) -> str:
        return self.release.manifest_hash

    @property
    def release_digest(self) -> str:
        return self.release.release_digest


class PiReleaseMaterializer:
    """Create and rehydrate Pi releases without mutating live adapters."""

    def __init__(
        self,
        execution_gateway: ExecutionGateway | None = None,
        *,
        release_root: Path,
        root: Path | None = None,
        llm: Any = None,
        follow_up_rounds: int = 3,
        gateway_base_url: str | None = None,
        token_ttl_seconds: float = 3600.0,
        default_model_provider: Callable[[str], str | None] | None = None,
    ) -> None:
        self._execution_gateway = execution_gateway or ExecutionGateway()
        self._release_root, self._release_root_identity = self._prepare_release_root(release_root)
        self._root = root
        self._llm = llm
        self._follow_up_rounds = follow_up_rounds
        self._gateway_base_url = gateway_base_url
        self._token_ttl_seconds = token_ttl_seconds
        self._default_model_provider = default_model_provider

    @staticmethod
    def _prepare_release_root(release_root: Path) -> tuple[Path, tuple[int, int]]:
        expanded = release_root.expanduser()
        if not expanded.is_absolute():
            raise PiReleaseMaterializationError("managed release root must be absolute")
        try:
            parent = expanded.parent.resolve(strict=True)
            if parent == parent.parent:
                raise PiReleaseMaterializationError(
                    "managed release root cannot be adjacent to the filesystem root"
                )
            parent_status = parent.stat()
            if stat.S_IMODE(parent_status.st_mode) & 0o022 and not (
                parent_status.st_mode & stat.S_ISVTX
            ):
                raise PiReleaseMaterializationError(
                    "managed release root parent permissions are unsafe"
                )
            canonical = parent / expanded.name
            if canonical.is_symlink():
                raise PiReleaseMaterializationError("managed release root cannot be a symlink")
            canonical.mkdir(mode=0o700, exist_ok=True)
            if not canonical.is_dir() or canonical.resolve(strict=True) != canonical:
                raise PiReleaseMaterializationError("managed release root is invalid")
            descriptor = os.open(
                canonical,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                before = os.fstat(descriptor)
                if hasattr(os, "geteuid") and before.st_uid != os.geteuid():
                    raise PiReleaseMaterializationError(
                        "managed release root must be owned by the current user"
                    )
                os.fchmod(descriptor, 0o700)
                secured = os.fstat(descriptor)
                current = canonical.lstat()
                if (
                    stat.S_IMODE(secured.st_mode) != 0o700
                    or not stat.S_ISDIR(current.st_mode)
                    or (secured.st_dev, secured.st_ino) != (current.st_dev, current.st_ino)
                ):
                    raise PiReleaseMaterializationError(
                        "managed release root permissions are unsafe"
                    )
            finally:
                os.close(descriptor)
            return canonical, (secured.st_dev, secured.st_ino)
        except PiReleaseMaterializationError:
            raise
        except (OSError, RuntimeError) as exc:
            raise PiReleaseMaterializationError("managed release root is unavailable") from exc

    def _assert_release_root(self) -> None:
        try:
            descriptor = os.open(
                self._release_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                secured = os.fstat(descriptor)
                current = self._release_root.lstat()
                if (
                    not stat.S_ISDIR(current.st_mode)
                    or (secured.st_dev, secured.st_ino) != self._release_root_identity
                    or (current.st_dev, current.st_ino) != self._release_root_identity
                    or stat.S_IMODE(secured.st_mode) != 0o700
                    or (hasattr(os, "geteuid") and secured.st_uid != os.geteuid())
                ):
                    raise PiReleaseMaterializationError("managed release root identity changed")
            finally:
                os.close(descriptor)
        except PiReleaseMaterializationError:
            raise
        except (OSError, RuntimeError) as exc:
            raise PiReleaseMaterializationError("managed release root is unavailable") from exc

    def _install_managed_package(
        self,
        executable: ExecutableProvenance,
    ) -> ExecutableProvenance:
        self._assert_release_root()
        source_root_value = executable.package_root
        package_digest = executable.package_digest
        entrypoint = executable.package_entrypoint
        if source_root_value is None or package_digest is None or entrypoint is None:
            raise PiReleaseMaterializationError("pi package tree is unavailable")

        destination = self._release_root / package_digest
        if destination.exists():
            return self._verified_managed_provenance(destination, executable)

        temporary = Path(
            tempfile.mkdtemp(prefix=f".{package_digest[:12]}-", dir=self._release_root)
        )
        try:
            package_destination = temporary / "package"
            try:
                shutil.copytree(
                    Path(source_root_value),
                    package_destination,
                    symlinks=True,
                    copy_function=shutil.copy2,
                )
                bin_dir = temporary / "bin"
                bin_dir.mkdir(mode=0o700)
                managed_binary = bin_dir / "pi"
                managed_target = package_destination / entrypoint
                relative_target = os.path.relpath(managed_target, start=bin_dir)
                managed_binary.symlink_to(relative_target)
                self._verified_managed_provenance(temporary, executable)
            except (OSError, RuntimeError, ExecutableInspectionError) as exc:
                raise PiReleaseMaterializationError("pi package cannot be copied") from exc

            try:
                temporary.rename(destination)
            except OSError as exc:
                if not destination.exists():
                    raise PiReleaseMaterializationError(
                        "managed pi release cannot be published"
                    ) from exc
            return self._verified_managed_provenance(destination, executable)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)

    def _verified_managed_provenance(
        self,
        release_directory: Path,
        expected: ExecutableProvenance,
    ) -> ExecutableProvenance:
        self._assert_release_root()
        if release_directory.is_symlink() or release_directory.parent != self._release_root:
            raise PiReleaseMaterializationError("managed release directory is invalid")
        managed_binary = release_directory / "bin" / "pi"
        if expected.package_entrypoint is None:
            raise PiReleaseMaterializationError("pi package entrypoint is unavailable")
        self._validate_relative_launch_link(
            managed_binary,
            expected_entrypoint=expected.package_entrypoint,
        )
        try:
            actual = inspect_persisted_executable(str(managed_binary), backend="pi")
        except ExecutableInspectionError as exc:
            raise PiReleaseMaterializationError("managed pi package is unavailable") from exc
        checks = (
            ("binary_digest", actual.binary_digest, expected.binary_digest),
            ("package_name", actual.package_name, expected.package_name),
            ("package_entrypoint", actual.package_entrypoint, expected.package_entrypoint),
            ("package_digest", actual.package_digest, expected.package_digest),
            ("cli_version", actual.version, expected.version),
            ("cli_version_source", actual.version_source, expected.version_source),
        )
        for field, observed, wanted in checks:
            if observed != wanted:
                raise PiReleaseMaterializationError(f"managed {field} drift")
        return actual

    @staticmethod
    def _validate_relative_launch_link(
        binary_path: Path,
        *,
        expected_entrypoint: str,
    ) -> None:
        try:
            if not binary_path.is_symlink():
                raise PiReleaseMaterializationError("managed pi launch path is not a symlink")
            target = Path(os.readlink(binary_path))
            if target.is_absolute():
                raise PiReleaseMaterializationError("managed pi launch symlink is not relative")
            actual = (binary_path.parent / target).resolve(strict=True)
            package_root = binary_path.parent.parent / "package"
            if package_root.is_symlink() or package_root.resolve(strict=True) != package_root:
                raise PiReleaseMaterializationError("managed package root is invalid")
            expected = (package_root / expected_entrypoint).resolve(strict=True)
            if actual != expected:
                raise PiReleaseMaterializationError("managed pi launch symlink drift")
            actual.relative_to(package_root.resolve(strict=True))
        except PiReleaseMaterializationError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise PiReleaseMaterializationError("managed pi launch symlink is unavailable") from exc

    def create_release(
        self,
        *,
        manifest: ExecutorCapabilityManifestV1 | None = None,
        binary: str = "pi",
    ) -> RuntimeReleaseV1:
        """Copy one discovered Pi package into managed content-addressed storage."""

        effective_manifest = manifest or pi_manifest()
        self._validate_expected_manifest(effective_manifest)
        try:
            executable = resolve_stage_executable(binary, backend="pi")
        except ExecutableInspectionError as exc:
            raise PiReleaseMaterializationError("pi executable cannot be staged") from exc
        if Path(executable.binary_path).name != "pi":
            raise PiReleaseMaterializationError("pi launch path is not canonical")
        if executable.package_name != PI_PACKAGE_NAME:
            raise PiReleaseMaterializationError("pi package name is not canonical")
        if (
            executable.package_root is None
            or executable.package_entrypoint is None
            or executable.package_digest is None
        ):
            raise PiReleaseMaterializationError("pi package tree is unavailable")
        if executable.version is None:
            raise PiReleaseMaterializationError("pi CLI version is unavailable")

        managed = self._install_managed_package(executable)
        if (
            managed.version is None
            or managed.package_name is None
            or managed.package_entrypoint is None
            or managed.package_digest is None
        ):
            raise PiReleaseMaterializationError("managed pi package identity is incomplete")

        material: dict[str, object] = {
            "schema_version": 1,
            "scope": PI_GENERATION_SCOPE,
            "manifest": effective_manifest.model_dump(mode="json"),
            "manifest_hash": effective_manifest.content_hash,
            "cli_version": managed.version,
            "cli_version_source": managed.version_source,
            "binary_path": managed.binary_path,
            "binary_digest": managed.binary_digest,
            "package_name": managed.package_name,
            "package_entrypoint": managed.package_entrypoint,
            "package_digest": managed.package_digest,
            "single_argv_shape": PI_SINGLE_ARGV_SHAPE,
            "session_argv_shape": PI_SESSION_ARGV_SHAPE,
            "pi_wire_version": VERIFIED_SESSION_VERSION,
            "materializer_id": PI_MATERIALIZER_ID,
            "materializer_version": PI_MATERIALIZER_VERSION,
        }
        return RuntimeReleaseV1(
            scope=PI_GENERATION_SCOPE,
            manifest=effective_manifest.model_dump(mode="json"),
            manifest_hash=effective_manifest.content_hash,
            cli_version=managed.version,
            cli_version_source=managed.version_source,
            binary_path=managed.binary_path,
            binary_digest=managed.binary_digest,
            package_name=managed.package_name,
            package_entrypoint=managed.package_entrypoint,
            package_digest=managed.package_digest,
            single_argv_shape=PI_SINGLE_ARGV_SHAPE,
            session_argv_shape=PI_SESSION_ARGV_SHAPE,
            pi_wire_version=VERIFIED_SESSION_VERSION,
            materializer_id=PI_MATERIALIZER_ID,
            materializer_version=PI_MATERIALIZER_VERSION,
            release_digest=canonical_sha256(material),
        )

    def materialize(self, release: RuntimeReleaseV1) -> PiGenerationBundle:
        """Strictly rehydrate a bundle from persisted material, never from PATH."""

        manifest = self._validate_release(release)
        single_adapter = PiAdapter(binary_path=release.binary_path)
        session_adapter = PiSessionAdapter(binary_path=release.binary_path)
        single_delegate = KeqingExecutor(
            root=self._root,
            execution_gateway=self._execution_gateway,
            default_model_provider=self._default_model_provider,
            adapter=single_adapter,
        )
        session_delegate = KeqingSessionExecutor(
            root=self._root,
            execution_gateway=self._execution_gateway,
            llm=self._llm,
            follow_up_rounds=self._follow_up_rounds,
            gateway_base_url=self._gateway_base_url,
            token_ttl_seconds=self._token_ttl_seconds,
            default_model_provider=self._default_model_provider,
            adapter=session_adapter,
        )
        generation_delegate = PiGenerationDelegate(
            materializer=self,
            release=release,
            delegate=session_delegate,
        )
        executor_adapter = DelegatingExecutorAdapter(
            adapter_id=manifest.adapter_id,
            manifest=manifest,
            delegate=generation_delegate,
        )
        return PiGenerationBundle(
            release=release,
            manifest=manifest,
            single_adapter=single_adapter,
            session_adapter=session_adapter,
            single_delegate=single_delegate,
            session_delegate=session_delegate,
            generation_delegate=generation_delegate,
            executor_adapter=executor_adapter,
        )

    def rehydrate(self, release: RuntimeReleaseV1) -> PiGenerationBundle:
        """Explicit restart-oriented alias for strict materialization."""

        return self.materialize(release)

    def verify_release(self, release: RuntimeReleaseV1) -> None:
        """Fail closed if persisted managed material is unavailable or has drifted."""

        self._validate_release(release)

    def _validate_release(
        self,
        release: RuntimeReleaseV1,
    ) -> ExecutorCapabilityManifestV1:
        self._assert_release_root()
        material = release.model_dump(mode="json", exclude={"release_digest"})
        if release.release_digest != canonical_sha256(material):
            raise PiReleaseMaterializationError("release_digest drift")
        if release.scope != PI_GENERATION_SCOPE:
            raise PiReleaseMaterializationError("scope drift")
        if release.materializer_id != PI_MATERIALIZER_ID:
            raise PiReleaseMaterializationError("materializer_id drift")
        if release.materializer_version != PI_MATERIALIZER_VERSION:
            raise PiReleaseMaterializationError("materializer_version drift")
        if release.pi_wire_version != VERIFIED_SESSION_VERSION:
            raise PiReleaseMaterializationError("pi_wire_version drift")
        if release.single_argv_shape != PI_SINGLE_ARGV_SHAPE:
            raise PiReleaseMaterializationError("single_argv_shape drift")
        if release.session_argv_shape != PI_SESSION_ARGV_SHAPE:
            raise PiReleaseMaterializationError("session_argv_shape drift")
        if Path(release.binary_path).name != "pi":
            raise PiReleaseMaterializationError("binary_path is not canonical for pi")
        expected_binary = self._release_root / release.package_digest / "bin" / "pi"
        if Path(release.binary_path) != expected_binary:
            raise PiReleaseMaterializationError("binary_path is outside managed release storage")
        self._validate_relative_launch_link(
            expected_binary,
            expected_entrypoint=release.package_entrypoint,
        )
        if release.package_name != PI_PACKAGE_NAME:
            raise PiReleaseMaterializationError("package_name drift")
        if release.manifest_hash != canonical_sha256(release.manifest):
            raise PiReleaseMaterializationError("manifest_hash drift")
        try:
            manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        except (TypeError, ValueError) as exc:
            raise PiReleaseMaterializationError("manifest is invalid") from exc
        if manifest.adapter_id != PI_ADAPTER_ID:
            raise PiReleaseMaterializationError("manifest adapter_id drift")

        try:
            executable = inspect_persisted_executable(release.binary_path, backend="pi")
        except ExecutableInspectionError as exc:
            raise PiReleaseMaterializationError("persisted pi executable is unavailable") from exc
        checks = (
            ("binary_path", executable.binary_path, release.binary_path),
            ("binary_digest", executable.binary_digest, release.binary_digest),
            ("package_name", executable.package_name, release.package_name),
            (
                "package_entrypoint",
                executable.package_entrypoint,
                release.package_entrypoint,
            ),
            ("package_digest", executable.package_digest, release.package_digest),
            ("cli_version", executable.version, release.cli_version),
            ("cli_version_source", executable.version_source, release.cli_version_source),
        )
        for field, actual, expected in checks:
            if actual != expected:
                raise PiReleaseMaterializationError(f"{field} drift")
        return manifest

    @staticmethod
    def _validate_expected_manifest(manifest: ExecutorCapabilityManifestV1) -> None:
        expected = pi_manifest()
        if manifest.adapter_id != PI_ADAPTER_ID:
            raise PiReleaseMaterializationError("manifest adapter_id drift")
        if manifest.content_hash != expected.content_hash:
            raise PiReleaseMaterializationError("manifest drift")


__all__ = [
    "PI_ADAPTER_ID",
    "PI_GENERATION_SCOPE",
    "PI_MATERIALIZER_ID",
    "PI_MATERIALIZER_VERSION",
    "PI_SESSION_ARGV_SHAPE",
    "PI_SINGLE_ARGV_SHAPE",
    "PiGenerationBundle",
    "PiGenerationDelegate",
    "PiReleaseMaterializationError",
    "PiReleaseMaterializer",
]
