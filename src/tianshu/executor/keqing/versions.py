"""Offline executable provenance for governed Keqing CLI launches.

The helpers in this module never spawn a subprocess.  Ordinary execution uses
the lightweight :func:`resolve_execution_executable`; Pi generation staging and
rehydration use the strict provenance resolvers below.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from tianshu.executor.keqing.pi_wire import PINNED_PI_VERSION

VersionSource = Literal["package_json", "pinned", "unverified"]

PI_PACKAGE_NAME = "@earendil-works/pi-coding-agent"

# Only Pi has a repository-verified runtime pin today.  Keep named constants for
# all built-in backends so provenance policy stays uniform without inventing a
# version claim for CLIs that Tianshu has not pinned.
PINNED_CLAUDE_CODE_VERSION: str | None = None
PINNED_CODEX_VERSION: str | None = None
PINNED_OPENCODE_VERSION: str | None = None

_PINNED_VERSIONS: dict[str, str | None] = {
    "claude-code": PINNED_CLAUDE_CODE_VERSION,
    "codex": PINNED_CODEX_VERSION,
    "opencode": PINNED_OPENCODE_VERSION,
    "pi": PINNED_PI_VERSION,
}


class ExecutableInspectionError(ValueError):
    """The requested executable cannot be resolved or verified offline."""


class _Hash(Protocol):
    def update(self, data: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class ExecutableProvenance:
    """Content identity and version evidence for one resolved CLI executable."""

    binary_path: str
    binary_digest: str
    package_path: str | None
    package_root: str | None
    package_name: str | None
    package_entrypoint: str | None
    package_digest: str | None
    version: str | None
    version_source: VersionSource


@dataclass(frozen=True, slots=True)
class ExecutionExecutable:
    """Minimal executable identity for ordinary governed process execution."""

    binary_path: str
    version: str | None
    version_source: VersionSource


@dataclass(frozen=True, slots=True)
class _PackageMetadata:
    manifest_path: Path
    manifest_digest: str
    root: Path
    name: str | None
    version: str | None
    entrypoint: str


def pinned_version(backend: str) -> str | None:
    """Return the repository-declared version pin, if one exists."""

    return _PINNED_VERSIONS.get(backend)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_field(digest: _Hash, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


def _canonical_package_tree_digest(root: Path) -> str:
    """Hash one controlled package tree without following its symlinks."""

    try:
        canonical_root = root.resolve(strict=True)
        if not canonical_root.is_dir():
            raise ExecutableInspectionError("package root is not a directory")
        digest = hashlib.sha256()
        _digest_field(digest, b"tianshu-pi-package-tree-v1")
        _digest_field(digest, b"root-directory")
        _digest_field(
            digest,
            str(stat.S_IMODE(canonical_root.stat(follow_symlinks=False).st_mode)).encode(),
        )

        def absorb(directory: Path) -> None:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: os.fsencode(item.name))
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(canonical_root).as_posix().encode()
                metadata = entry.stat(follow_symlinks=False)
                mode = stat.S_IMODE(metadata.st_mode)
                _digest_field(digest, relative)
                if entry.is_symlink():
                    target = os.readlink(path)
                    target_path = Path(target)
                    if target_path.is_absolute():
                        raise ExecutableInspectionError(
                            f"package symlink must be relative: {path!s}"
                        )
                    resolved_target = (path.parent / target_path).resolve(strict=True)
                    try:
                        resolved_target.relative_to(canonical_root)
                    except ValueError as exc:
                        raise ExecutableInspectionError(
                            f"package symlink escapes package root: {path!s}"
                        ) from exc
                    _digest_field(digest, b"symlink")
                    _digest_field(digest, os.fsencode(target))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    _digest_field(digest, b"directory")
                    _digest_field(digest, str(mode).encode())
                    absorb(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ExecutableInspectionError(f"unsupported package tree entry: {path!s}")
                _digest_field(digest, b"file")
                _digest_field(digest, str(mode).encode())
                _digest_field(digest, str(metadata.st_size).encode())
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = path.stat(follow_symlinks=False)
                if (after.st_size, after.st_mtime_ns) != (
                    metadata.st_size,
                    metadata.st_mtime_ns,
                ):
                    raise ExecutableInspectionError(f"package file changed while hashing: {path!s}")

        absorb(canonical_root)
        return digest.hexdigest()
    except ExecutableInspectionError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ExecutableInspectionError("package tree cannot be inspected") from exc


def _package_metadata(binary_path: Path) -> _PackageMetadata | None:
    """Find the nearest npm-style package.json without executing the CLI."""

    for parent in tuple(binary_path.parents)[:8]:
        package_path = parent / "package.json"
        if not package_path.is_file():
            continue
        try:
            package_bytes = package_path.read_bytes()
            payload = json.loads(package_bytes)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExecutableInspectionError("package manifest cannot be inspected") from exc
        if not isinstance(payload, dict):
            raise ExecutableInspectionError("package manifest must be an object")
        raw_name = payload.get("name")
        name = str(raw_name).strip() if raw_name is not None else None
        raw_version = payload.get("version") if isinstance(payload, dict) else None
        version = str(raw_version).strip() if raw_version is not None else None
        return _PackageMetadata(
            manifest_path=package_path.resolve(strict=True),
            manifest_digest=hashlib.sha256(package_bytes).hexdigest(),
            root=parent.resolve(strict=True),
            name=name or None,
            version=version or None,
            entrypoint=binary_path.relative_to(parent.resolve(strict=True)).as_posix(),
        )
    return None


def _inspect_path(path: Path, *, backend: str) -> ExecutableProvenance:
    # Keep the absolute launch path (and therefore the canonical adapter name)
    # while hashing and inspecting its strict real target.  npm installs expose
    # e.g. /opt/homebrew/bin/pi -> .../dist/cli.js; launching the real target
    # would fail the existing narrowly-scoped `pi` command grant.
    launch_path = Path(os.path.abspath(path))
    try:
        resolved = launch_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExecutableInspectionError(f"Keqing CLI not found: {str(path)!r}") from exc
    if not resolved.is_file() or not os.access(launch_path, os.X_OK):
        raise ExecutableInspectionError(f"Keqing CLI is not executable: {str(launch_path)!r}")

    try:
        binary_digest = _sha256_file(resolved)
        package = _package_metadata(resolved)
        package_digest = (
            (
                _canonical_package_tree_digest(package.root)
                if backend == "pi"
                else package.manifest_digest
            )
            if package is not None
            else None
        )
    except ExecutableInspectionError:
        raise
    except OSError as exc:
        raise ExecutableInspectionError("Keqing CLI provenance cannot be inspected") from exc
    package_version = package.version if package is not None else None
    fallback = pinned_version(backend)
    if package_version is not None:
        version = package_version
        source: VersionSource = "package_json"
    elif fallback is not None:
        version = fallback
        source = "pinned"
    else:
        version = None
        source = "unverified"
    return ExecutableProvenance(
        binary_path=str(launch_path),
        binary_digest=binary_digest,
        package_path=str(package.manifest_path) if package is not None else None,
        package_root=str(package.root) if package is not None else None,
        package_name=package.name if package is not None else None,
        package_entrypoint=package.entrypoint if package is not None else None,
        package_digest=package_digest,
        version=version,
        version_source=source,
    )


def resolve_execution_executable(
    binary: str,
    *,
    backend: str | None = None,
) -> ExecutionExecutable:
    """Resolve an executable while treating package/version metadata as advisory."""

    effective_backend = backend or _backend_for_binary(binary)
    if not effective_backend.strip():
        raise ExecutableInspectionError("Keqing backend must not be blank")
    raw = Path(binary)
    if raw.is_absolute():
        candidate = raw
    else:
        resolved_binary = shutil.which(binary)
        if resolved_binary is None:
            raise ExecutableInspectionError(f"Keqing CLI not found: {binary!r}")
        candidate = Path(resolved_binary)
    launch_path = Path(os.path.abspath(candidate))
    try:
        resolved = launch_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ExecutableInspectionError(f"Keqing CLI not found: {binary!r}") from exc
    if not resolved.is_file() or not os.access(launch_path, os.X_OK):
        raise ExecutableInspectionError(f"Keqing CLI is not executable: {str(launch_path)!r}")

    try:
        package = _package_metadata(resolved)
    except (ExecutableInspectionError, OSError, RuntimeError, ValueError):
        package = None
    if package is not None and package.version is not None:
        return ExecutionExecutable(
            binary_path=str(launch_path),
            version=package.version,
            version_source="package_json",
        )
    return ExecutionExecutable(
        binary_path=str(launch_path),
        version=None,
        version_source="unverified",
    )


def resolve_stage_executable(binary: str, *, backend: str) -> ExecutableProvenance:
    """Resolve a stage/preparation executable, allowing a bare PATH lookup.

    This is the only helper in the generation materialization path that may
    consult ``PATH``.  Absolute inputs are still inspected directly.
    """

    raw = Path(binary)
    if raw.is_absolute():
        return _inspect_path(raw, backend=backend)
    candidate = shutil.which(binary)
    if candidate is None:
        raise ExecutableInspectionError(f"Keqing CLI not found: {binary!r}")
    return _inspect_path(Path(candidate), backend=backend)


def inspect_persisted_executable(binary_path: str, *, backend: str) -> ExecutableProvenance:
    """Inspect an immutable persisted executable identity without PATH fallback."""

    path = Path(binary_path)
    if not path.is_absolute():
        raise ExecutableInspectionError("persisted Keqing binary path must be absolute")
    return _inspect_path(path, backend=backend)


def detect_installed_version(binary: str, *, backend: str | None = None) -> str | None:
    """Read only package metadata for status; never hash the package tree."""

    try:
        effective_backend = backend or _backend_for_binary(binary)
        raw = Path(binary)
        if raw.is_absolute():
            launch_path = raw
        else:
            resolved_binary = shutil.which(binary)
            if resolved_binary is None:
                return None
            launch_path = Path(resolved_binary)
        resolved = launch_path.resolve(strict=True)
        if not resolved.is_file():
            return None
        package = _package_metadata(resolved)
        if package is not None:
            if effective_backend == "pi" and package.name != PI_PACKAGE_NAME:
                return None
            if package.version is not None:
                return package.version
        return None
    except (ExecutableInspectionError, OSError, RuntimeError, ValueError):
        return None


def _backend_for_binary(binary: str) -> str:
    name = Path(binary).name
    return {
        "claude": "claude-code",
        "codex": "codex",
        "opencode": "opencode",
        "pi": "pi",
    }.get(name, name)
