"""Strict named Git operations used by Tianshu runtime services.

This module intentionally has one private low-level process launch site.  Runtime
services receive named Git operations instead of an arbitrary ``git argv`` escape
hatch, so validation and process hardening cannot be bypassed accidentally.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import BinaryIO, Literal

from tianshu.executor.anchored_fs import AnchoredFilesystemError, AnchoredRoot
from tianshu.security.clean_env import build_clean_env
from tianshu.security.redact import redact_text

_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_OUTPUT_LIMIT_BYTES = 1_000_000
_DEFAULT_BLOB_LIMIT_BYTES = 100_000_000
_DEFAULT_MATERIALIZATION_LIMIT_BYTES = 1_000_000_000
_DEFAULT_STAGE_PATH_LIMIT = 10_000
_DEFAULT_OBJECT_ENTRY_LIMIT = 200_000
_DEFAULT_OBJECT_FINGERPRINT_BYTE_LIMIT = 64_000_000
_MAX_PATH_BYTES = 4_096


@dataclass(frozen=True)
class GitLocation:
    """A work tree and, optionally, its independent Git directory."""

    work_tree: Path
    git_dir: Path | None = None

    def __post_init__(self) -> None:
        work_tree = Path(self.work_tree).expanduser().resolve()
        if "\x00" in str(work_tree):
            raise ValueError("work_tree contains NUL")
        object.__setattr__(self, "work_tree", work_tree)
        if self.git_dir is not None:
            git_dir = Path(self.git_dir).expanduser().resolve()
            if "\x00" in str(git_dir):
                raise ValueError("git_dir contains NUL")
            object.__setattr__(self, "git_dir", git_dir)


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str

    def __post_init__(self) -> None:
        if not self.name or any(char in self.name for char in "\r\n\x00"):
            raise ValueError("git identity name is invalid")
        if not self.email or any(char in self.email for char in "\r\n\x00"):
            raise ValueError("git identity email is invalid")


@dataclass(frozen=True)
class GitLogEntry:
    sha: str
    subject: str
    committed_at: str


@dataclass(frozen=True)
class GitRepositorySnapshot:
    """Stable source identity without exposing a raw Git command surface."""

    work_tree: Path
    git_dir: Path
    git_dir_identity: str
    common_git_dir: Path
    repository_id: str
    head_revision: str
    head_ref: str | None
    index_tree: str
    refs_hash: str
    object_database_hash: str
    status_hash: str
    clean: bool


type GitChangeKind = Literal["add", "modify", "delete", "rename", "copy", "mode", "untracked"]


@dataclass(frozen=True)
class GitStagedChange:
    kind: GitChangeKind
    old_path: str | None
    new_path: str | None
    old_oid: str | None
    new_oid: str | None
    old_mode: str | None
    new_mode: str | None
    old_size: int | None
    new_size: int | None
    binary: bool


@dataclass(frozen=True)
class GitWorktreeEntry:
    """Exact non-index worktree identity used by governed apply."""

    mode: Literal["100644", "100755", "120000"]
    oid: str
    size: int
    content: bytes = dataclass_field(repr=False)


class GitBackendError(RuntimeError):
    """A redacted, typed failure from one named Git operation."""

    def __init__(
        self,
        operation: str,
        detail: str,
        *,
        returncode: int | None = None,
        timed_out: bool = False,
        output_truncated: bool = False,
    ) -> None:
        self.operation = operation
        self.returncode = returncode
        self.timed_out = timed_out
        self.output_truncated = output_truncated
        super().__init__(f"git {operation} failed: {redact_text(detail)}")


@dataclass(frozen=True)
class _InvocationResult:
    stdout_bytes: bytes
    stdout: str
    stderr: str
    returncode: int
    output_truncated: bool
    output_limit_bytes: int


@dataclass(frozen=True)
class _IndexEntry:
    mode: str
    sha: str
    path: str


def _validate_ref(value: str, *, label: str = "revision") -> str:
    if (
        not _REF_RE.fullmatch(value)
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
    ):
        raise ValueError(f"invalid git {label}: {value!r}")
    return value


def _validate_sha(value: str) -> str:
    if not _SHA_RE.fullmatch(value):
        raise ValueError(f"invalid git SHA: {value!r}")
    return value


def _validate_pathspec(value: str | Path) -> str:
    raw = str(value)
    normalized = raw
    parts = normalized.split("/")
    raw_bytes = os.fsencode(raw)
    if (
        not raw
        or "\x00" in raw
        or b"\r" in raw_bytes
        or b"\n" in raw_bytes
        or len(raw_bytes) > _MAX_PATH_BYTES
        or "\\" in raw
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(part.casefold() == ".git" for part in parts)
    ):
        raise ValueError(f"invalid git pathspec: {raw!r}")
    return normalized


def _validate_message(message: str) -> str:
    if not message or "\x00" in message or len(message) > 4096:
        raise ValueError("invalid git commit message")
    return message


def _directory_identity(path: Path) -> str:
    resolved = Path(path).resolve()
    metadata = resolved.stat()
    payload = b"\x00".join(
        (
            os.fsencode(resolved),
            str(metadata.st_dev).encode("ascii"),
            str(metadata.st_ino).encode("ascii"),
        )
    )
    return hashlib.sha256(payload).hexdigest()


def trusted_git_executable() -> str | None:
    """Return the absolute Git executable accepted by the runtime backend."""
    executable = shutil.which("git", path=os.defpath)
    if executable is None or not Path(executable).is_absolute():
        return None
    return str(Path(executable).resolve())


class GitBackend:
    """Named, bounded Git operations with no public raw-command method."""

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = _DEFAULT_OUTPUT_LIMIT_BYTES,
        blob_limit_bytes: int = _DEFAULT_BLOB_LIMIT_BYTES,
        materialization_limit_bytes: int = _DEFAULT_MATERIALIZATION_LIMIT_BYTES,
        stage_path_limit: int = _DEFAULT_STAGE_PATH_LIMIT,
        filesystem_operation_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if output_limit_bytes <= 0:
            raise ValueError("output_limit_bytes must be positive")
        if blob_limit_bytes <= 0:
            raise ValueError("blob_limit_bytes must be positive")
        if materialization_limit_bytes < blob_limit_bytes:
            raise ValueError("materialization_limit_bytes must cover at least one blob")
        if not 0 < stage_path_limit <= _DEFAULT_STAGE_PATH_LIMIT:
            raise ValueError(f"stage_path_limit must be between 1 and {_DEFAULT_STAGE_PATH_LIMIT}")
        self._timeout_seconds = timeout_seconds
        self._output_limit_bytes = output_limit_bytes
        self._blob_limit_bytes = blob_limit_bytes
        self._materialization_limit_bytes = materialization_limit_bytes
        self._stage_path_limit = stage_path_limit
        self._stage_metadata_limit_bytes = stage_path_limit * (_MAX_PATH_BYTES + 80)
        self._git_executable = trusted_git_executable()
        self._filesystem_operation_hook = filesystem_operation_hook

    def resolve_revision(
        self,
        location: GitLocation,
        revision: str = "HEAD",
        *,
        short: bool = False,
    ) -> str:
        revision = _validate_ref(revision)
        args = ["rev-parse", "--verify"]
        if short:
            args.append("--short")
        args.append(revision)
        result = self._invoke("resolve_revision", location, tuple(args))
        self._require_complete_output("resolve_revision", result)
        return result.stdout.strip()

    def resolve_commit(self, location: GitLocation, revision: str) -> str:
        revision = _validate_ref(revision)
        result = self._invoke(
            "resolve_commit",
            location,
            ("rev-parse", "--verify", f"{revision}^{{commit}}"),
        )
        self._require_complete_output("resolve_commit", result)
        value = result.stdout.strip()
        return _validate_sha(value)

    def resolve_parent_commit(self, location: GitLocation, revision: str = "HEAD") -> str:
        """Resolve the first parent of one validated revision."""

        revision = _validate_ref(revision)
        result = self._invoke(
            "resolve_parent_commit",
            location,
            ("rev-parse", "--verify", f"{revision}^{{commit}}^"),
        )
        self._require_complete_output("resolve_parent_commit", result)
        return _validate_sha(result.stdout.strip())

    def worktree_status_paths(self, location: GitLocation) -> tuple[str, ...]:
        """Return the bounded set of tracked or untracked dirty paths."""

        with self._isolated_git_state(location) as isolated_env:
            result = self._invoke(
                "worktree_status_paths",
                location,
                ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                env_overrides=isolated_env,
                max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
            )
        self._require_complete_output("worktree_status_paths", result)
        tokens = result.stdout_bytes.split(b"\0")
        paths: set[str] = set()
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if not token:
                index += 1
                continue
            if len(token) < 4 or token[2:3] != b" ":
                raise GitBackendError("worktree_status_paths", "malformed status record")
            status = token[:2]
            paths.add(os.fsdecode(token[3:]))
            if b"R" in status or b"C" in status:
                index += 1
                if index >= len(tokens) or not tokens[index]:
                    raise GitBackendError("worktree_status_paths", "malformed rename record")
                paths.add(os.fsdecode(tokens[index]))
            index += 1
        return tuple(sorted(paths))

    def changed_paths_between(
        self,
        location: GitLocation,
        base_commit: str,
        target_commit: str,
    ) -> tuple[str, ...]:
        """Return both sides of every path changed between exact commits."""

        base_commit = _validate_sha(base_commit)
        target_commit = _validate_sha(target_commit)
        result = self._invoke(
            "changed_paths_between",
            location,
            (
                "diff",
                "--name-status",
                "-z",
                "--no-ext-diff",
                "--no-textconv",
                base_commit,
                target_commit,
                "--",
            ),
            max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
        )
        self._require_complete_output("changed_paths_between", result)
        tokens = result.stdout_bytes.split(b"\0")
        paths: set[str] = set()
        index = 0
        while index < len(tokens):
            status = tokens[index]
            if not status:
                index += 1
                continue
            index += 1
            path_count = 2 if status.startswith((b"R", b"C")) else 1
            if index + path_count > len(tokens):
                raise GitBackendError("changed_paths_between", "malformed diff record")
            for _ in range(path_count):
                if not tokens[index]:
                    raise GitBackendError("changed_paths_between", "malformed diff path")
                paths.add(os.fsdecode(tokens[index]))
                index += 1
        return tuple(sorted(paths))

    def inspect_repository(self, location: GitLocation) -> GitRepositorySnapshot:
        top = self._invoke("inspect_repository.root", location, ("rev-parse", "--show-toplevel"))
        self._require_complete_output("inspect_repository.root", top)
        work_tree = self._validated_git_path(location, top.stdout, require_directory=True)
        common = self._invoke(
            "inspect_repository.common_dir",
            location,
            ("rev-parse", "--path-format=absolute", "--git-common-dir"),
        )
        self._require_complete_output("inspect_repository.common_dir", common)
        common_git_dir = self._validated_git_path(location, common.stdout, require_directory=True)
        git_dir = self._resolve_git_dir(location)
        head_revision = self.resolve_commit(location, "HEAD")
        try:
            ref_result = self._invoke(
                "inspect_repository.head_ref", location, ("symbolic-ref", "-q", "HEAD")
            )
            self._require_complete_output("inspect_repository.head_ref", ref_result)
            head_ref = ref_result.stdout.strip() or None
        except GitBackendError as exc:
            if exc.returncode != 1:
                raise
            head_ref = None
        refs = self._invoke(
            "inspect_repository.refs",
            location,
            (
                "for-each-ref",
                "--sort=refname",
                "--format=%(refname)%00%(objectname)%00%(symref)",
            ),
            max_output_bytes=min(self._materialization_limit_bytes, 40_000_000),
        )
        self._require_complete_output("inspect_repository.refs", refs)
        object_database_hash = self._object_database_hash(
            self._resolve_git_path(location, "objects")
        )
        with self._isolated_git_state(location) as isolated_env:
            tree = self._invoke(
                "inspect_repository.index",
                location,
                ("write-tree",),
                env_overrides=isolated_env,
            )
            self._require_complete_output("inspect_repository.index", tree)
            index_tree = _validate_sha(tree.stdout.strip())
            status = self._invoke(
                "inspect_repository.status",
                location,
                ("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                env_overrides=isolated_env,
                max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
            )
            self._require_complete_output("inspect_repository.status", status)
        repository_id = _directory_identity(common_git_dir)
        return GitRepositorySnapshot(
            work_tree=work_tree,
            git_dir=git_dir,
            git_dir_identity=_directory_identity(git_dir),
            common_git_dir=common_git_dir,
            repository_id=repository_id,
            head_revision=head_revision,
            head_ref=head_ref,
            index_tree=index_tree,
            refs_hash=hashlib.sha256(refs.stdout_bytes).hexdigest(),
            object_database_hash=object_database_hash,
            status_hash=hashlib.sha256(status.stdout_bytes).hexdigest(),
            clean=not status.stdout_bytes,
        )

    @staticmethod
    def _object_database_hash(objects: Path) -> str:
        """Fingerprint logical Git object storage within explicit safety bounds.

        Git may freshen existing loose-object timestamps while hashing worktree
        content against an alternate object directory.  Those timestamps are
        deliberately excluded. File payloads are hashed within a total byte
        limit, and alternate object databases fail closed because they cannot be
        bound safely without recursively expanding the authority boundary.
        """

        digest = hashlib.sha256()
        pending = [(Path(objects), b"")]
        entry_count = 0
        content_bytes = 0
        try:
            while pending:
                directory, relative = pending.pop()
                entry_count += 1
                if entry_count > _DEFAULT_OBJECT_ENTRY_LIMIT:
                    raise GitBackendError(
                        "inspect_repository.objects",
                        "Git object database exceeds the inspection entry limit",
                    )
                metadata = directory.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise GitBackendError(
                        "inspect_repository.objects",
                        "Git object database contains an unsafe directory",
                    )
                digest.update(
                    b"D\x00"
                    + relative
                    + b"\x00"
                    + str(stat.S_IMODE(metadata.st_mode)).encode("ascii")
                    + b"\x00"
                    + str(metadata.st_dev).encode("ascii")
                    + b"\x00"
                    + str(metadata.st_ino).encode("ascii")
                    + b"\x00"
                )
                entries = sorted(
                    os.scandir(directory),
                    key=lambda entry: os.fsencode(entry.name),
                    reverse=True,
                )
                for entry in entries:
                    entry_count += 1
                    if entry_count > _DEFAULT_OBJECT_ENTRY_LIMIT:
                        raise GitBackendError(
                            "inspect_repository.objects",
                            "Git object database exceeds the inspection entry limit",
                        )
                    name = os.fsencode(entry.name)
                    child_relative = name if not relative else relative + b"/" + name
                    child_metadata = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(child_metadata.st_mode):
                        raise GitBackendError(
                            "inspect_repository.objects",
                            "Git object database contains an unsafe link",
                        )
                    if stat.S_ISDIR(child_metadata.st_mode):
                        pending.append((Path(entry.path), child_relative))
                        continue
                    if not stat.S_ISREG(child_metadata.st_mode):
                        raise GitBackendError(
                            "inspect_repository.objects",
                            "Git object database contains an unsupported entry",
                        )
                    digest.update(
                        b"F\x00"
                        + child_relative
                        + b"\x00"
                        + str(stat.S_IMODE(child_metadata.st_mode)).encode("ascii")
                        + b"\x00"
                        + str(child_metadata.st_size).encode("ascii")
                        + b"\x00"
                        + str(child_metadata.st_dev).encode("ascii")
                        + b"\x00"
                        + str(child_metadata.st_ino).encode("ascii")
                        + b"\x00"
                    )
                    if child_relative == b"info/alternates" and child_metadata.st_size:
                        raise GitBackendError(
                            "inspect_repository.objects",
                            "alternate Git object databases are unsupported",
                        )
                    content_bytes += child_metadata.st_size
                    if content_bytes > _DEFAULT_OBJECT_FINGERPRINT_BYTE_LIMIT:
                        raise GitBackendError(
                            "inspect_repository.objects",
                            "Git object fingerprint exceeds the content byte limit",
                        )
                    digest.update(
                        b"C\x00"
                        + child_relative
                        + b"\x00"
                        + GitBackend._bounded_file_hash(Path(entry.path), child_metadata).encode(
                            "ascii"
                        )
                        + b"\x00"
                    )
        except GitBackendError:
            raise
        except OSError as exc:
            raise GitBackendError(
                "inspect_repository.objects",
                "Git object database changed during inspection",
            ) from exc
        return digest.hexdigest()

    @staticmethod
    def _bounded_file_hash(path: Path, expected: os.stat_result) -> str:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_dev != expected.st_dev
                or before.st_ino != expected.st_ino
                or before.st_size != expected.st_size
            ):
                raise OSError("object fingerprint identity changed")
            digest = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65_536))
                if not chunk:
                    raise OSError("object fingerprint content was truncated")
                digest.update(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            if (
                after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or after.st_ctime_ns != before.st_ctime_ns
            ):
                raise OSError("object fingerprint content changed")
            return digest.hexdigest()
        except OSError as exc:
            raise GitBackendError(
                "inspect_repository.objects",
                "Git object database changed during inspection",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def create_detached_worktree(
        self,
        location: GitLocation,
        destination: Path,
        *,
        start_ref: str,
    ) -> None:
        start_ref = _validate_sha(start_ref)
        raw_target = Path(destination).expanduser()
        if raw_target.is_symlink():
            raise ValueError("detached worktree destination must not be a symlink")
        target = raw_target.resolve(strict=False)
        if target.exists() or target.is_symlink():
            raise ValueError("detached worktree destination must not exist")
        if not target.parent.is_dir() or target.parent.is_symlink():
            raise ValueError("detached worktree parent must be an existing real directory")
        self._invoke(
            "create_detached_worktree",
            location,
            ("worktree", "add", "--detach", "--no-checkout", str(target), start_ref),
        )
        try:
            target_location = GitLocation(target)
            self._invoke(
                "create_detached_worktree.read_tree",
                target_location,
                ("read-tree", "HEAD"),
            )
            self._materialize_index(target_location)
        except Exception:
            with suppress(GitBackendError):
                self.remove_worktree(location, target, force=True)
            raise

    def create_branch_worktree(
        self,
        location: GitLocation,
        destination: Path,
        *,
        branch: str,
        start_ref: str,
    ) -> None:
        branch = _validate_ref(branch, label="branch")
        start_ref = _validate_ref(start_ref)
        target = Path(destination).expanduser().resolve()
        self._invoke(
            "create_branch_worktree",
            location,
            ("worktree", "add", "--no-checkout", "-b", branch, str(target), start_ref),
        )
        try:
            target_location = GitLocation(target)
            self._invoke("create_branch_worktree.read_tree", target_location, ("read-tree", "HEAD"))
            self._materialize_index(target_location)
        except Exception:
            with suppress(GitBackendError):
                self.remove_worktree(location, target, force=True)
            with suppress(GitBackendError):
                self.delete_branch(location, branch, force=True)
            raise

    def restore_branch_worktree(
        self,
        location: GitLocation,
        destination: Path,
        *,
        branch: str,
    ) -> None:
        branch = _validate_ref(branch, label="branch")
        target = Path(destination).expanduser().resolve()
        self._invoke(
            "restore_branch_worktree",
            location,
            ("worktree", "add", "--no-checkout", str(target), branch),
        )
        try:
            target_location = GitLocation(target)
            self._invoke(
                "restore_branch_worktree.read_tree", target_location, ("read-tree", "HEAD")
            )
            self._materialize_index(target_location)
        except Exception:
            with suppress(GitBackendError):
                self.remove_worktree(location, target, force=True)
            raise

    def remove_worktree(
        self,
        location: GitLocation,
        destination: Path,
        *,
        force: bool = False,
        expected_git_dir: Path | None = None,
        expected_git_dir_identity: str | None = None,
    ) -> None:
        target = Path(destination).expanduser().absolute()
        if "\x00" in str(target):
            raise ValueError("worktree destination contains NUL")
        if (expected_git_dir is None) != (expected_git_dir_identity is None):
            raise ValueError("worktree Git authority must be provided together")
        if target.is_symlink():
            raise GitBackendError("remove_worktree", "worktree destination is a symlink")
        if target.exists() and not target.is_dir():
            raise GitBackendError("remove_worktree", "worktree destination is not a directory")
        if expected_git_dir is not None and expected_git_dir_identity is not None:
            authority = Path(expected_git_dir).expanduser().resolve()
            if target.exists():
                snapshot = self.inspect_repository(GitLocation(target))
                if (
                    snapshot.work_tree != target
                    or snapshot.git_dir != authority
                    or snapshot.git_dir_identity != expected_git_dir_identity
                ):
                    raise GitBackendError("remove_worktree", "worktree authority changed")
            elif authority.exists():
                self._validate_worktree_admin(
                    target,
                    authority,
                    expected_git_dir_identity,
                )
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(target))
        try:
            self._invoke("remove_worktree", location, tuple(args))
        except GitBackendError:
            if target.exists() or target.is_symlink():
                raise
            self._invoke(
                "remove_worktree.prune",
                location,
                ("worktree", "prune", "--expire", "now"),
            )

    @staticmethod
    def _validate_worktree_admin(
        work_tree: Path,
        git_dir: Path,
        expected_identity: str,
    ) -> None:
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise GitBackendError("remove_worktree", "worktree Git authority is unavailable")
        if _directory_identity(git_dir) != expected_identity:
            raise GitBackendError("remove_worktree", "worktree Git authority changed")
        pointer = git_dir / "gitdir"
        if pointer.is_symlink() or not pointer.is_file():
            raise GitBackendError("remove_worktree", "worktree Git pointer is unavailable")
        try:
            registered = Path(pointer.read_text(encoding="utf-8").strip()).absolute()
        except (OSError, UnicodeError) as exc:
            raise GitBackendError("remove_worktree", str(exc)) from exc
        if registered != work_tree / ".git":
            raise GitBackendError("remove_worktree", "worktree registration changed")

    def delete_branch(
        self,
        location: GitLocation,
        branch: str,
        *,
        force: bool = False,
    ) -> None:
        branch = _validate_ref(branch, label="branch")
        self._invoke("delete_branch", location, ("branch", "-D" if force else "-d", branch))

    def diff(self, location: GitLocation, base_ref: str) -> str:
        base_ref = _validate_ref(base_ref)
        with tempfile.TemporaryDirectory(prefix="tianshu-git-index-") as temp_dir:
            temp_root = Path(temp_dir)
            index_path = temp_root / "index"
            object_directory = temp_root / "objects"
            object_directory.mkdir()
            control_worktree = temp_root / "worktree"
            control_worktree.mkdir()
            source_objects = self._resolve_git_path(location, "objects")
            index_env = {
                "GIT_INDEX_FILE": str(index_path),
                "GIT_OBJECT_DIRECTORY": str(object_directory),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source_objects),
            }
            index_location = GitLocation(
                control_worktree,
                git_dir=self._resolve_git_dir(location),
            )
            self._invoke(
                "diff.read_tree",
                index_location,
                ("read-tree", base_ref),
                env_overrides=index_env,
            )
            self._stage_all_raw(
                location,
                env_overrides=index_env,
                index_location=index_location,
            )
            result = self._invoke(
                "diff",
                index_location,
                ("diff", "--cached", "--no-ext-diff", "--no-textconv", base_ref, "--"),
                env_overrides=index_env,
            )
            self._require_complete_output("diff", result)
            return result.stdout

    def stage_paths(self, location: GitLocation, pathspecs: tuple[str | Path, ...]) -> None:
        if not pathspecs:
            raise ValueError("at least one git pathspec is required")
        paths = tuple(_validate_pathspec(path) for path in pathspecs)
        with tempfile.TemporaryDirectory(prefix="tianshu-git-index-control-") as temp_dir:
            control_worktree = Path(temp_dir)
            index_location = GitLocation(
                control_worktree,
                git_dir=self._resolve_git_dir(location),
            )
            for path in paths:
                target = self._safe_worktree_path(location, path)
                if target.is_dir() and not target.is_symlink():
                    raise ValueError("GitBackend.stage_paths accepts exact files, not directories")
            self._stage_paths_raw(
                location,
                paths,
                index_location=index_location,
            )

    def stage_all(self, location: GitLocation) -> None:
        with tempfile.TemporaryDirectory(prefix="tianshu-git-index-control-") as temp_dir:
            index_location = GitLocation(
                Path(temp_dir),
                git_dir=self._resolve_git_dir(location),
            )
            self._stage_all_raw(location, index_location=index_location)

    def capture_staged_changes(
        self, location: GitLocation, base_revision: str
    ) -> tuple[GitStagedChange, ...]:
        """Stage the lease and return an exact-OID canonical change inventory.

        Rename/copy detection deliberately uses a 100% similarity threshold.  A
        moved-and-edited file is represented as delete/add instead of a fuzzy
        rename, keeping the classification stable across Git versions.
        """

        base_revision = _validate_sha(base_revision)
        untracked = set(self._untracked_paths(location))
        with self._isolated_git_state(location) as isolated_env:
            self._stage_all_raw(location, env_overrides=isolated_env)
            raw = self._invoke(
                "capture_changes.raw",
                location,
                (
                    "diff",
                    "--cached",
                    "--raw",
                    "-z",
                    "--no-abbrev",
                    "--find-renames=100%",
                    "--find-copies=100%",
                    "--find-copies-harder",
                    base_revision,
                    "--",
                ),
                env_overrides=isolated_env,
                max_output_bytes=min(self._materialization_limit_bytes, 40_000_000),
            )
            self._require_complete_output("capture_changes.raw", raw)
            parsed = self._parse_raw_changes(raw.stdout_bytes, untracked)
            binary_paths = self._binary_paths(
                location,
                base_revision,
                env_overrides=isolated_env,
            )
            object_sizes = self._object_sizes(
                location,
                {
                    oid
                    for change in parsed
                    for oid in (change.old_oid, change.new_oid)
                    if oid is not None
                },
                env_overrides=isolated_env,
            )
        return tuple(
            GitStagedChange(
                kind=change.kind,
                old_path=change.old_path,
                new_path=change.new_path,
                old_oid=change.old_oid,
                new_oid=change.new_oid,
                old_mode=change.old_mode,
                new_mode=change.new_mode,
                old_size=object_sizes.get(change.old_oid) if change.old_oid else None,
                new_size=object_sizes.get(change.new_oid) if change.new_oid else None,
                binary=bool({change.old_path, change.new_path}.difference({None}) & binary_paths),
            )
            for change in parsed
        )

    @staticmethod
    def _parse_raw_changes(payload: bytes, untracked: set[str]) -> tuple[GitStagedChange, ...]:
        tokens = payload.split(b"\x00")
        if tokens and not tokens[-1]:
            tokens.pop()
        parsed: list[GitStagedChange] = []
        index = 0
        while index < len(tokens):
            header = tokens[index]
            index += 1
            if not header.startswith(b":"):
                raise GitBackendError("capture_changes.raw", "Git returned malformed raw diff")
            try:
                old_mode_raw, new_mode_raw, old_oid_raw, new_oid_raw, status_raw = header[1:].split(
                    b" "
                )
                old_mode = old_mode_raw.decode("ascii")
                new_mode = new_mode_raw.decode("ascii")
                old_oid = old_oid_raw.decode("ascii").lower()
                new_oid = new_oid_raw.decode("ascii").lower()
                status = status_raw.decode("ascii")
            except (UnicodeDecodeError, ValueError) as exc:
                raise GitBackendError(
                    "capture_changes.raw", "Git returned malformed raw metadata"
                ) from exc
            path_count = 2 if status.startswith(("R", "C")) else 1
            if index + path_count > len(tokens):
                raise GitBackendError("capture_changes.raw", "Git omitted a raw diff path")
            paths = tuple(
                _validate_pathspec(os.fsdecode(item)) for item in tokens[index : index + path_count]
            )
            index += path_count
            old_path = paths[0]
            new_path = paths[1] if path_count == 2 else paths[0]
            old_value = None if set(old_oid) == {"0"} else _validate_sha(old_oid)
            new_value = None if set(new_oid) == {"0"} else _validate_sha(new_oid)
            old_mode_value = None if old_mode == "000000" else old_mode
            new_mode_value = None if new_mode == "000000" else new_mode
            status_code = status[:1]
            if status_code == "A":
                kind: GitChangeKind = "untracked" if new_path in untracked else "add"
                old_path = None
            elif status_code == "D":
                kind = "delete"
                new_path = None
            elif status_code == "R":
                kind = "rename"
            elif status_code == "C":
                kind = "copy"
            elif status_code in {"M", "T"}:
                kind = (
                    "mode"
                    if old_value == new_value and old_mode_value != new_mode_value
                    else "modify"
                )
            else:
                raise GitBackendError(
                    "capture_changes.raw", f"unsupported Git change status {status!r}"
                )
            parsed.append(
                GitStagedChange(
                    kind=kind,
                    old_path=old_path,
                    new_path=new_path,
                    old_oid=old_value,
                    new_oid=new_value,
                    old_mode=old_mode_value,
                    new_mode=new_mode_value,
                    old_size=None,
                    new_size=None,
                    binary=False,
                )
            )
        return tuple(parsed)

    def _binary_paths(
        self,
        location: GitLocation,
        base_revision: str,
        *,
        env_overrides: Mapping[str, str] | None = None,
    ) -> set[str]:
        result = self._invoke(
            "capture_changes.binary",
            location,
            (
                "diff",
                "--cached",
                "--numstat",
                "-z",
                "--no-renames",
                base_revision,
                "--",
            ),
            env_overrides=env_overrides,
            max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
        )
        self._require_complete_output("capture_changes.binary", result)
        binary: set[str] = set()
        for record in result.stdout_bytes.split(b"\x00"):
            if not record:
                continue
            try:
                added, deleted, raw_path = record.split(b"\t", 2)
                path = _validate_pathspec(os.fsdecode(raw_path))
            except ValueError as exc:
                raise GitBackendError(
                    "capture_changes.binary", "Git returned malformed numstat data"
                ) from exc
            if added == deleted == b"-":
                binary.add(path)
        return binary

    def _object_sizes(
        self,
        location: GitLocation,
        object_ids: set[str],
        *,
        env_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, int]:
        if not object_ids:
            return {}
        ordered = sorted(object_ids)
        stdin_bytes = b"".join(oid.encode("ascii") + b"\n" for oid in ordered)
        result = self._invoke(
            "capture_changes.object_sizes",
            location,
            ("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
            stdin_bytes=stdin_bytes,
            env_overrides=env_overrides,
            stdin_limit_bytes=self._stage_metadata_limit_bytes,
            max_output_bytes=max(self._output_limit_bytes, len(ordered) * 128 + 1),
        )
        self._require_complete_output("capture_changes.object_sizes", result)
        lines = result.stdout.splitlines()
        if len(lines) != len(ordered):
            raise GitBackendError("capture_changes.object_sizes", "Git omitted object metadata")
        sizes: dict[str, int] = {}
        for expected, line in zip(ordered, lines, strict=True):
            try:
                actual, object_type, raw_size = line.split()
                size = int(raw_size)
            except (TypeError, ValueError) as exc:
                raise GitBackendError(
                    "capture_changes.object_sizes", "Git returned malformed object metadata"
                ) from exc
            if (
                actual != expected
                or object_type != "blob"
                or not 0 <= size <= self._blob_limit_bytes
            ):
                raise GitBackendError(
                    "capture_changes.object_sizes", "Git returned invalid blob metadata"
                )
            sizes[actual] = size
        return sizes

    def commit(
        self,
        location: GitLocation,
        message: str,
        *,
        identity: GitIdentity,
        allow_empty: bool = False,
    ) -> str:
        message = _validate_message(message)
        with tempfile.TemporaryDirectory(prefix="tianshu-git-commit-control-") as temp_dir:
            control_location = GitLocation(
                Path(temp_dir),
                git_dir=self._resolve_git_dir(location),
            )
            tree_result = self._invoke("commit.write_tree", control_location, ("write-tree",))
            tree_sha = tree_result.stdout.strip()
            _validate_sha(tree_sha)
            parent_sha: str | None = None
            try:
                parent_result = self._invoke(
                    "commit.resolve_parent",
                    control_location,
                    ("rev-parse", "--verify", "--quiet", "HEAD"),
                )
                parent_sha = parent_result.stdout.strip()
                _validate_sha(parent_sha)
            except GitBackendError as exc:
                if exc.returncode not in {1, 128}:
                    raise
            if parent_sha is not None and not allow_empty:
                parent_tree = self._invoke(
                    "commit.resolve_parent_tree",
                    control_location,
                    ("rev-parse", "--verify", f"{parent_sha}^{{tree}}"),
                ).stdout.strip()
                if parent_tree == tree_sha:
                    raise GitBackendError("commit", "nothing to commit")
            commit_args = ["commit-tree", tree_sha]
            if parent_sha is not None:
                commit_args.extend(("-p", parent_sha))
            commit_args.extend(("-m", message))
            commit_result = self._invoke(
                "commit.create",
                control_location,
                tuple(commit_args),
                identity=identity,
            )
            commit_sha = commit_result.stdout.strip()
            _validate_sha(commit_sha)
            expected_old = parent_sha or self._zero_oid(control_location)
            update_args = ["update-ref", "-m", message, "HEAD", commit_sha, expected_old]
            self._invoke("commit.update_ref", control_location, tuple(update_args))
            return commit_sha

    def init_repository(self, location: GitLocation) -> None:
        if not location.work_tree.is_dir():
            raise ValueError(f"git work tree is not a directory: {location.work_tree}")
        init_location = location
        if location.git_dir is None:
            init_location = GitLocation(location.work_tree, git_dir=location.work_tree / ".git")
        assert init_location.git_dir is not None
        init_location.git_dir.parent.mkdir(parents=True, exist_ok=True)
        self._invoke("init_repository", init_location, ("init", "-q"))

    def commit_timestamp(self, location: GitLocation, sha: str) -> str:
        sha = _validate_sha(sha)
        result = self._invoke(
            "commit_timestamp",
            location,
            ("show", "-s", "--format=%cI", sha),
        )
        self._require_complete_output("commit_timestamp", result)
        return result.stdout.strip()

    def list_log(self, location: GitLocation) -> tuple[GitLogEntry, ...]:
        result = self._invoke(
            "list_log",
            location,
            ("log", "--format=%H%x1f%s%x1f%cI"),
        )
        self._require_complete_output("list_log", result)
        entries: list[GitLogEntry] = []
        for line in result.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 3 or not _SHA_RE.fullmatch(parts[0]):
                raise GitBackendError("list_log", "git returned malformed log output")
            entries.append(GitLogEntry(parts[0], parts[1], parts[2]))
        return tuple(entries)

    def restore_snapshot(
        self,
        location: GitLocation,
        sha: str,
        *,
        identity: GitIdentity,
    ) -> str:
        sha = _validate_sha(sha)
        previous_paths = set(self._index_entries(location))
        self._invoke("restore_snapshot.read_tree", location, ("read-tree", sha))
        current_paths = set(self._index_entries(location))
        self._remove_worktree_paths(location, previous_paths - current_paths)
        self._materialize_index(location)
        self._invoke("restore_snapshot.clean", location, ("clean", "-fd"))
        return self.commit(
            location,
            f"revert to {sha[:10]}",
            identity=identity,
            allow_empty=True,
        )

    def read_worktree_entry(
        self,
        location: GitLocation,
        relative_path: str,
    ) -> GitWorktreeEntry:
        """Read and hash one safe worktree path without writing Git objects."""

        try:
            with AnchoredRoot(
                location.work_tree,
                byte_limit=self._blob_limit_bytes,
                operation_hook=self._filesystem_operation_hook,
            ) as root:
                entry = root.capture(relative_path)
            if entry.kind == "symlink":
                mode: Literal["100644", "100755", "120000"] = "120000"
                assert entry.content is not None
                content = entry.content
            elif entry.kind == "file":
                assert entry.mode is not None and entry.content is not None
                mode = "100755" if entry.mode & stat.S_IXUSR else "100644"
                content = entry.content
            else:
                raise GitBackendError("read_worktree_entry", "unsupported worktree entry type")
        except AnchoredFilesystemError as exc:
            raise GitBackendError("read_worktree_entry", "worktree entry identity changed") from exc
        if len(content) > self._blob_limit_bytes:
            raise GitBackendError(
                "read_worktree_entry",
                f"blob exceeds {self._blob_limit_bytes} byte limit: {relative_path}",
            )
        result = self._invoke(
            "read_worktree_entry.hash",
            location,
            ("hash-object", "--no-filters", "--stdin"),
            stdin_bytes=content,
        )
        self._require_complete_output("read_worktree_entry.hash", result)
        oid = _validate_sha(result.stdout.strip())
        return GitWorktreeEntry(mode=mode, oid=oid, size=len(content), content=content)

    def _safe_worktree_path(self, location: GitLocation, relative_path: str) -> Path:
        normalized = _validate_pathspec(relative_path)
        root = location.work_tree.resolve()
        parts = Path(normalized).parts
        cursor = root
        for part in parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                raise GitBackendError(
                    "path_boundary", f"symlink parent escapes safety: {normalized}"
                )
            if cursor.exists() and not cursor.is_dir():
                raise GitBackendError("path_boundary", f"non-directory parent: {normalized}")
        return root.joinpath(*parts)

    def _resolve_git_dir(self, location: GitLocation) -> Path:
        result = self._invoke(
            "resolve_git_dir",
            location,
            ("rev-parse", "--absolute-git-dir"),
        )
        self._require_complete_output("resolve_git_dir", result)
        return self._validated_git_path(location, result.stdout, require_directory=True)

    def _resolve_git_index(self, location: GitLocation) -> Path:
        result = self._invoke(
            "resolve_git_index",
            location,
            ("rev-parse", "--git-path", "index"),
        )
        self._require_complete_output("resolve_git_index", result)
        raw_path = Path(result.stdout.strip())
        index_path = (
            raw_path.absolute()
            if raw_path.is_absolute()
            else (location.work_tree / raw_path).absolute()
        )
        if index_path.is_symlink() or not index_path.is_file():
            raise GitBackendError("resolve_git_index", "Git index is not a regular file")
        return index_path

    @contextmanager
    def _isolated_git_state(self, location: GitLocation) -> Iterator[dict[str, str]]:
        source_index = self._resolve_git_index(location)
        source_objects = self._resolve_git_path(location, "objects")
        with tempfile.TemporaryDirectory(prefix="tianshu-git-state-") as temp_dir:
            temp_root = Path(temp_dir)
            index_path = temp_root / "index"
            object_directory = temp_root / "objects"
            object_directory.mkdir()
            shutil.copyfile(source_index, index_path)
            yield {
                "GIT_INDEX_FILE": str(index_path),
                "GIT_OBJECT_DIRECTORY": str(object_directory),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source_objects),
            }

    def _resolve_git_path(self, location: GitLocation, name: str) -> Path:
        if name != "objects":
            raise ValueError("only the Git objects directory may be resolved")
        result = self._invoke(
            "resolve_git_path",
            location,
            ("rev-parse", "--git-path", name),
        )
        self._require_complete_output("resolve_git_path", result)
        return self._validated_git_path(location, result.stdout, require_directory=True)

    def _zero_oid(self, location: GitLocation) -> str:
        result = self._invoke(
            "resolve_object_format",
            location,
            ("rev-parse", "--show-object-format"),
        )
        self._require_complete_output("resolve_object_format", result)
        object_format = result.stdout.strip()
        if object_format == "sha1":
            return "0" * 40
        if object_format == "sha256":
            return "0" * 64
        raise GitBackendError(
            "resolve_object_format",
            f"unsupported Git object format: {object_format}",
        )

    @staticmethod
    def _validated_git_path(
        location: GitLocation,
        raw_value: str,
        *,
        require_directory: bool,
    ) -> Path:
        if not raw_value or "\x00" in raw_value or "\n" in raw_value.strip("\n"):
            raise GitBackendError("resolve_git_path", "git returned an invalid path")
        raw_path = Path(raw_value.strip())
        resolved = (
            raw_path.resolve()
            if raw_path.is_absolute()
            else (location.work_tree / raw_path).resolve()
        )
        if require_directory and not resolved.is_dir():
            raise GitBackendError("resolve_git_path", f"Git path is not a directory: {resolved}")
        return resolved

    def _index_entries(
        self,
        location: GitLocation,
        *,
        env_overrides: Mapping[str, str] | None = None,
    ) -> dict[str, _IndexEntry]:
        result = self._invoke(
            "read_index",
            location,
            ("ls-files", "-s", "-z", "--"),
            env_overrides=env_overrides,
            max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
        )
        self._require_complete_output("read_index", result)
        entries: dict[str, _IndexEntry] = {}
        for record in result.stdout_bytes.split(b"\x00"):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b"\t", 1)
                mode, raw_sha, stage_number = metadata.split(b" ", 2)
                path = os.fsdecode(raw_path)
                entry = _IndexEntry(
                    mode=mode.decode("ascii"),
                    sha=raw_sha.decode("ascii"),
                    path=_validate_pathspec(path),
                )
            except (UnicodeDecodeError, ValueError) as exc:
                raise GitBackendError("read_index", "git returned malformed index data") from exc
            if stage_number != b"0" or not _SHA_RE.fullmatch(entry.sha):
                raise GitBackendError("read_index", "unmerged or malformed index entry")
            if entry.path in entries:
                raise GitBackendError("read_index", "duplicate index path")
            entries[entry.path] = entry
        return entries

    def _untracked_paths(
        self,
        location: GitLocation,
        *,
        env_overrides: Mapping[str, str] | None = None,
    ) -> tuple[str, ...]:
        result = self._invoke(
            "read_untracked",
            location,
            ("ls-files", "--others", "--exclude-standard", "-z", "--"),
            env_overrides=env_overrides,
            max_output_bytes=min(self._materialization_limit_bytes, 20_000_000),
        )
        self._require_complete_output("read_untracked", result)
        paths: list[str] = []
        for raw_path in result.stdout_bytes.split(b"\x00"):
            if raw_path:
                paths.append(_validate_pathspec(os.fsdecode(raw_path)))
        return tuple(paths)

    def _hash_worktree_path(
        self,
        location: GitLocation,
        relative_path: str,
        *,
        env_overrides: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        target = self._safe_worktree_path(location, relative_path)
        try:
            file_stat = target.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                mode = "120000"
                content = os.fsencode(os.readlink(target))
                if len(content) > self._blob_limit_bytes:
                    raise GitBackendError(
                        "hash_blob",
                        f"symlink exceeds {self._blob_limit_bytes} byte blob limit: {relative_path}",
                    )
            elif stat.S_ISREG(file_stat.st_mode):
                mode = "100755" if file_stat.st_mode & stat.S_IXUSR else "100644"
                if file_stat.st_size > self._blob_limit_bytes:
                    raise GitBackendError(
                        "hash_blob",
                        f"file exceeds {self._blob_limit_bytes} byte blob limit: {relative_path}",
                    )
                content = target.read_bytes()
            else:
                raise GitBackendError("hash_blob", f"unsupported file type: {relative_path}")
        except OSError as exc:
            raise GitBackendError("hash_blob", str(exc)) from exc
        result = self._invoke(
            "hash_blob",
            location,
            ("hash-object", "-w", "--no-filters", "--stdin"),
            stdin_bytes=content,
            env_overrides=env_overrides,
        )
        sha = result.stdout.strip()
        _validate_sha(sha)
        return mode, sha

    def _preflight_stage_paths(
        self,
        location: GitLocation,
        paths: tuple[str, ...] | list[str],
        entries: Mapping[str, _IndexEntry] | None = None,
    ) -> None:
        if len(paths) > self._stage_path_limit:
            raise GitBackendError(
                "stage_preflight",
                f"staged path count exceeds {self._stage_path_limit} path limit",
            )
        total_bytes = 0
        indexed = entries or {}
        for relative_path in paths:
            target = self._safe_worktree_path(location, relative_path)
            if not target.exists() and not target.is_symlink():
                continue
            entry = indexed.get(relative_path)
            if entry is not None and entry.mode == "160000" and target.is_dir():
                continue
            try:
                file_stat = target.lstat()
                if stat.S_ISLNK(file_stat.st_mode):
                    size = len(os.fsencode(os.readlink(target)))
                elif stat.S_ISREG(file_stat.st_mode):
                    size = file_stat.st_size
                else:
                    raise GitBackendError(
                        "stage_preflight",
                        f"unsupported file type: {relative_path}",
                    )
            except OSError as exc:
                raise GitBackendError("stage_preflight", str(exc)) from exc
            if size > self._blob_limit_bytes:
                raise GitBackendError(
                    "stage_preflight",
                    f"file exceeds {self._blob_limit_bytes} byte blob limit: {relative_path}",
                )
            total_bytes += size
            if total_bytes > self._materialization_limit_bytes:
                raise GitBackendError(
                    "stage_preflight",
                    f"staged content exceeds {self._materialization_limit_bytes} byte limit",
                )

    def _stage_paths_raw(
        self,
        location: GitLocation,
        paths: tuple[str, ...] | list[str],
        *,
        entries: Mapping[str, _IndexEntry] | None = None,
        env_overrides: Mapping[str, str] | None = None,
        index_location: GitLocation | None = None,
    ) -> None:
        update_location = index_location or location
        indexed = entries or {}
        self._preflight_stage_paths(location, paths, indexed)
        records: list[bytes] = []
        regular_files: list[tuple[str, str]] = []
        deleted_paths: list[str] = []
        for relative_path in paths:
            target = self._safe_worktree_path(location, relative_path)
            entry = indexed.get(relative_path)
            if entry is not None and entry.mode == "160000" and target.is_dir():
                continue
            if not target.exists() and not target.is_symlink():
                deleted_paths.append(relative_path)
                continue
            file_stat = target.lstat()
            if stat.S_ISLNK(file_stat.st_mode):
                mode, sha = self._hash_worktree_path(
                    location,
                    relative_path,
                    env_overrides=env_overrides,
                )
                records.append(
                    f"{mode} {sha}\t".encode("ascii") + os.fsencode(relative_path) + b"\x00"
                )
                continue
            mode = "100755" if file_stat.st_mode & stat.S_IXUSR else "100644"
            regular_files.append((relative_path, mode))

        if deleted_paths:
            zero_oid = self._zero_oid(location)
            records.extend(
                f"0 {zero_oid}\t".encode("ascii") + os.fsencode(path) + b"\x00"
                for path in deleted_paths
            )
        if regular_files:
            path_input = b"".join(os.fsencode(path) + b"\n" for path, _mode in regular_files)
            hashes_result = self._invoke(
                "hash_blobs",
                location,
                ("hash-object", "-w", "--no-filters", "--stdin-paths"),
                stdin_bytes=path_input,
                env_overrides=env_overrides,
                max_output_bytes=max(
                    self._output_limit_bytes,
                    self._stage_path_limit * 66,
                ),
                stdin_limit_bytes=self._stage_metadata_limit_bytes,
            )
            self._require_complete_output("hash_blobs", hashes_result)
            hashes = hashes_result.stdout.splitlines()
            if len(hashes) != len(regular_files) or any(
                not _SHA_RE.fullmatch(sha) for sha in hashes
            ):
                raise GitBackendError("hash_blobs", "git returned malformed blob hashes")
            records.extend(
                f"{mode} {sha}\t".encode("ascii") + os.fsencode(path) + b"\x00"
                for (path, mode), sha in zip(regular_files, hashes, strict=True)
            )
        if not records:
            return
        self._invoke(
            "update_index_paths",
            update_location,
            ("update-index", "-z", "--index-info"),
            stdin_bytes=b"".join(records),
            env_overrides=env_overrides,
            stdin_limit_bytes=self._stage_metadata_limit_bytes,
        )

    def _stage_all_raw(
        self,
        location: GitLocation,
        *,
        env_overrides: Mapping[str, str] | None = None,
        index_location: GitLocation | None = None,
    ) -> None:
        entries = self._index_entries(location, env_overrides=env_overrides)
        paths = sorted(
            set(entries).union(self._untracked_paths(location, env_overrides=env_overrides))
        )
        self._stage_paths_raw(
            location,
            paths,
            entries=entries,
            env_overrides=env_overrides,
            index_location=index_location,
        )

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    def _remove_worktree_paths(self, location: GitLocation, paths: set[str]) -> None:
        root = location.work_tree.resolve()
        for relative_path in sorted(paths, key=lambda value: len(Path(value).parts), reverse=True):
            target = self._safe_worktree_path(location, relative_path)
            if target == root / ".git":
                raise GitBackendError("materialize_index", "refusing to replace worktree metadata")
            self._remove_path(target)
            parent = target.parent
            while parent != root:
                with suppress(OSError):
                    parent.rmdir()
                parent = parent.parent

    def _prepare_parent(self, root: Path, target: Path) -> None:
        cursor = root
        for part in target.relative_to(root).parts[:-1]:
            cursor /= part
            if cursor.is_symlink() or (cursor.exists() and not cursor.is_dir()):
                self._remove_path(cursor)
            cursor.mkdir(exist_ok=True)

    def _materialize_index(self, location: GitLocation) -> None:
        entries = self._index_entries(location)
        if len(entries) > self._stage_path_limit:
            raise GitBackendError(
                "materialize_index",
                f"worktree path count exceeds {self._stage_path_limit} path limit",
            )
        root = location.work_tree.resolve()
        materialized: list[tuple[_IndexEntry, Path]] = []
        gitlinks: list[tuple[_IndexEntry, Path]] = []
        for entry in entries.values():
            target = self._safe_worktree_path(location, entry.path)
            if target == root / ".git":
                raise GitBackendError("materialize_index", "refusing to replace worktree metadata")
            if entry.mode == "160000":
                gitlinks.append((entry, target))
                continue
            if entry.mode not in {"100644", "100755", "120000"}:
                raise GitBackendError("materialize_index", f"unsupported Git mode {entry.mode}")
            materialized.append((entry, target))

        sha_input = b"".join(entry.sha.encode("ascii") + b"\n" for entry, _ in materialized)
        sizes_result = self._invoke(
            "read_blob_sizes",
            location,
            ("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
            stdin_bytes=sha_input,
            stdin_limit_bytes=self._stage_metadata_limit_bytes,
            max_output_bytes=max(self._output_limit_bytes, len(materialized) * 128 + 1),
        )
        self._require_complete_output("read_blob_sizes", sizes_result)
        size_lines = sizes_result.stdout.splitlines()
        if len(size_lines) != len(materialized):
            raise GitBackendError("read_blob_sizes", "git returned a malformed blob size batch")
        sizes: list[int] = []
        total_bytes = 0
        for (entry, _target), line in zip(materialized, size_lines, strict=True):
            fields = line.split()
            try:
                object_sha, object_type, raw_size = fields
                blob_size = int(raw_size)
            except (ValueError, TypeError) as exc:
                raise GitBackendError(
                    "read_blob_sizes", "git returned an invalid blob size"
                ) from exc
            if object_sha.lower() != entry.sha.lower() or object_type != "blob":
                raise GitBackendError("read_blob_sizes", "git returned an unexpected object")
            if blob_size < 0 or blob_size > self._blob_limit_bytes:
                raise GitBackendError(
                    "read_blob_sizes",
                    f"blob exceeds {self._blob_limit_bytes} byte limit",
                )
            if total_bytes + blob_size > self._materialization_limit_bytes:
                raise GitBackendError(
                    "materialize_index",
                    f"worktree exceeds {self._materialization_limit_bytes} byte limit",
                )
            sizes.append(blob_size)
            total_bytes += blob_size

        batches: list[list[int]] = []
        current_batch: list[int] = []
        current_bytes = 0
        for index, blob_size in enumerate(sizes):
            if current_batch and current_bytes + blob_size > self._blob_limit_bytes:
                batches.append(current_batch)
                current_batch = []
                current_bytes = 0
            current_batch.append(index)
            current_bytes += blob_size
        if current_batch:
            batches.append(current_batch)

        blob_locations: list[tuple[int, int] | None] = [None] * len(materialized)
        with tempfile.TemporaryFile() as blob_store:
            for batch in batches:
                batch_input = b"".join(
                    materialized[index][0].sha.encode("ascii") + b"\n" for index in batch
                )
                batch_bytes = sum(sizes[index] for index in batch)
                blobs_result = self._invoke(
                    "read_blobs",
                    location,
                    ("cat-file", "--batch"),
                    stdin_bytes=batch_input,
                    stdin_limit_bytes=self._stage_metadata_limit_bytes,
                    max_output_bytes=max(
                        self._output_limit_bytes,
                        batch_bytes + len(batch) * 128 + 1,
                    ),
                )
                self._require_complete_output("read_blobs", blobs_result)
                offset = 0
                payload = blobs_result.stdout_bytes
                for index in batch:
                    entry = materialized[index][0]
                    blob_size = sizes[index]
                    header_end = payload.find(b"\n", offset, offset + 129)
                    if header_end < 0:
                        raise GitBackendError("read_blobs", "git returned a malformed blob header")
                    try:
                        raw_sha, object_type, raw_size = payload[offset:header_end].split()
                        header_size = int(raw_size)
                    except (ValueError, TypeError) as exc:
                        raise GitBackendError(
                            "read_blobs", "git returned a malformed blob header"
                        ) from exc
                    content_start = header_end + 1
                    content_end = content_start + blob_size
                    if (
                        raw_sha.lower() != entry.sha.encode("ascii").lower()
                        or object_type != b"blob"
                        or header_size != blob_size
                        or content_end >= len(payload)
                        or payload[content_end : content_end + 1] != b"\n"
                    ):
                        raise GitBackendError("read_blobs", "git returned malformed blob content")
                    store_offset = blob_store.tell()
                    blob_store.write(payload[content_start:content_end])
                    blob_locations[index] = (store_offset, blob_size)
                    offset = content_end + 1
                if offset != len(payload):
                    raise GitBackendError("read_blobs", "git returned trailing blob content")

            for _entry, target in gitlinks:
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    self._remove_path(target)
                target.mkdir(parents=True, exist_ok=True)
            for index, (entry, target) in enumerate(materialized):
                blob_location = blob_locations[index]
                if blob_location is None:
                    raise GitBackendError("read_blobs", "blob content was not materialized")
                store_offset, blob_size = blob_location
                blob_store.seek(store_offset)
                blob = blob_store.read(blob_size)
                if len(blob) != blob_size:
                    raise GitBackendError("read_blobs", "blob spool was truncated")
                self._prepare_parent(root, target)
                self._remove_path(target)
                try:
                    if entry.mode == "120000":
                        os.symlink(os.fsdecode(blob), target)
                    else:
                        target.write_bytes(blob)
                        target.chmod(0o755 if entry.mode == "100755" else 0o644)
                except (OSError, ValueError) as exc:
                    raise GitBackendError("materialize_index", str(exc)) from exc

    def _require_complete_output(self, operation: str, result: _InvocationResult) -> None:
        if result.output_truncated:
            raise GitBackendError(
                operation,
                f"output exceeded {result.output_limit_bytes} bytes",
                returncode=result.returncode,
                output_truncated=True,
            )

    @staticmethod
    def _drain_pipe(
        read_fd: int,
        limit: int,
        captures: dict[str, tuple[bytes, bool, OSError | None]],
        key: str,
    ) -> None:
        kept = bytearray()
        total = 0
        error: OSError | None = None
        try:
            while True:
                chunk = os.read(read_fd, 65_536)
                if not chunk:
                    break
                total += len(chunk)
                if len(kept) < limit:
                    kept.extend(chunk[: limit - len(kept)])
        except OSError as exc:
            error = exc
        finally:
            with suppress(OSError):
                os.close(read_fd)
        captures[key] = (bytes(kept), total > limit, error)

    def _invoke(
        self,
        operation: str,
        location: GitLocation,
        args: tuple[str, ...],
        *,
        identity: GitIdentity | None = None,
        stdin_bytes: bytes | None = None,
        env_overrides: Mapping[str, str] | None = None,
        max_output_bytes: int | None = None,
        stdin_limit_bytes: int | None = None,
    ) -> _InvocationResult:
        if self._git_executable is None:
            raise GitBackendError(
                operation,
                f"trusted git executable was not found under {os.defpath}",
            )
        input_limit = stdin_limit_bytes or self._blob_limit_bytes
        if input_limit <= 0:
            raise ValueError("stdin_limit_bytes must be positive")
        if stdin_bytes is not None and len(stdin_bytes) > input_limit:
            raise GitBackendError(
                operation,
                f"input exceeded {input_limit} byte limit",
            )
        output_limit = max_output_bytes or self._output_limit_bytes
        if output_limit <= 0:
            raise ValueError("max_output_bytes must be positive")
        command = [
            self._git_executable,
            "-c",
            f"core.hooksPath={os.devnull}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            f"core.attributesFile={os.devnull}",
            "-c",
            f"core.excludesFile={os.devnull}",
            "-c",
            "diff.external=",
            "-c",
            "diff.trustExitCode=false",
            "-c",
            "credential.helper=",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "tag.gpgSign=false",
            "-c",
            "log.showSignature=false",
            "-c",
            "gc.auto=0",
            "-c",
            "maintenance.auto=false",
            "-c",
            "submodule.recurse=false",
        ]
        if location.git_dir is not None:
            command.append(f"--git-dir={location.git_dir}")
        command.append(f"--work-tree={location.work_tree}")
        command.extend(args)

        env = build_clean_env("", base_env=dict(os.environ))
        env.update(
            {
                "GCM_INTERACTIVE": "Never",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_EXTERNAL_DIFF": "",
                "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_PAGER": "cat",
                "GIT_TERMINAL_PROMPT": "0",
                "PAGER": "cat",
                "PATH": os.defpath,
            }
        )
        if env_overrides is not None:
            keys = set(env_overrides)
            allowed_key_sets = (
                {"GIT_INDEX_FILE"},
                {
                    "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                },
            )
            if keys not in allowed_key_sets:
                raise ValueError("only private Git index/object paths may override environment")
            index_file = Path(env_overrides["GIT_INDEX_FILE"])
            if not index_file.is_absolute() or "\x00" in str(index_file):
                raise ValueError("alternate Git index path must be absolute")
            if not index_file.parent.is_dir():
                raise ValueError("alternate Git index parent must exist")
            if "GIT_OBJECT_DIRECTORY" in env_overrides:
                object_directory = Path(env_overrides["GIT_OBJECT_DIRECTORY"])
                alternate = env_overrides["GIT_ALTERNATE_OBJECT_DIRECTORIES"]
                if (
                    not object_directory.is_absolute()
                    or not object_directory.is_dir()
                    or not alternate
                    or any(char in alternate for char in ("\x00", "\r", "\n", os.pathsep))
                ):
                    raise ValueError("private Git object paths are invalid")
                alternate_path = Path(alternate)
                if not alternate_path.is_absolute() or not alternate_path.is_dir():
                    raise ValueError("source Git object directory must be absolute and existing")
            env.update(env_overrides)
        if identity is not None:
            env.update(
                {
                    "GIT_AUTHOR_EMAIL": identity.email,
                    "GIT_AUTHOR_NAME": identity.name,
                    "GIT_COMMITTER_EMAIL": identity.email,
                    "GIT_COMMITTER_NAME": identity.name,
                }
            )

        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        captures: dict[str, tuple[bytes, bool, OSError | None]] = {}
        readers = (
            threading.Thread(
                target=self._drain_pipe,
                args=(stdout_read, output_limit, captures, "stdout"),
                name="tianshu-git-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=self._drain_pipe,
                args=(stderr_read, output_limit, captures, "stderr"),
                name="tianshu-git-stderr",
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()

        completed: subprocess.CompletedProcess[bytes] | None = None
        run_error: BaseException | None = None
        with tempfile.TemporaryFile() as stdin:
            process_stdin: int | BinaryIO = subprocess.DEVNULL
            if stdin_bytes is not None:
                stdin.write(stdin_bytes)
                stdin.seek(0)
                process_stdin = stdin
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(location.work_tree),
                    env=env,
                    stdin=process_stdin,
                    stdout=stdout_write,
                    stderr=stderr_write,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except BaseException as exc:  # cleanup pipes before translating or re-raising
                run_error = exc
            finally:
                with suppress(OSError):
                    os.close(stdout_write)
                with suppress(OSError):
                    os.close(stderr_write)

        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            with suppress(OSError):
                os.close(stdout_read)
            with suppress(OSError):
                os.close(stderr_read)
            for reader in readers:
                reader.join(timeout=1)
            raise GitBackendError(operation, "bounded output drain did not terminate")

        if isinstance(run_error, subprocess.TimeoutExpired):
            raise GitBackendError(operation, "timed out", timed_out=True) from run_error
        if isinstance(run_error, (FileNotFoundError, OSError)):
            raise GitBackendError(operation, str(run_error)) from run_error
        if run_error is not None:
            raise run_error
        assert completed is not None

        stdout_bytes, stdout_truncated, stdout_error = captures["stdout"]
        stderr_bytes, stderr_truncated, stderr_error = captures["stderr"]
        if stdout_error is not None or stderr_error is not None:
            detail = stdout_error or stderr_error
            raise GitBackendError(operation, f"bounded output drain failed: {detail}")
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        output_truncated = stdout_truncated or stderr_truncated
        if completed.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip() or "unknown git error"
            raise GitBackendError(
                operation,
                detail,
                returncode=completed.returncode,
                output_truncated=output_truncated,
            )
        return _InvocationResult(
            stdout_bytes=stdout_bytes,
            stdout=stdout_text,
            stderr=stderr_text,
            returncode=completed.returncode,
            output_truncated=output_truncated,
            output_limit_bytes=output_limit,
        )


__all__ = [
    "GitBackend",
    "GitBackendError",
    "GitIdentity",
    "GitLocation",
    "GitLogEntry",
    "trusted_git_executable",
]
