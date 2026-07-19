"""Exact filesystem transaction used by governed workspace apply.

The database claim and filesystem effect cannot be one atomic transaction.  This
module therefore guarantees synchronous rollback only; a host crash after claim
is reported by the service as an explicit claim/effect gap.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from tianshu.executor.anchored_fs import (
    AnchoredEntry,
    AnchoredFilesystemError,
    AnchoredRoot,
    safe_relative_parts,
)
from tianshu.executor.git_backend import GitBackend, GitLocation, GitWorktreeEntry
from tianshu.models.workspace import CanonicalChange

_MAX_PATHS = 10_000
_MAX_TRANSACTION_BYTES = 1_000_000_000


class WorkspaceMaterializationError(RuntimeError):
    """The canonical set cannot be safely materialized or restored."""


class WorkspacePreimageDrift(WorkspaceMaterializationError):
    """The source no longer matches the exact backup before first mutation."""


@dataclass(frozen=True)
class ApplyPlan:
    changes: tuple[CanonicalChange, ...]
    desired: dict[str, GitWorktreeEntry]
    removals: frozenset[str]
    affected: tuple[str, ...]


@dataclass
class SourceBackup:
    root: Path
    root_device: int
    root_inode: int
    filesystem: AnchoredRoot = field(repr=False)
    entries: dict[str, AnchoredEntry]
    parents: dict[str, AnchoredEntry]
    created_directories: dict[str, AnchoredEntry] = field(default_factory=dict)
    owned_postimages: dict[str, AnchoredEntry] = field(default_factory=dict)
    pending_paths: set[str] = field(default_factory=set)
    pending_beforeimages: dict[str, AnchoredEntry] = field(default_factory=dict)
    pending_postimages: dict[str, AnchoredEntry] = field(default_factory=dict)
    pending_exact_postimages: dict[str, AnchoredEntry] = field(default_factory=dict)
    mutated_paths: set[str] = field(default_factory=set)
    rollback_removed_paths: set[str] = field(default_factory=set)
    mutation_started: bool = False


class WorkspaceApplyEngine:
    def __init__(
        self,
        git_backend: GitBackend,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self._git = git_backend
        self._failure_injector = failure_injector

    def _inject(self, stage: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(stage)

    def preflight(
        self,
        source: GitLocation,
        staging: GitLocation,
        changes: tuple[CanonicalChange, ...],
    ) -> ApplyPlan:
        if len(changes) > _MAX_PATHS:
            raise WorkspaceMaterializationError("canonical path count exceeds safety limit")
        desired: dict[str, GitWorktreeEntry] = {}
        removals = {
            change.old_path
            for change in changes
            if change.kind in {"delete", "rename"} and change.old_path is not None
        }
        affected = {
            path
            for change in changes
            for path in (change.old_path, change.new_path)
            if path is not None
        }
        if len(affected) > _MAX_PATHS * 2:
            raise WorkspaceMaterializationError("affected path count exceeds safety limit")
        self._reject_target_prefix_conflicts(
            tuple(change.new_path for change in changes if change.new_path is not None)
        )
        total_bytes = 0
        for change in changes:
            if change.old_path is not None:
                old = self._read_expected(
                    source,
                    change.old_path,
                    oid=change.old_oid,
                    mode=change.old_mode,
                    size=change.old_size,
                )
                total_bytes += old.size
            if change.new_path is not None:
                new = self._read_expected(
                    staging,
                    change.new_path,
                    oid=change.new_oid,
                    mode=change.new_mode,
                    size=change.new_size,
                )
                desired[change.new_path] = new
                total_bytes += new.size
                self._validate_target_availability(
                    source.work_tree,
                    change,
                    removals,
                )
            if total_bytes > _MAX_TRANSACTION_BYTES:
                raise WorkspaceMaterializationError(
                    "canonical transaction exceeds byte safety limit"
                )
        return ApplyPlan(
            changes=changes,
            desired=desired,
            removals=frozenset(removals),
            affected=tuple(sorted(affected, key=lambda item: os.fsencode(item))),
        )

    def backup(self, source_root: Path, plan: ApplyPlan) -> SourceBackup:
        entries: dict[str, AnchoredEntry] = {}
        parents: dict[str, AnchoredEntry] = {}
        total_bytes = 0
        root = AnchoredRoot(
            source_root,
            byte_limit=_MAX_TRANSACTION_BYTES,
            operation_hook=lambda stage, _path: self._inject(stage),
        )
        try:
            parents["."] = root.capture_directory(".")
            for relative_path in plan.affected:
                parts = safe_relative_parts(relative_path)
                for length in range(1, len(parts)):
                    parent_path = "/".join(parts[:length])
                    if parent_path in parents:
                        continue
                    parent = root.capture(parent_path)
                    if parent.kind not in {"missing", "directory"}:
                        raise WorkspaceMaterializationError("source parent is not a real directory")
                    parents[parent_path] = parent
                entry = root.capture(relative_path)
                entries[relative_path] = entry
                if entry.content is not None:
                    total_bytes += len(entry.content)
                if total_bytes > _MAX_TRANSACTION_BYTES:
                    raise WorkspaceMaterializationError("source backup exceeds byte safety limit")
            backup = SourceBackup(
                root=Path(source_root),
                root_device=root.device,
                root_inode=root.inode,
                filesystem=root,
                entries=entries,
                parents=parents,
            )
            self._inject("after_backup")
            return backup
        except AnchoredFilesystemError as exc:
            root.close()
            raise WorkspaceMaterializationError("source backup failed") from exc
        except BaseException:
            root.close()
            raise

    def materialize(self, backup: SourceBackup, plan: ApplyPlan) -> None:
        try:
            root = self._open_root(backup)
            self._verify_preimage(root, backup)
            removal_candidates = set(plan.removals) | set(plan.desired)
            for relative_path in sorted(
                removal_candidates,
                key=lambda item: (-len(PurePosixPath(item).parts), os.fsencode(item)),
            ):
                expected = backup.entries[relative_path]
                current = root.capture(relative_path)
                if not current.semantically_matches(expected):
                    raise WorkspacePreimageDrift(
                        "source path changed immediately before materialization"
                    )
                if current.kind == "missing":
                    continue
                backup.pending_paths.add(relative_path)
                backup.pending_beforeimages[relative_path] = current
                backup.pending_postimages[relative_path] = AnchoredEntry("missing")
                backup.mutation_started = True
                postimage = root.remove(
                    relative_path,
                    expected,
                    exact=False,
                    before_leaf=lambda: self._inject("after_materialize_parent_open"),
                )
                backup.owned_postimages[relative_path] = postimage
                backup.mutated_paths.add(relative_path)
                backup.pending_paths.remove(relative_path)
                backup.pending_beforeimages.pop(relative_path, None)
                backup.pending_postimages.pop(relative_path, None)
            for relative_path, entry in sorted(
                plan.desired.items(),
                key=lambda item: (
                    len(PurePosixPath(item[0]).parts),
                    os.fsencode(item[0]),
                ),
            ):
                prior_owned = backup.owned_postimages.get(
                    relative_path,
                    backup.entries[relative_path],
                )
                backup.pending_paths.add(relative_path)
                backup.pending_beforeimages[relative_path] = prior_owned
                backup.mutation_started = True

                def record_pending_exact(
                    postimage: AnchoredEntry,
                    path: str = relative_path,
                ) -> None:
                    backup.pending_exact_postimages[path] = postimage

                if entry.mode == "120000":
                    backup.pending_postimages[relative_path] = AnchoredEntry(
                        "symlink",
                        content=entry.content,
                    )
                    postimage = root.write_symlink(
                        relative_path,
                        entry.content,
                        created=backup.created_directories,
                        before_leaf=lambda: self._inject("after_write_parent_open_before_leaf"),
                        on_prepared=record_pending_exact,
                        on_published=record_pending_exact,
                    )
                else:
                    posix_mode = self._desired_posix_mode(
                        backup,
                        plan,
                        relative_path,
                        entry,
                    )
                    effective_mode = (
                        posix_mode
                        if posix_mode is not None
                        else (0o755 if entry.mode == "100755" else 0o644)
                    )
                    backup.pending_postimages[relative_path] = AnchoredEntry(
                        "file",
                        mode=effective_mode,
                        content=entry.content,
                    )
                    postimage = root.write_regular(
                        relative_path,
                        entry.content,
                        effective_mode,
                        created=backup.created_directories,
                        before_leaf=lambda: self._inject("after_write_parent_open_before_leaf"),
                        on_prepared=record_pending_exact,
                        on_published=record_pending_exact,
                    )
                backup.owned_postimages[relative_path] = postimage
                backup.mutated_paths.add(relative_path)
                backup.pending_paths.remove(relative_path)
                backup.pending_beforeimages.pop(relative_path, None)
                backup.pending_postimages.pop(relative_path, None)
                backup.pending_exact_postimages.pop(relative_path, None)
            for directory in tuple(backup.created_directories):
                backup.created_directories[directory] = root.capture(directory)
        except WorkspacePreimageDrift:
            raise
        except AnchoredFilesystemError as exc:
            if not backup.mutation_started:
                raise WorkspacePreimageDrift("source path identity changed") from exc
            raise WorkspaceMaterializationError("anchored materialization failed") from exc
        self._inject("after_materialize")

    def verify_preimage(self, backup: SourceBackup) -> None:
        try:
            self._verify_preimage(self._open_root(backup), backup)
        except AnchoredFilesystemError as exc:
            raise WorkspacePreimageDrift("source path identity changed") from exc

    @staticmethod
    def _verify_preimage(root: AnchoredRoot, backup: SourceBackup) -> None:
        for relative_path, expected in backup.parents.items():
            current = (
                root.capture_directory(".") if relative_path == "." else root.capture(relative_path)
            )
            if not current.semantically_matches(expected):
                raise WorkspacePreimageDrift("source parent chain changed before materialization")
        for relative_path, expected in backup.entries.items():
            if not root.capture(relative_path).semantically_matches(expected):
                raise WorkspacePreimageDrift(
                    "source path changed after backup and before materialization"
                )

    def _open_root(self, backup: SourceBackup) -> AnchoredRoot:
        root = backup.filesystem
        if (root.device, root.inode) != (backup.root_device, backup.root_inode):
            raise WorkspaceMaterializationError("source root identity changed")
        return root

    @staticmethod
    def release(backup: SourceBackup | None) -> None:
        if backup is not None:
            backup.filesystem.close()

    def verify_materialized(
        self,
        backup: SourceBackup,
        plan: ApplyPlan,
    ) -> None:
        self._inject("before_verify")
        try:
            root = self._open_root(backup)
            for relative_path, expected in plan.desired.items():
                current = root.capture(relative_path)
                if expected.mode == "120000":
                    valid = current.kind == "symlink" and current.content == expected.content
                else:
                    expected_mode = self._desired_posix_mode(
                        backup,
                        plan,
                        relative_path,
                        expected,
                    )
                    valid = (
                        current.kind == "file"
                        and current.content == expected.content
                        and current.mode == expected_mode
                    )
                if not valid:
                    raise WorkspaceMaterializationError("materialized path identity mismatch")
            for relative_path in plan.removals - set(plan.desired):
                if root.capture(relative_path).kind != "missing":
                    raise WorkspaceMaterializationError(
                        "deleted path remains after materialization"
                    )
            root.assert_published_root()
        except AnchoredFilesystemError as exc:
            raise WorkspaceMaterializationError("materialized path identity changed") from exc

    def restore(self, backup: SourceBackup) -> None:
        try:
            with self._open_root(backup).borrowed() as root:
                for relative_path in tuple(backup.pending_paths):
                    current = root.capture(relative_path)
                    preimage = backup.entries[relative_path]
                    before = backup.pending_beforeimages[relative_path]
                    if current.semantically_matches(preimage):
                        backup.pending_paths.remove(relative_path)
                        backup.pending_beforeimages.pop(relative_path, None)
                        backup.pending_postimages.pop(relative_path, None)
                        backup.pending_exact_postimages.pop(relative_path, None)
                        continue
                    if current == before:
                        backup.owned_postimages[relative_path] = before
                        backup.mutated_paths.add(relative_path)
                        backup.pending_paths.remove(relative_path)
                        backup.pending_beforeimages.pop(relative_path, None)
                        backup.pending_postimages.pop(relative_path, None)
                        backup.pending_exact_postimages.pop(relative_path, None)
                        continue
                    exact_postimage = backup.pending_exact_postimages.get(relative_path)
                    if exact_postimage is None or not current.same_owned_object(exact_postimage):
                        raise WorkspaceMaterializationError(
                            "pending materialization outcome is indeterminate"
                        )
                    backup.owned_postimages[relative_path] = current
                    backup.mutated_paths.add(relative_path)
                    backup.pending_paths.remove(relative_path)
                    backup.pending_beforeimages.pop(relative_path, None)
                    backup.pending_postimages.pop(relative_path, None)
                    backup.pending_exact_postimages.pop(relative_path, None)
                for relative_path in backup.mutated_paths:
                    current = root.capture(relative_path)
                    preimage = backup.entries[relative_path]
                    postimage = backup.owned_postimages[relative_path]
                    if current.semantically_matches(preimage):
                        continue
                    if relative_path in backup.rollback_removed_paths:
                        if current.kind == "missing":
                            continue
                        raise WorkspaceMaterializationError("rollback journal identity changed")
                    if current != postimage:
                        raise WorkspaceMaterializationError(
                            "rollback postimage compare-and-swap failed"
                        )
                for relative_path, postimage in backup.created_directories.items():
                    if root.capture(relative_path) != postimage:
                        raise WorkspaceMaterializationError(
                            "rollback parent postimage compare-and-swap failed"
                        )
                self._inject("before_restore")
                for relative_path in sorted(
                    backup.mutated_paths,
                    key=lambda item: (
                        -len(PurePosixPath(item).parts),
                        os.fsencode(item),
                    ),
                ):
                    preimage = backup.entries[relative_path]
                    postimage = backup.owned_postimages[relative_path]
                    current = root.capture(relative_path)
                    if current.semantically_matches(preimage):
                        backup.rollback_removed_paths.discard(relative_path)
                        continue
                    if relative_path in backup.rollback_removed_paths:
                        if current.kind != "missing":
                            raise WorkspaceMaterializationError("rollback journal identity changed")
                    elif current != postimage:
                        raise WorkspaceMaterializationError(
                            "rollback postimage compare-and-swap failed"
                        )
                    else:
                        root.remove(
                            relative_path,
                            postimage,
                            exact=True,
                            before_leaf=lambda: self._inject("after_restore_parent_open"),
                        )
                        backup.rollback_removed_paths.add(relative_path)
                        self._inject("after_restore_remove")
                    if preimage.kind == "file":
                        assert preimage.content is not None and preimage.mode is not None
                        root.write_regular(
                            relative_path,
                            preimage.content,
                            preimage.mode,
                            before_leaf=lambda: self._inject("after_restore_parent_open"),
                        )
                    elif preimage.kind == "symlink":
                        assert preimage.content is not None
                        root.write_symlink(
                            relative_path,
                            preimage.content,
                            before_leaf=lambda: self._inject("after_restore_parent_open"),
                        )
                    elif preimage.kind == "directory":
                        assert preimage.mode is not None
                        root.write_directory(relative_path, preimage.mode)
                    backup.rollback_removed_paths.discard(relative_path)
                for relative_path, postimage in sorted(
                    backup.created_directories.items(),
                    key=lambda item: (
                        -len(PurePosixPath(item[0]).parts),
                        os.fsencode(item[0]),
                    ),
                ):
                    current = root.capture(relative_path)
                    if current.kind == "missing":
                        continue
                    if not current.semantically_matches(postimage):
                        raise WorkspaceMaterializationError(
                            "created directory rollback identity changed"
                        )
                    root.remove(relative_path, current, exact=True)
                self._verify_restored(root, backup)
        except AnchoredFilesystemError as exc:
            raise WorkspaceMaterializationError("anchored rollback failed") from exc
        self._inject("after_restore")

    def verify_restored(self, backup: SourceBackup) -> None:
        try:
            with self._open_root(backup).borrowed() as root:
                self._verify_restored(root, backup)
        except AnchoredFilesystemError as exc:
            raise WorkspaceMaterializationError("restored path identity mismatch") from exc

    @staticmethod
    def _verify_restored(root: AnchoredRoot, backup: SourceBackup) -> None:
        for relative_path, expected in backup.entries.items():
            if not root.capture(relative_path).semantically_matches(expected):
                raise WorkspaceMaterializationError("restored path identity mismatch")
        for relative_path, expected in backup.parents.items():
            current = (
                root.capture_directory(".") if relative_path == "." else root.capture(relative_path)
            )
            if not current.semantically_matches(expected):
                raise WorkspaceMaterializationError("restored parent identity mismatch")
        root.assert_published_root()

    @staticmethod
    def _desired_posix_mode(
        backup: SourceBackup,
        plan: ApplyPlan,
        relative_path: str,
        entry: GitWorktreeEntry,
    ) -> int | None:
        if entry.mode == "120000":
            return None
        change = next(change for change in plan.changes if change.new_path == relative_path)
        previous = backup.entries.get(change.old_path or "")
        if previous is None or previous.kind != "file" or previous.mode is None:
            return 0o755 if entry.mode == "100755" else 0o644
        if change.old_mode == change.new_mode:
            return previous.mode
        if entry.mode == "100755":
            return previous.mode | stat.S_IXUSR
        return previous.mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _read_expected(
        self,
        location: GitLocation,
        relative_path: str,
        *,
        oid: str | None,
        mode: str | None,
        size: int | None,
    ) -> GitWorktreeEntry:
        if oid is None or mode is None or size is None:
            raise WorkspaceMaterializationError("canonical path identity is incomplete")
        try:
            entry = self._git.read_worktree_entry(location, relative_path)
        except Exception as exc:
            raise WorkspaceMaterializationError("canonical path is unavailable") from exc
        if (entry.oid, entry.mode, entry.size) != (oid, mode, size):
            raise WorkspaceMaterializationError("canonical path identity drifted")
        return entry

    def _validate_target_availability(
        self,
        source_root: Path,
        change: CanonicalChange,
        removals: set[str],
    ) -> None:
        assert change.new_path is not None
        if change.kind in {"modify", "mode"}:
            return
        try:
            with AnchoredRoot(source_root, byte_limit=_MAX_TRANSACTION_BYTES) as root:
                target = root.capture(change.new_path)
            if target.kind == "missing" or change.new_path in removals:
                return
        except AnchoredFilesystemError as exc:
            raise WorkspaceMaterializationError("canonical target identity is unavailable") from exc
        raise WorkspaceMaterializationError("canonical target already exists")

    @staticmethod
    def _reject_target_prefix_conflicts(paths: tuple[str, ...]) -> None:
        ordered = sorted((PurePosixPath(path) for path in paths), key=lambda item: item.parts)
        previous: PurePosixPath | None = None
        for current in ordered:
            if previous is not None and previous == PurePosixPath(
                *current.parts[: len(previous.parts)]
            ):
                raise WorkspaceMaterializationError(
                    "canonical targets contain a path prefix conflict"
                )
            previous = current


__all__ = [
    "ApplyPlan",
    "SourceBackup",
    "WorkspaceApplyEngine",
    "WorkspaceMaterializationError",
    "WorkspacePreimageDrift",
]
