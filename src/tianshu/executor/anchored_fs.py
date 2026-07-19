"""Root-anchored filesystem primitives for governed workspace operations."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4


class AnchoredFilesystemError(RuntimeError):
    """A root-anchored operation could not be proven safe."""


@dataclass(frozen=True)
class AnchoredEntry:
    kind: Literal["missing", "file", "symlink", "directory"]
    mode: int | None = None
    device: int | None = None
    inode: int | None = None
    size: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    content: bytes | None = field(default=None, repr=False)

    def semantically_matches(self, other: AnchoredEntry) -> bool:
        if self.kind != other.kind:
            return False
        if self.kind == "missing":
            return True
        if self.kind == "directory":
            return (
                self.mode == other.mode
                and self.device == other.device
                and self.inode == other.inode
            )
        return self.mode == other.mode and self.content == other.content

    def same_owned_object(self, other: AnchoredEntry) -> bool:
        """Match one inode across a publish operation that may update ctime."""

        return (
            self.kind == other.kind
            and self.kind in {"file", "symlink"}
            and self.device is not None
            and self.inode is not None
            and (self.device, self.inode) == (other.device, other.inode)
            and self.mode == other.mode
            and self.size == other.size
            and self.content == other.content
        )


def safe_relative_parts(relative_path: str) -> tuple[str, ...]:
    path = PurePosixPath(relative_path)
    if (
        not relative_path
        or relative_path.startswith("/")
        or "\\" in relative_path
        or "\x00" in relative_path
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts)
    ):
        raise AnchoredFilesystemError("workspace path is unsafe")
    return path.parts


class AnchoredRoot:
    """Operate relative to one verified root directory descriptor."""

    def __init__(
        self,
        root: Path,
        *,
        byte_limit: int,
        expected_device: int | None = None,
        expected_inode: int | None = None,
        operation_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        self._published_root = Path(os.path.abspath(os.fspath(root)))
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(self._published_root, flags)
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise AnchoredFilesystemError("workspace root is unavailable") from exc
        self._descriptor: int | None = descriptor
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise AnchoredFilesystemError("workspace root is not a directory")
        if (
            expected_device is not None
            and expected_inode is not None
            and (metadata.st_dev, metadata.st_ino) != (expected_device, expected_inode)
        ):
            os.close(descriptor)
            raise AnchoredFilesystemError("workspace root identity changed")
        self.device = metadata.st_dev
        self.inode = metadata.st_ino
        self.mode = stat.S_IMODE(metadata.st_mode)
        self._byte_limit = byte_limit
        self._operation_hook = operation_hook

    def _inject(self, stage: str, relative_path: str) -> None:
        if self._operation_hook is not None:
            self._operation_hook(stage, relative_path)

    def _descriptor_fd(self) -> int:
        descriptor = self._descriptor
        if descriptor is None:
            raise AnchoredFilesystemError("workspace root descriptor is closed")
        return descriptor

    def assert_published_root(self) -> None:
        """Prove the published root path still names the pinned directory."""

        descriptor: int | None = None
        try:
            before = os.stat(self._published_root, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise AnchoredFilesystemError("published workspace root identity changed")
            descriptor = os.open(self._published_root, self._directory_flags())
            opened = os.fstat(descriptor)
            after = os.stat(self._published_root, follow_symlinks=False)
            expected = (self.device, self.inode, self.mode)
            for observed in (before, opened, after):
                if (
                    not stat.S_ISDIR(observed.st_mode)
                    or (observed.st_dev, observed.st_ino, stat.S_IMODE(observed.st_mode))
                    != expected
                ):
                    raise AnchoredFilesystemError("published workspace root identity changed")
        except AnchoredFilesystemError:
            raise
        except OSError as exc:
            raise AnchoredFilesystemError("published workspace root is unavailable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def close(self) -> None:
        descriptor = getattr(self, "_descriptor", None)
        if descriptor is not None:
            os.close(descriptor)
            self._descriptor = None

    def __enter__(self) -> AnchoredRoot:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @contextmanager
    def borrowed(self) -> Iterator[AnchoredRoot]:
        """Borrow the descriptor without transferring lifecycle ownership."""

        yield self

    @staticmethod
    def _directory_flags() -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    @contextmanager
    def parent(
        self,
        relative_path: str,
        *,
        create: bool = False,
        created: dict[str, AnchoredEntry] | None = None,
    ) -> Iterator[tuple[int, str]]:
        parts = safe_relative_parts(relative_path)
        descriptor = os.dup(self._descriptor_fd())
        traversed: list[str] = []
        try:
            for component in parts[:-1]:
                traversed.append(component)
                try:
                    child = os.open(
                        component,
                        self._directory_flags(),
                        dir_fd=descriptor,
                    )
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o755, dir_fd=descriptor)
                    child = os.open(
                        component,
                        self._directory_flags(),
                        dir_fd=descriptor,
                    )
                    if created is not None:
                        metadata = os.fstat(child)
                        created["/".join(traversed)] = self._from_stat(
                            metadata,
                            kind="directory",
                        )
                except OSError as exc:
                    raise AnchoredFilesystemError(
                        "workspace parent is not a real directory"
                    ) from exc
                os.close(descriptor)
                descriptor = child
            try:
                yield descriptor, parts[-1]
            finally:
                if created is not None:
                    active_error = sys.exc_info()[0] is not None
                    for relative_path in tuple(created):
                        try:
                            created[relative_path] = self.capture(relative_path)
                        except AnchoredFilesystemError:
                            if not active_error:
                                raise
        finally:
            os.close(descriptor)

    @staticmethod
    def _from_stat(
        metadata: os.stat_result,
        *,
        kind: Literal["file", "symlink", "directory"],
        content: bytes | None = None,
    ) -> AnchoredEntry:
        return AnchoredEntry(
            kind=kind,
            mode=stat.S_IMODE(metadata.st_mode),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
            content=content,
        )

    @staticmethod
    def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
        return (
            left.st_dev == right.st_dev
            and left.st_ino == right.st_ino
            and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
            and left.st_size == right.st_size
            and left.st_mtime_ns == right.st_mtime_ns
            and left.st_ctime_ns == right.st_ctime_ns
        )

    def _capture_at(self, parent_fd: int, leaf: str) -> AnchoredEntry:
        try:
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return AnchoredEntry("missing")
        except OSError as exc:
            raise AnchoredFilesystemError("workspace leaf is unavailable") from exc
        self._inject("after_leaf_stat_before_open", leaf)
        try:
            if stat.S_ISLNK(before.st_mode):
                content = os.fsencode(os.readlink(leaf, dir_fd=parent_fd))
                after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if not self._same_identity(before, after):
                    raise AnchoredFilesystemError("workspace symlink changed while reading")
                if len(content) > self._byte_limit:
                    raise AnchoredFilesystemError("workspace leaf exceeds the byte limit")
                return self._from_stat(after, kind="symlink", content=content)
            if stat.S_ISDIR(before.st_mode):
                descriptor = os.open(leaf, self._directory_flags(), dir_fd=parent_fd)
                try:
                    opened = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
                after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if not self._same_identity(before, opened) or not self._same_identity(
                    opened, after
                ):
                    raise AnchoredFilesystemError("workspace directory identity changed")
                return self._from_stat(after, kind="directory")
            if not stat.S_ISREG(before.st_mode):
                raise AnchoredFilesystemError("workspace leaf type is unsupported")
            if before.st_size > self._byte_limit:
                raise AnchoredFilesystemError("workspace leaf exceeds the byte limit")
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(leaf, flags, dir_fd=parent_fd)
            try:
                opened = os.fstat(descriptor)
                if not self._same_identity(before, opened):
                    raise AnchoredFilesystemError("workspace file identity changed")
                self._inject("after_open_before_read", leaf)
                chunks: list[bytes] = []
                remaining = opened.st_size
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 65_536))
                    if not chunk:
                        raise AnchoredFilesystemError("workspace file was truncated")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                after_open = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            after_name = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not self._same_identity(opened, after_open) or not self._same_identity(
                after_open, after_name
            ):
                raise AnchoredFilesystemError("workspace file changed while reading")
            return self._from_stat(after_name, kind="file", content=b"".join(chunks))
        except AnchoredFilesystemError:
            raise
        except OSError as exc:
            raise AnchoredFilesystemError("workspace leaf operation failed") from exc

    def capture(self, relative_path: str) -> AnchoredEntry:
        try:
            with self.parent(relative_path) as (parent_fd, leaf):
                return self._capture_at(parent_fd, leaf)
        except FileNotFoundError:
            return AnchoredEntry("missing")

    def capture_directory(self, relative_path: str) -> AnchoredEntry:
        if relative_path == ".":
            return self._from_stat(os.fstat(self._descriptor_fd()), kind="directory")
        entry = self.capture(f"{relative_path}/.tianshu-directory-probe")
        if entry.kind != "missing":  # pragma: no cover - reserved probe cannot be materialized
            raise AnchoredFilesystemError("workspace directory probe collided")
        parts = safe_relative_parts(f"{relative_path}/probe")
        descriptor = os.dup(self._descriptor_fd())
        try:
            for component in parts[:-1]:
                child = os.open(component, self._directory_flags(), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return self._from_stat(os.fstat(descriptor), kind="directory")
        except OSError as exc:
            raise AnchoredFilesystemError("workspace parent identity changed") from exc
        finally:
            os.close(descriptor)

    def remove(
        self,
        relative_path: str,
        expected: AnchoredEntry,
        *,
        exact: bool,
        before_leaf: Callable[[], None] | None = None,
    ) -> AnchoredEntry:
        try:
            with self.parent(relative_path) as (parent_fd, leaf):
                if before_leaf is not None:
                    before_leaf()
                    self._verify_parent_binding(relative_path, parent_fd)
                current = self._capture_at(parent_fd, leaf)
                matches = current == expected if exact else current.semantically_matches(expected)
                if not matches:
                    raise AnchoredFilesystemError("workspace leaf compare-and-swap failed")
                if current.kind == "missing":
                    return current
                if current.kind == "directory":
                    os.rmdir(leaf, dir_fd=parent_fd)
                else:
                    os.unlink(leaf, dir_fd=parent_fd)
                return self._capture_at(parent_fd, leaf)
        except FileNotFoundError:
            if expected.kind == "missing":
                return AnchoredEntry("missing")
            raise AnchoredFilesystemError("workspace parent disappeared") from None
        except AnchoredFilesystemError:
            raise
        except OSError as exc:
            raise AnchoredFilesystemError("workspace leaf removal failed") from exc

    def _verify_parent_binding(self, relative_path: str, expected_parent_fd: int) -> None:
        try:
            with self.parent(relative_path) as (current_parent_fd, _leaf):
                expected = os.fstat(expected_parent_fd)
                current = os.fstat(current_parent_fd)
        except (OSError, AnchoredFilesystemError, FileNotFoundError) as exc:
            raise AnchoredFilesystemError("workspace parent binding changed") from exc
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise AnchoredFilesystemError("workspace parent binding changed")

    def write_regular(
        self,
        relative_path: str,
        content: bytes,
        mode: int,
        *,
        created: dict[str, AnchoredEntry] | None = None,
        before_leaf: Callable[[], None] | None = None,
        on_prepared: Callable[[AnchoredEntry], None] | None = None,
        on_published: Callable[[AnchoredEntry], None] | None = None,
    ) -> AnchoredEntry:
        with self.parent(relative_path, create=True, created=created) as (parent_fd, leaf):
            if before_leaf is not None:
                before_leaf()
                self._verify_parent_binding(relative_path, parent_fd)
            if self._capture_at(parent_fd, leaf).kind != "missing":
                raise AnchoredFilesystemError("workspace target appeared before write")
            temp = f".tianshu-apply-{uuid4().hex}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor: int | None = None
            try:
                descriptor = os.open(temp, flags, 0o600, dir_fd=parent_fd)
                offset = 0
                while offset < len(content):
                    offset += os.write(descriptor, content[offset:])
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
                temp_identity = os.fstat(descriptor)
                prepared = self._from_stat(temp_identity, kind="file", content=content)
                if on_prepared is not None:
                    on_prepared(prepared)
                self._inject("after_write_absence_check_before_publish", relative_path)
                os.link(
                    temp,
                    leaf,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                self._inject("after_regular_publish_before_identity_check", relative_path)
                published = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(published.st_mode)
                    or (published.st_dev, published.st_ino)
                    != (temp_identity.st_dev, temp_identity.st_ino)
                    or stat.S_IMODE(published.st_mode) != stat.S_IMODE(temp_identity.st_mode)
                ):
                    raise AnchoredFilesystemError("published file identity changed")
                os.unlink(temp, dir_fd=parent_fd)
                published = os.fstat(descriptor)
                postimage = self._from_stat(published, kind="file", content=content)
                if on_published is not None:
                    on_published(postimage)
                return postimage
            except AnchoredFilesystemError:
                raise
            except OSError as exc:
                raise AnchoredFilesystemError("workspace regular write failed") from exc
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                with suppress(FileNotFoundError):
                    os.unlink(temp, dir_fd=parent_fd)

    def write_symlink(
        self,
        relative_path: str,
        content: bytes,
        *,
        created: dict[str, AnchoredEntry] | None = None,
        before_leaf: Callable[[], None] | None = None,
        on_prepared: Callable[[AnchoredEntry], None] | None = None,
        on_published: Callable[[AnchoredEntry], None] | None = None,
    ) -> AnchoredEntry:
        with self.parent(relative_path, create=True, created=created) as (parent_fd, leaf):
            if before_leaf is not None:
                before_leaf()
                self._verify_parent_binding(relative_path, parent_fd)
            if self._capture_at(parent_fd, leaf).kind != "missing":
                raise AnchoredFilesystemError("workspace target appeared before symlink write")
            temp = f".tianshu-apply-{uuid4().hex}"
            try:
                os.symlink(os.fsdecode(content), temp, dir_fd=parent_fd)
                temp_identity = os.stat(temp, dir_fd=parent_fd, follow_symlinks=False)
                prepared = self._from_stat(temp_identity, kind="symlink", content=content)
                if on_prepared is not None:
                    on_prepared(prepared)
                self._inject("after_write_absence_check_before_publish", relative_path)
                os.link(
                    temp,
                    leaf,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                self._inject("after_symlink_publish_before_identity_check", relative_path)
                published = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat.S_ISLNK(published.st_mode)
                    or (published.st_dev, published.st_ino)
                    != (temp_identity.st_dev, temp_identity.st_ino)
                    or stat.S_IMODE(published.st_mode) != stat.S_IMODE(temp_identity.st_mode)
                ):
                    raise AnchoredFilesystemError("published symlink identity changed")
                os.unlink(temp, dir_fd=parent_fd)
                published = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                if not stat.S_ISLNK(published.st_mode) or (published.st_dev, published.st_ino) != (
                    temp_identity.st_dev,
                    temp_identity.st_ino,
                ):
                    raise AnchoredFilesystemError("published symlink identity changed")
                postimage = self._from_stat(published, kind="symlink", content=content)
                if on_published is not None:
                    on_published(postimage)
                return postimage
            except AnchoredFilesystemError:
                raise
            except (OSError, ValueError) as exc:
                raise AnchoredFilesystemError("workspace symlink write failed") from exc
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temp, dir_fd=parent_fd)

    def write_directory(
        self,
        relative_path: str,
        mode: int,
        *,
        created: dict[str, AnchoredEntry] | None = None,
    ) -> AnchoredEntry:
        with self.parent(relative_path, create=True, created=created) as (parent_fd, leaf):
            if self._capture_at(parent_fd, leaf).kind != "missing":
                raise AnchoredFilesystemError("workspace directory target appeared")
            try:
                os.mkdir(leaf, mode, dir_fd=parent_fd)
                descriptor = os.open(leaf, self._directory_flags(), dir_fd=parent_fd)
                try:
                    os.fchmod(descriptor, mode)
                finally:
                    os.close(descriptor)
            except OSError as exc:
                raise AnchoredFilesystemError("workspace directory write failed") from exc
            return self._capture_at(parent_fd, leaf)


__all__ = [
    "AnchoredEntry",
    "AnchoredFilesystemError",
    "AnchoredRoot",
    "safe_relative_parts",
]
