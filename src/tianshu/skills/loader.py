"""Skills Loader - SKILL.md discovery, parsing, and system prompt injection."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import stat
import tempfile
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, RLock
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

import frontmatter

from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.frozen_content import (
    FrozenContentViewsV1,
    FrozenSkillsViewV1,
    FrozenSkillV1,
    frozen_skill_digest,
    frozen_skills_view_digest,
)

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 256 * 1024  # 256KB
_MAX_CANDIDATES_PER_DIR = 300
_L1_MAX_ENTRIES = 8
_SKILL_RESOURCE_DIRS = ("scripts", "references", "assets", "templates")
_MAX_RESOURCE_BYTES = 1024 * 1024  # 1 MiB per resource file
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)
_FREEZE_MAX_ATTEMPTS = 3

_CURRENT_FROZEN_CONTENT_VIEWS: ContextVar[FrozenContentViewsV1 | None] = ContextVar(
    "tianshu_frozen_content_views",
    default=None,
)


class _RuntimeSkillMember(TypedDict):
    path: str
    kind: str
    content: str | None


class _SkillMetricsLike(Protocol):
    created_by: str
    status: str

    def is_dormant(self) -> bool: ...


class _SkillMetricsStoreLike(Protocol):
    def get(self, skill_name: str) -> _SkillMetricsLike | None: ...


@dataclass(frozen=True)
class _CapturedSkillSource:
    name: str
    source: str
    path: str
    raw: bytes
    load_all_eligible: bool


@dataclass(frozen=True)
class _CapturedPathStamp:
    logical_path: str
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class _ReadRegularFileCapture:
    raw: bytes
    identity: os.stat_result


@dataclass(frozen=True)
class _PinnedDirectoryPath:
    directory_fd: int | None
    stamps: tuple[_CapturedPathStamp, ...]


@dataclass(frozen=True)
class _RuntimeSkillOverlay:
    skill: dict | None
    load_all_eligible: bool
    hide_lower: bool = False


@dataclass(frozen=True)
class _CapturedSkillsSource:
    source_digest: str
    layers: tuple[tuple[_CapturedSkillSource, ...], ...]
    injected: tuple[tuple[str, str], ...]
    runtime: tuple[tuple[str, _RuntimeSkillOverlay], ...]
    path_stamps: tuple[_CapturedPathStamp, ...]
    injected_generation: int


def _skill_requirements_met(openclaw: dict) -> bool:
    requires = openclaw.get("requires", {})

    for binary in requires.get("bins", []):
        if not shutil.which(binary):
            return False

    any_bins = requires.get("anyBins", [])
    if any_bins and not any(shutil.which(binary) for binary in any_bins):
        return False

    for environment_name in requires.get("env", []):
        if environment_name not in os.environ:
            return False

    import sys

    allowed_os = openclaw.get("os", [])
    return not allowed_os or sys.platform in allowed_os


def _runtime_skill_overlays() -> dict[str, _RuntimeSkillOverlay]:
    from tianshu.evolution.runtime_context import current_evolution_runtime, runtime_subject_key
    from tianshu.models.canonical import canonical_sha256

    runtime = current_evolution_runtime()
    if runtime is None:
        return {}
    try:
        validate_subject_views = cast(Callable[[], object], runtime.validate_subject_views)
        validate_subject_views()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("runtime evolution provenance mismatch") from exc
    entries = tuple(
        (key, overlay, runtime.payloads[key]) for key, overlay in sorted(runtime.overlays.items())
    )
    if not entries and runtime.overlay is not None and runtime.selected_payload is not None:
        entries = (("compatibility", runtime.overlay, runtime.selected_payload),)

    assignments = {
        runtime_subject_key(assignment.kind, assignment.subject_key): assignment
        for assignment in getattr(runtime, "assignments", ())
    }

    skills: dict[str, _RuntimeSkillOverlay] = {}
    for key, overlay, package in entries:
        if canonical_sha256(package) != overlay.canonical_digest:
            raise RuntimeError("runtime skill overlay payload digest mismatch")
        if (
            overlay.kind is None
            or overlay.kind.value != "skill"
            or overlay.subject_key is None
            or not overlay.subject_key.startswith("skill:")
        ):
            continue
        name = overlay.subject_key.removeprefix("skill:")
        validate_skill_name(name)
        if name in skills:
            raise RuntimeError("multiple runtime overlays target the same skill")
        if package.get("state") == "absent":
            assignment = (
                getattr(runtime, "assignment", None)
                if key == "compatibility"
                else assignments.get(key)
            )
            selected_base = bool(
                assignment is not None and assignment.selected_ref == assignment.champion_ref
            )
            skills[name] = _RuntimeSkillOverlay(
                skill=None,
                load_all_eligible=False,
                hide_lower=not selected_base,
            )
            continue
        members = cast(list[_RuntimeSkillMember], package["members"])
        skill_member = next(
            (
                member
                for member in members
                if member.get("path") == "SKILL.md" and member.get("kind") == "file"
            ),
            None,
        )
        assert skill_member is not None
        raw_skill = cast(str, skill_member["content"])
        post = frontmatter.loads(raw_skill)
        metadata = post.metadata or {}
        openclaw = metadata.get("metadata", {}).get("openclaw", {})
        always = bool(openclaw.get("always", False))
        skills[name] = _RuntimeSkillOverlay(
            skill={
                "name": name,
                "description": metadata.get("description", ""),
                "source": "evolution-overlay",
                "always": always,
                "tool_tier": openclaw.get("toolTier"),
                "path": "",
                "content_length": len(post.content),
                "content": post.content,
            },
            load_all_eligible=len(raw_skill.encode("utf-8")) <= _MAX_FILE_SIZE
            and (always or _skill_requirements_met(openclaw)),
        )
    return skills


def current_frozen_content_views() -> FrozenContentViewsV1 | None:
    """Return the declarative content views bound to the current task."""

    return _CURRENT_FROZEN_CONTENT_VIEWS.get()


@contextmanager
def bind_frozen_content_views(views: FrozenContentViewsV1) -> Iterator[None]:
    """Bind immutable declarative content views for the current task."""

    token = _CURRENT_FROZEN_CONTENT_VIEWS.set(views)
    try:
        yield
    finally:
        _CURRENT_FROZEN_CONTENT_VIEWS.reset(token)


@contextmanager
def suspend_frozen_content_views() -> Iterator[None]:
    """Temporarily clear an outer run's frozen content views."""

    token = _CURRENT_FROZEN_CONTENT_VIEWS.set(None)
    try:
        yield
    finally:
        _CURRENT_FROZEN_CONTENT_VIEWS.reset(token)


def validate_skill_name(name: str) -> str:
    """Return a canonical ASCII skill identifier or raise ``ValueError``."""
    if not isinstance(name, str) or _SKILL_NAME_RE.fullmatch(name) is None:
        raise ValueError(
            f"invalid skill name {name!r}. Must match: lowercase alphanumeric, "
            "hyphens, dots, underscores; 1-64 chars; start with letter/digit."
        )
    return name


def _validated_filter_names(filter_names: list[str] | None) -> set[str] | None:
    if not filter_names:
        return None
    return {validate_skill_name(name) for name in filter_names}


def _is_canonical_discovered_skill_name(name: str) -> bool:
    try:
        validate_skill_name(name)
    except ValueError:
        logger.warning("Skipping skill entry with invalid identifier: %r", name)
        return False
    return True


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: tempfile in same dir + os.replace()."""
    dir_ = path.parent
    fd = -1
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp_path, str(path))
    except BaseException:
        if fd >= 0:
            os.close(fd)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _read_regular_file_at(
    directory_fd: int,
    relative_path: str,
) -> _ReadRegularFileCapture | None:
    """Read and version one open file relative to a pinned directory identity."""

    file_fd = os.open(
        relative_path,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            return None
        with os.fdopen(file_fd, "rb", closefd=False) as file:
            raw = file.read()
        after = os.fstat(file_fd)
        if _path_stamp("open-file", before) != _path_stamp("open-file", after):
            raise OSError("skill member changed while freezing content view")
        return _ReadRegularFileCapture(raw=raw, identity=after)
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _path_stamp(logical_path: str, identity: os.stat_result) -> _CapturedPathStamp:
    return _CapturedPathStamp(
        logical_path=logical_path,
        device=identity.st_dev,
        inode=identity.st_ino,
        mode=identity.st_mode,
        size=identity.st_size,
        mtime_ns=identity.st_mtime_ns,
        ctime_ns=identity.st_ctime_ns,
    )


def _path_identity_stamp(logical_path: str, identity: os.stat_result) -> _CapturedPathStamp:
    """Witness path-component identity without observing unrelated child churn."""

    return _CapturedPathStamp(
        logical_path=logical_path,
        device=identity.st_dev,
        inode=identity.st_ino,
        mode=identity.st_mode,
        size=0,
        mtime_ns=0,
        ctime_ns=0,
    )


def _open_pinned_directory_path(path: Path, *, logical_path: str) -> _PinnedDirectoryPath:
    """Open an absolute directory path component-by-component without symlinks."""

    absolute = Path(os.path.abspath(path))
    current_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    stamps: list[_CapturedPathStamp] = []
    try:
        current_path = Path("/")
        for part in absolute.parts[1:]:
            try:
                path_identity = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                os.close(current_fd)
                return _PinnedDirectoryPath(directory_fd=None, stamps=tuple(stamps))
            if stat.S_ISLNK(path_identity.st_mode):
                raise OSError("skill search path must not contain symbolic links")
            if not stat.S_ISDIR(path_identity.st_mode):
                os.close(current_fd)
                return _PinnedDirectoryPath(directory_fd=None, stamps=tuple(stamps))
            next_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            opened_identity = os.fstat(next_fd)
            current_path /= part
            component_logical_path = f"{logical_path}:{current_path}"
            path_stamp = _path_identity_stamp(component_logical_path, path_identity)
            opened_stamp = _path_identity_stamp(component_logical_path, opened_identity)
            if path_stamp != opened_stamp:
                os.close(next_fd)
                raise OSError("skill search path changed while freezing content view")
            stamps.append(opened_stamp)
            os.close(current_fd)
            current_fd = next_fd
        return _PinnedDirectoryPath(directory_fd=current_fd, stamps=tuple(stamps))
    except BaseException:
        os.close(current_fd)
        raise


def _read_stable_regular_file_at(
    directory_fd: int,
    relative_path: str,
    *,
    logical_path: str,
) -> tuple[bytes, _CapturedPathStamp] | None:
    """Read one member and reject replacement or in-place mutation during the read."""

    try:
        before = os.stat(relative_path, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(before.st_mode):
        raise OSError("skill members must not be symbolic links")
    if not stat.S_ISREG(before.st_mode):
        return None
    captured = _read_regular_file_at(directory_fd, relative_path)
    if captured is None:
        return None
    try:
        after = os.stat(relative_path, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise OSError("skill member changed while freezing content view") from exc
    before_stamp = _path_stamp(logical_path, before)
    opened_stamp = _path_stamp(logical_path, captured.identity)
    after_stamp = _path_stamp(logical_path, after)
    if before_stamp != opened_stamp or opened_stamp != after_stamp:
        raise OSError("skill member changed while freezing content view")
    return captured.raw, after_stamp


class SkillsLoader:
    def __init__(
        self,
        builtin_dir: Path,
        workspace_dir: Path | None = None,
        user_dir: Path | None = None,
        char_budget: int = 30000,
    ) -> None:
        self._builtin_dir = builtin_dir
        self._workspace_dir = workspace_dir
        self._user_dir = user_dir  # ~/.tianshu/skills
        self._char_budget = char_budget
        self._fallback_dirs: list[tuple[Path, str]] = []
        self._workspace_writes_only = False
        self._workspace_overlay_root: Path | None = None
        self._cache_lock = RLock()
        self._cache_generation = 0

        # L1: In-memory LRU cache for get_skill()
        self._l1_cache: OrderedDict[str, dict] = OrderedDict()
        # L2: File stat snapshot for list_all_metadata() — {path: (mtime_ns, size)}
        self._l2_stats: dict[str, tuple[int, int]] = {}
        self._l2_metadata: list[dict] | None = None
        self._content_digest_cache: str | None = None
        self._injected_skills: dict[str, str] = {}

    @property
    def user_dir(self) -> Path | None:
        return self._user_dir

    def repoint_user_dir(self, new_user_dir: Path) -> None:
        """切换 user 技能根目录（位面切换时调用）并失效所有缓存。"""
        self._user_dir = Path(new_user_dir).expanduser()
        self.invalidate_cache()

    def for_workspace_overlay(self, workspace_root: Path) -> SkillsLoader:
        """Return a per-call view whose mutations are confined to staging /skills."""
        lexical_root = Path(workspace_root).expanduser().absolute()
        if lexical_root.is_symlink() or not lexical_root.is_dir():
            raise ValueError("workspace overlay root must be a real directory")
        resolved_root = lexical_root.resolve()
        overlay = SkillsLoader(
            builtin_dir=self._builtin_dir,
            workspace_dir=resolved_root,
            user_dir=None,
            char_budget=self._char_budget,
        )
        overlay._fallback_dirs = self._search_dirs()
        overlay._workspace_writes_only = True
        overlay._workspace_overlay_root = resolved_root
        with self._cache_lock:
            overlay._injected_skills = dict(self._injected_skills)
        return overlay

    def set_char_budget(self, budget: int) -> None:
        self._char_budget = budget

    def invalidate_cache(self) -> None:
        """Clear all cache layers. Called by SkillsWatcher on file changes."""

        with self._cache_lock:
            self._invalidate_cache_locked()
        logger.debug("Skills cache invalidated")

    def _invalidate_cache_locked(self) -> None:
        self._l1_cache.clear()
        self._l2_stats.clear()
        self._l2_metadata = None
        self._content_digest_cache = None
        self._cache_generation += 1

    def content_digest(self) -> str:
        """Return the canonical digest of disk and PluginApi skill content."""

        frozen = self._frozen_view_for_reads()
        if frozen is not None:
            return frozen.source_digest

        with self._cache_lock:
            if self._content_digest_cache is not None:
                return self._content_digest_cache
            generation = self._cache_generation

        digest = self._calculate_content_digest()
        with self._cache_lock:
            if generation == self._cache_generation:
                self._content_digest_cache = digest
        return digest

    def _calculate_content_digest(self) -> str:
        """Read and hash current source bytes without consulting loader caches."""

        return self._capture_source_state().source_digest

    def _capture_source_state(self) -> _CapturedSkillsSource:
        """Return a whole-loader capture only when two consecutive passes agree."""

        first = self._capture_source_state_once()
        second = self._capture_source_state_once()
        if first != second:
            raise OSError("skills changed while freezing content view")
        return first

    def _capture_source_state_once(self) -> _CapturedSkillsSource:
        """Capture one pass with immutable member and directory mutation stamps."""

        members: dict[str, str] = {}
        layers: list[tuple[_CapturedSkillSource, ...]] = []
        path_stamps: list[_CapturedPathStamp] = []
        layer = 0
        for search_index, (base, source) in enumerate(self._search_dirs()):
            base_logical_path = f"search:{search_index}:{source}"
            pinned_base = _open_pinned_directory_path(
                base,
                logical_path=base_logical_path,
            )
            path_stamps.extend(pinned_base.stamps)
            base_fd = pinned_base.directory_fd
            if base_fd is None:
                continue
            try:
                base_before = _path_stamp(base_logical_path, os.fstat(base_fd))
                candidates = sorted(os.listdir(base_fd))[:_MAX_CANDIDATES_PER_DIR]
                captured_layer: list[_CapturedSkillSource] = []
                for name in candidates:
                    if not _is_canonical_discovered_skill_name(name):
                        continue
                    entry_identity = os.stat(
                        name,
                        dir_fd=base_fd,
                        follow_symlinks=False,
                    )
                    if stat.S_ISLNK(entry_identity.st_mode):
                        raise OSError("skill directories must not be symbolic links")
                    if not stat.S_ISDIR(entry_identity.st_mode):
                        continue
                    try:
                        directory_fd = os.open(
                            name,
                            os.O_RDONLY
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=base_fd,
                        )
                    except (FileNotFoundError, NotADirectoryError):
                        continue
                    try:
                        logical_root = f"layer:{layer}:{source}/{name}"
                        directory_before = _path_stamp(
                            logical_root,
                            os.fstat(directory_fd),
                        )
                        skill_member = _read_stable_regular_file_at(
                            directory_fd,
                            "SKILL.md",
                            logical_path=f"{logical_root}/SKILL.md",
                        )
                        if skill_member is not None:
                            raw, skill_stamp = skill_member
                            members[f"{logical_root}/SKILL.md"] = hashlib.sha256(raw).hexdigest()
                            path_stamps.append(skill_stamp)
                            path = str(base / name / "SKILL.md")
                            parsed = self._parse_skill_bytes(
                                name,
                                raw,
                                source=source,
                                path=path,
                            )
                            load_all_eligible = parsed is not None and parsed[1]
                            members[f"{logical_root}/load-all-eligible"] = hashlib.sha256(
                                b"true" if load_all_eligible else b"false"
                            ).hexdigest()
                            captured_layer.append(
                                _CapturedSkillSource(
                                    name=name,
                                    source=source,
                                    path=path,
                                    raw=raw,
                                    load_all_eligible=load_all_eligible,
                                )
                            )
                            for resource_name in _SKILL_RESOURCE_DIRS:
                                try:
                                    resource_identity = os.stat(
                                        resource_name,
                                        dir_fd=directory_fd,
                                        follow_symlinks=False,
                                    )
                                except FileNotFoundError:
                                    continue
                                if stat.S_ISLNK(resource_identity.st_mode):
                                    raise OSError(
                                        "skill resource directories must not be symbolic links"
                                    )
                                if not stat.S_ISDIR(resource_identity.st_mode):
                                    continue
                                try:
                                    resource_fd = os.open(
                                        resource_name,
                                        os.O_RDONLY
                                        | getattr(os, "O_DIRECTORY", 0)
                                        | getattr(os, "O_NOFOLLOW", 0),
                                        dir_fd=directory_fd,
                                    )
                                except (FileNotFoundError, NotADirectoryError):
                                    continue
                                try:
                                    resource_logical_path = f"{logical_root}/{resource_name}"
                                    resource_before = _path_stamp(
                                        resource_logical_path,
                                        os.fstat(resource_fd),
                                    )
                                    for root, directories, filenames, walk_fd in os.fwalk(
                                        ".",
                                        topdown=True,
                                        follow_symlinks=False,
                                        dir_fd=resource_fd,
                                    ):
                                        for directory_name in directories:
                                            nested_identity = os.stat(
                                                directory_name,
                                                dir_fd=walk_fd,
                                                follow_symlinks=False,
                                            )
                                            if stat.S_ISLNK(nested_identity.st_mode):
                                                raise OSError(
                                                    "skill resource directories must not contain "
                                                    "symbolic links"
                                                )
                                            if not stat.S_ISDIR(nested_identity.st_mode):
                                                raise OSError(
                                                    "skill resource directory member is not a "
                                                    "directory"
                                                )
                                        directories.sort()
                                        nested_root = root.removeprefix("./")
                                        walked_directory = "/".join(
                                            part
                                            for part in (resource_logical_path, nested_root)
                                            if part and part != "."
                                        )
                                        path_stamps.append(
                                            _path_stamp(walked_directory, os.fstat(walk_fd))
                                        )
                                        for filename in sorted(filenames):
                                            relative = "/".join(
                                                part
                                                for part in (
                                                    resource_name,
                                                    nested_root,
                                                    filename,
                                                )
                                                if part and part != "."
                                            )
                                            resource_member = _read_stable_regular_file_at(
                                                walk_fd,
                                                filename,
                                                logical_path=f"{logical_root}/{relative}",
                                            )
                                            if resource_member is None:
                                                continue
                                            resource_raw, resource_stamp = resource_member
                                            path_stamps.append(resource_stamp)
                                            members[f"{logical_root}/{relative}"] = hashlib.sha256(
                                                resource_raw
                                            ).hexdigest()
                                    resource_after = _path_stamp(
                                        resource_logical_path,
                                        os.fstat(resource_fd),
                                    )
                                    if resource_before != resource_after:
                                        raise OSError(
                                            "skill resource directory changed while freezing "
                                            "content view"
                                        )
                                finally:
                                    os.close(resource_fd)
                        directory_after = _path_stamp(
                            logical_root,
                            os.fstat(directory_fd),
                        )
                        try:
                            current_identity = os.stat(name, dir_fd=base_fd)
                        except FileNotFoundError as exc:
                            raise OSError(
                                "skill directory changed while freezing content view"
                            ) from exc
                        if (
                            directory_before != directory_after
                            or current_identity.st_dev != directory_after.device
                            or current_identity.st_ino != directory_after.inode
                        ):
                            raise OSError("skill directory changed while freezing content view")
                        path_stamps.append(directory_after)
                    finally:
                        os.close(directory_fd)
                base_after = _path_stamp(base_logical_path, os.fstat(base_fd))
                if base_before != base_after:
                    raise OSError("skills directory changed while freezing content view")
                path_stamps.append(base_after)
                if captured_layer:
                    layers.append(tuple(captured_layer))
                    layer += 1
            finally:
                os.close(base_fd)

        with self._cache_lock:
            injected = tuple(sorted(self._injected_skills.items()))
            injected_generation = self._cache_generation
        for name, content in injected:
            members[f"injected/{name}/SKILL.md"] = hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest()
        runtime = tuple(sorted(_runtime_skill_overlays().items()))
        for name, overlay in runtime:
            members[f"runtime/{name}/load-all-eligible"] = hashlib.sha256(
                (
                    f"eligible={int(overlay.load_all_eligible)};"
                    f"hide-lower={int(overlay.hide_lower)}"
                ).encode()
            ).hexdigest()

        return _CapturedSkillsSource(
            source_digest=canonical_sha256(members),
            layers=tuple(layers),
            injected=injected,
            runtime=runtime,
            path_stamps=tuple(path_stamps),
            injected_generation=injected_generation,
        )

    def _frozen_view_for_reads(self) -> FrozenSkillsViewV1 | None:
        views = current_frozen_content_views()
        return None if views is None else views.skills

    def freeze_view(self) -> FrozenSkillsViewV1:
        """Capture one stable, immutable view of effective skill content."""

        last_error: OSError | None = None
        for _attempt in range(_FREEZE_MAX_ATTEMPTS):
            try:
                captured = self._capture_source_state()
            except OSError as exc:
                last_error = exc
                continue
            return self._freeze_captured_source(captured)
        raise RuntimeError("skills changed while freezing content view") from last_error

    def _freeze_captured_source(
        self,
        captured: _CapturedSkillsSource,
    ) -> FrozenSkillsViewV1:
        effective: dict[str, dict | None] = {}
        load_all_skills: dict[str, str] = {}
        for layer in reversed(captured.layers):
            for source in layer:
                parsed = self._parse_skill_bytes(
                    source.name,
                    source.raw,
                    source=source.source,
                    path=source.path,
                )
                if parsed is None:
                    effective[source.name] = None
                    continue
                parsed_skill, _current_load_all_eligible = parsed
                effective[source.name] = parsed_skill
                if source.load_all_eligible:
                    load_all_skills[source.name] = cast(str, parsed_skill["content"])

        for name, content in captured.injected:
            effective[name] = self._injected_skill(name, content)
            load_all_skills[name] = content

        runtime_skills = dict(captured.runtime)
        for name, runtime_overlay in runtime_skills.items():
            runtime_skill = runtime_overlay.skill
            if runtime_skill is None:
                if runtime_overlay.hide_lower:
                    effective[name] = None
                    load_all_skills.pop(name, None)
                continue
            else:
                effective[name] = runtime_skill
                if runtime_overlay.load_all_eligible:
                    load_all_skills[name] = cast(str, runtime_skill["content"])

        staging_blocked_names = tuple(
            sorted(
                {name for name, _content in captured.injected}
                | {
                    name
                    for name, runtime_overlay in runtime_skills.items()
                    if runtime_overlay.skill is not None or runtime_overlay.hide_lower
                }
            )
        )

        frozen_skills: dict[str, FrozenSkillV1] = {}
        for name, effective_skill in effective.items():
            if effective_skill is None:
                continue
            skill_metadata = cast(
                dict[str, JsonValue],
                {key: value for key, value in effective_skill.items() if key != "content"},
            )
            content = cast(str, effective_skill["content"])
            frozen_skills[name] = FrozenSkillV1(
                digest=frozen_skill_digest(
                    content=content,
                    metadata=skill_metadata,
                ),
                content=content,
                metadata=skill_metadata,
            )
        load_all_entries = tuple(load_all_skills.items())
        return FrozenSkillsViewV1(
            source_digest=captured.source_digest,
            effective_digest=frozen_skills_view_digest(
                skills=frozen_skills,
                load_all_entries=load_all_entries,
                staging_blocked_names=staging_blocked_names,
            ),
            skills=frozen_skills,
            load_all_entries=load_all_entries,
            staging_blocked_names=staging_blocked_names,
        )

    def load_index(
        self,
        filter_names: list[str] | None = None,
        include_dormant: bool = False,
        metrics_store: object | None = None,
    ) -> str:
        """Return skill index (name + description only) for system prompt injection."""
        filter_set = _validated_filter_names(filter_names)
        metadata = self.list_all_metadata()
        typed_metrics_store = (
            cast(_SkillMetricsStoreLike, metrics_store) if metrics_store is not None else None
        )

        if filter_set is not None:
            metadata = [m for m in metadata if m["name"] in filter_set]

        # Filter dormant agent-created skills (unless explicitly requested)
        if not include_dormant and typed_metrics_store is not None:
            filtered = []
            for m in metadata:
                metrics = typed_metrics_store.get(m["name"])
                if metrics and metrics.is_dormant() and metrics.created_by == "agent":
                    continue
                filtered.append(m)
            metadata = filtered

        lines: list[str] = []
        for m in metadata:
            desc = m.get("description", "")
            status_marker = ""
            if typed_metrics_store is not None:
                metrics = typed_metrics_store.get(m["name"])
                if metrics and metrics.status == "warning":
                    status_marker = " [low success rate]"
                elif metrics and metrics.status == "retire_suggested":
                    status_marker = " [retire suggested]"
            lines.append(f"- {m['name']}: {desc}{status_marker}")

        if not lines:
            return ""

        header = (
            "# Available Skills\n"
            "Use skill_list() to see all skills with details. "
            "Use skill_view(name) to load full content.\n\n"
            "<skills_index>\n"
        )
        footer = (
            "\n</skills_index>\n\nIf a skill matches your current task, load it with skill_view()."
        )
        return header + "\n".join(lines) + footer

    def load_always(self, filter_names: list[str] | None = None) -> str:
        """Return full content of skills marked always=true."""
        filter_set = _validated_filter_names(filter_names)
        # Use list_all_metadata (single parse) to find always-on skill names
        metadata = self.list_all_metadata()
        always_names = {m["name"] for m in metadata if m.get("always", False)}

        if filter_set is not None:
            always_names &= filter_set

        if not always_names:
            return ""

        # Load full content only for always-on skills
        parts: list[str] = []
        total = 0
        for name in sorted(always_names):
            skill = self.get_skill(name)
            if not skill:
                continue
            entry = f"## Skill: {name}\n\n{skill['content']}"
            if total + len(entry) > self._char_budget:
                break
            parts.append(entry)
            total += len(entry)

        return "\n\n---\n\n".join(parts) if parts else ""

    def patch_skill(self, name: str, old: str, new: str) -> dict:
        """Find-and-replace within a skill's content using fuzzy matching."""
        validate_skill_name(name)
        from tianshu.skills.fuzzy_match import fuzzy_replace

        skill = self._get_live_skill(name, use_cache=True)
        if not skill:
            raise FileNotFoundError(f"Skill '{name}' not found")

        content = skill["content"]
        try:
            updated_content, strategy = fuzzy_replace(content, old, new)
        except ValueError:
            raise ValueError(f"Pattern not found in skill '{name}'") from None

        if strategy != "exact":
            logger.info(
                "patch_skill('%s'): matched via '%s' strategy",
                name,
                strategy,
            )
        return self.save_skill(name, updated_content)

    def register_skill(self, name: str, content: str) -> None:
        """Register an externally-provided skill (from PluginApi)."""
        validate_skill_name(name)
        with self._cache_lock:
            self._injected_skills[name] = content
            self._invalidate_cache_locked()

    def unregister_skill(self, name: str) -> bool:
        """Remove one PluginApi-injected skill and invalidate every cache layer."""

        validate_skill_name(name)
        with self._cache_lock:
            if name not in self._injected_skills:
                return False
            self._injected_skills.pop(name)
            self._invalidate_cache_locked()
            return True

    def load_all(self, filter_names: list[str] | None = None) -> str:
        filter_set = _validated_filter_names(filter_names)
        frozen = self._frozen_view_for_reads()
        if frozen is None:
            skills = self._load_all_live_skills()
        else:
            skills = dict(frozen.load_all_entries)
            if self._workspace_writes_only:
                for name, parsed in self._staged_skills().items():
                    if (
                        name not in frozen.staging_blocked_names
                        and parsed is not None
                        and parsed[1]
                    ):
                        skills[name] = cast(str, parsed[0]["content"])

        # Filter by allowed names if specified
        if filter_set is not None:
            skills = {k: v for k, v in skills.items() if k in filter_set}

        # Concatenate within char budget
        parts: list[str] = []
        total = 0
        for name, content in skills.items():
            if total + len(content) > self._char_budget:
                break
            parts.append(f"## Skill: {name}\n\n{content}")
            total += len(content)

        if not parts:
            return ""
        return "# Available Skills\n\n" + "\n\n---\n\n".join(parts)

    def _load_all_live_skills(self) -> dict[str, str]:
        """Read current load-all content without consulting frozen views."""

        skills: dict[str, str] = {}

        # Search dirs are highest-first; compose in reverse so higher layers replace
        # content without changing the canonical first-seen order.
        for base, _source in reversed(self._search_dirs()):
            self._scan_dir(base, skills)

        with self._cache_lock:
            injected = tuple(sorted(self._injected_skills.items()))
        skills.update(injected)

        for runtime_name, runtime_overlay in _runtime_skill_overlays().items():
            runtime_skill = runtime_overlay.skill
            if runtime_skill is None:
                if runtime_overlay.hide_lower:
                    skills.pop(runtime_name, None)
                continue
            elif runtime_overlay.load_all_eligible:
                skills[runtime_name] = runtime_skill["content"]

        return skills

    def _scan_dir(self, base: Path, skills: dict[str, str]) -> None:
        if not base.is_dir():
            return
        candidates = sorted(base.iterdir())[:_MAX_CANDIDATES_PER_DIR]
        for entry in candidates:
            if not _is_canonical_discovered_skill_name(entry.name):
                continue
            skill_file = entry / "SKILL.md" if entry.is_dir() else None
            if skill_file and skill_file.is_file():
                self._load_skill(entry.name, skill_file, skills)

    def _load_skill(self, name: str, path: Path, skills: dict[str, str]) -> None:
        if not _is_canonical_discovered_skill_name(name):
            return
        if path.stat().st_size > _MAX_FILE_SIZE:
            logger.warning("Skill '%s' exceeds max file size, skipping", name)
            return

        try:
            post = frontmatter.load(str(path))
        except Exception:
            logger.warning("Failed to parse skill '%s'", name, exc_info=True)
            return

        meta = post.metadata or {}
        openclaw = meta.get("metadata", {}).get("openclaw", {})

        # always=true skips requirement checks
        if not openclaw.get("always", False) and not self._check_requirements(openclaw):
            logger.debug("Skill '%s' failed requirements check", name)
            return

        skills[name] = post.content

    @staticmethod
    def _injected_skill(name: str, content: str) -> dict:
        return {
            "name": name,
            "description": "",
            "source": "injected",
            "always": False,
            "tool_tier": None,
            "path": "",
            "content_length": len(content),
            "content": content,
        }

    def _parse_skill_bytes(
        self,
        name: str,
        raw: bytes,
        *,
        source: str,
        path: str,
    ) -> tuple[dict, bool] | None:
        try:
            post = frontmatter.loads(raw.decode("utf-8"))
            meta = post.metadata or {}
            openclaw = meta.get("metadata", {}).get("openclaw", {})
            skill = {
                "name": name,
                "description": meta.get("description", ""),
                "source": source,
                "always": openclaw.get("always", False),
                "tool_tier": openclaw.get("toolTier"),
                "path": path,
                "content_length": len(post.content),
                "content": post.content,
            }
            load_all_eligible = len(raw) <= _MAX_FILE_SIZE and (
                bool(openclaw.get("always", False)) or self._check_requirements(openclaw)
            )
            return skill, load_all_eligible
        except Exception:
            logger.warning("Failed to load skill '%s'", name)
            return None

    def _staged_skill(
        self,
        name: str,
    ) -> tuple[bool, tuple[dict, bool] | None]:
        if not self._workspace_writes_only or self._workspace_dir is None:
            return False, None
        skill_file = self._workspace_dir / "skills" / name / "SKILL.md"
        if not skill_file.is_file():
            return False, None
        return True, self._parse_skill_bytes(
            name,
            skill_file.read_bytes(),
            source="workspace",
            path=str(skill_file),
        )

    def _staged_skills(self) -> dict[str, tuple[dict, bool] | None]:
        if not self._workspace_writes_only or self._workspace_dir is None:
            return {}
        skills_dir = self._workspace_dir / "skills"
        if not skills_dir.is_dir():
            return {}
        staged: dict[str, tuple[dict, bool] | None] = {}
        for entry in sorted(skills_dir.iterdir())[:_MAX_CANDIDATES_PER_DIR]:
            if not entry.is_dir() or not _is_canonical_discovered_skill_name(entry.name):
                continue
            skill_file = entry / "SKILL.md"
            if skill_file.is_file():
                staged[entry.name] = self._parse_skill_bytes(
                    entry.name,
                    skill_file.read_bytes(),
                    source="workspace",
                    path=str(skill_file),
                )
        return staged

    @staticmethod
    def _check_requirements(openclaw: dict) -> bool:
        return _skill_requirements_met(openclaw)

    def list_all_metadata(self) -> list[dict]:
        """Return structured metadata for all skills (builtin + workspace + injected)."""
        frozen = self._frozen_view_for_reads()
        if frozen is not None:
            metadata = [dict(skill.metadata) for skill in frozen.skills.values()]
            if self._workspace_writes_only:
                metadata = self._with_staged_metadata(
                    metadata,
                    blocked_names=frozenset(frozen.staging_blocked_names),
                )
            return metadata
        return self._list_all_metadata_live(use_cache=True)

    def _with_staged_metadata(
        self,
        metadata: list[dict],
        *,
        blocked_names: frozenset[str],
    ) -> list[dict]:
        staged = {
            name: parsed
            for name, parsed in self._staged_skills().items()
            if name not in blocked_names
        }
        visible: list[dict] = []
        handled: set[str] = set()
        for item in metadata:
            name = cast(str, item["name"])
            if name not in staged:
                visible.append(item)
                continue
            if name in handled:
                continue
            handled.add(name)
            parsed = staged[name]
            if parsed is not None:
                visible.append({key: value for key, value in parsed[0].items() if key != "content"})
        for name, parsed in staged.items():
            if name not in handled and parsed is not None:
                visible.append({key: value for key, value in parsed[0].items() if key != "content"})
        return visible

    def _list_all_metadata_live(self, *, use_cache: bool) -> list[dict]:
        """Read current metadata without consulting frozen content views."""

        if self._workspace_writes_only:
            return self._with_runtime_metadata(self._list_overlay_metadata())
        # L2: Check if file stats match cached snapshot
        with self._cache_lock:
            generation = self._cache_generation
            if use_cache and self._l2_metadata is not None and self._l2_stats_valid():
                return self._with_runtime_metadata(self._l2_metadata)

        # L3: Full disk scan
        result: list[dict] = []
        new_stats: dict[str, tuple[int, int]] = {}
        # Builtin
        self._collect_metadata(self._builtin_dir, "builtin", result, new_stats)
        # User (~/.tianshu/skills)
        if self._user_dir and self._user_dir.is_dir():
            self._collect_metadata(self._user_dir, "user", result, new_stats)
        # Workspace
        if self._workspace_dir:
            ws_skills = self._workspace_dir / "skills"
            if ws_skills.is_dir():
                self._collect_metadata(ws_skills, "workspace", result, new_stats)
        # Injected
        with self._cache_lock:
            injected = tuple(sorted(self._injected_skills.items()))
        for name, content in injected:
            result.append(
                {
                    "name": name,
                    "description": "",
                    "source": "injected",
                    "always": False,
                    "tool_tier": None,
                    "path": "",
                    "content_length": len(content),
                }
            )

        result = self._deduplicate_metadata(result)
        if use_cache:
            with self._cache_lock:
                if generation == self._cache_generation:
                    self._l2_metadata = result
                    self._l2_stats = new_stats
        return self._with_runtime_metadata(result)

    @staticmethod
    def _with_runtime_metadata(metadata: list[dict]) -> list[dict]:
        runtime_overlays = _runtime_skill_overlays()
        visible = {cast(str, item["name"]): item for item in metadata}
        for name, overlay in runtime_overlays.items():
            skill = overlay.skill
            if skill is not None:
                visible[name] = {key: value for key, value in skill.items() if key != "content"}
            elif overlay.hide_lower:
                visible.pop(name, None)
        return list(visible.values())

    @staticmethod
    def _deduplicate_metadata(metadata: list[dict]) -> list[dict]:
        visible: dict[str, dict] = {}
        for item in metadata:
            visible[cast(str, item["name"])] = item
        return [item for item in visible.values() if not item.get("_invalid", False)]

    def _list_overlay_metadata(self) -> list[dict]:
        result: list[dict] = []
        stats: dict[str, tuple[int, int]] = {}
        for base, source in reversed(self._search_dirs()):
            self._collect_metadata(base, source, result, stats)
        with self._cache_lock:
            injected = tuple(sorted(self._injected_skills.items()))
        for name, content in injected:
            result.append(
                {
                    "name": name,
                    "description": "",
                    "source": "injected",
                    "always": False,
                    "tool_tier": None,
                    "path": "",
                    "content_length": len(content),
                }
            )
        result = self._deduplicate_metadata(result)
        with self._cache_lock:
            self._l2_metadata = result
            self._l2_stats = stats
        return result

    def _l2_stats_valid(self) -> bool:
        """Check if all cached file stats still match disk."""
        for path_str, (cached_mtime, cached_size) in self._l2_stats.items():
            try:
                st = os.stat(path_str)
                if st.st_mtime_ns != cached_mtime or st.st_size != cached_size:
                    return False
            except OSError:
                return False
        return True

    def _collect_metadata(
        self,
        base: Path,
        source: str,
        out: list[dict],
        stats: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        if not base.is_dir():
            return
        candidates = sorted(base.iterdir())[:_MAX_CANDIDATES_PER_DIR]
        for entry in candidates:
            if not _is_canonical_discovered_skill_name(entry.name):
                continue
            skill_file = entry / "SKILL.md" if entry.is_dir() else None
            if skill_file and skill_file.is_file():
                try:
                    # Record file stats for L2 cache validation
                    if stats is not None:
                        st = skill_file.stat()
                        stats[str(skill_file)] = (st.st_mtime_ns, st.st_size)

                    post = frontmatter.load(str(skill_file))
                    meta = post.metadata or {}
                    oc = meta.get("metadata", {}).get("openclaw", {})
                    out.append(
                        {
                            "name": entry.name,
                            "description": meta.get("description", ""),
                            "source": source,
                            "always": oc.get("always", False),
                            "tool_tier": oc.get("toolTier"),
                            "path": str(skill_file),
                            "content_length": len(post.content),
                        }
                    )
                except Exception:
                    logger.warning("Failed to read metadata for skill '%s'", entry.name)
                    out.append({"name": entry.name, "_invalid": True})

    def get_skill(self, name: str) -> dict | None:
        """Return full content + metadata for a single skill."""
        validate_skill_name(name)
        frozen = self._frozen_view_for_reads()
        if frozen is not None:
            if name not in frozen.staging_blocked_names:
                staged, parsed = self._staged_skill(name)
                if staged:
                    return None if parsed is None else parsed[0]
            skill = frozen.skills.get(name)
            if skill is None:
                return None
            return {**dict(skill.metadata), "content": skill.content}
        return self._get_live_skill(name, use_cache=True)

    def _get_live_skill(self, name: str, *, use_cache: bool) -> dict | None:
        """Read one current effective skill without consulting frozen views."""

        validate_skill_name(name)
        runtime_overlays = _runtime_skill_overlays()
        if name in runtime_overlays:
            runtime_overlay = runtime_overlays[name]
            if runtime_overlay.skill is not None:
                return runtime_overlay.skill
            if runtime_overlay.hide_lower:
                return None
        with self._cache_lock:
            # Check injected first
            if name in self._injected_skills:
                return self._injected_skill(name, self._injected_skills[name])

            # L1 cache check
            if use_cache and name in self._l1_cache:
                self._l1_cache.move_to_end(name)
                return self._l1_cache[name]
            generation = self._cache_generation

        # L3: Full disk read
        for base, source in self._search_dirs():
            skill_file = base / name / "SKILL.md"
            if skill_file.is_file():
                parsed = self._parse_skill_bytes(
                    name,
                    skill_file.read_bytes(),
                    source=source,
                    path=str(skill_file),
                )
                if parsed is None:
                    return None
                result = parsed[0]
                if use_cache:
                    with self._cache_lock:
                        if generation == self._cache_generation:
                            self._l1_cache[name] = result
                            if len(self._l1_cache) > _L1_MAX_ENTRIES:
                                self._l1_cache.popitem(last=False)
                return result
        return None

    def _search_dirs(self) -> list[tuple[Path, str]]:
        """Return search dirs in priority order (highest first)."""
        dirs: list[tuple[Path, str]] = []
        if self._workspace_dir:
            ws_skills = self._workspace_dir / "skills"
            if ws_skills.is_dir():
                dirs.append((ws_skills, "workspace"))
        if self._user_dir and self._user_dir.is_dir():
            dirs.append((self._user_dir, "user"))
        dirs.extend(self._fallback_dirs)
        dirs.append((self._builtin_dir, "builtin"))
        deduplicated: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for path, source in dirs:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                deduplicated.append((path, source))
        return deduplicated

    def _materialize_writable_skill(self, name: str) -> Path:
        target_dir = self._validate_overlay_write_path(self._writable_skills_dir() / name)
        target_file = self._validate_overlay_write_path(target_dir / "SKILL.md")
        if target_file.is_file():
            return target_file
        for base, _source in self._search_dirs():
            source_dir = base / name
            if (source_dir / "SKILL.md").is_file():
                target_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_dir, target_dir)
                return target_file
        raise FileNotFoundError(f"Skill '{name}' not found")

    def save_skill(self, name: str, content: str) -> dict:
        """Write back skill content to its SKILL.md file. SkillsWatcher auto-reloads."""
        validate_skill_name(name)
        if self._workspace_writes_only:
            skill_file = self._materialize_writable_skill(name)
            try:
                post = frontmatter.load(str(skill_file))
                post.content = content
                _atomic_write(skill_file, frontmatter.dumps(post))
            except Exception:
                _atomic_write(skill_file, content)
            self.invalidate_cache()
            return self._get_live_skill(name, use_cache=True)  # type: ignore[return-value]
        for base, source in self._search_dirs():
            skill_file = base / name / "SKILL.md"
            if skill_file.is_file():
                if source == "builtin":
                    # Builtin skills are immutable packaged defaults: edits are
                    # copy-on-write materialized into the writable overlay.
                    skill_file = self._materialize_writable_skill(name)
                # Preserve frontmatter, replace content
                try:
                    post = frontmatter.load(str(skill_file))
                    post.content = content
                    _atomic_write(skill_file, frontmatter.dumps(post))
                except Exception:
                    # Fallback: write raw content
                    _atomic_write(skill_file, content)
                self.invalidate_cache()
                return self._get_live_skill(name, use_cache=True)  # type: ignore[return-value]
        raise FileNotFoundError(f"Skill '{name}' not found")

    def _writable_skills_dir(self) -> Path:
        """Return the best writable skills directory (workspace > user)."""
        if self._workspace_dir:
            return self._validate_overlay_write_path(self._workspace_dir / "skills")
        if self._user_dir:
            return self._user_dir
        raise ValueError("No writable skills directory configured")

    def create_skill(self, name: str, content: str) -> dict:
        """Create a new skill in writable skills directory (workspace or user)."""
        validate_skill_name(name)
        target = self._writable_skills_dir()
        target.mkdir(parents=True, exist_ok=True)
        skill_dir = self._validate_overlay_write_path(target / name)
        if skill_dir.exists():
            raise ValueError(f"Skill '{name}' already exists")
        skill_dir.mkdir()
        skill_file = self._validate_overlay_write_path(skill_dir / "SKILL.md")
        _atomic_write(skill_file, content)
        self.invalidate_cache()
        return self._get_live_skill(name, use_cache=True)  # type: ignore[return-value]

    def _resolve_skill_resource(self, name: str, rel_path: str) -> Path:
        """Resolve a resource path INSIDE an existing skill dir, safely.

        Rejects absolute paths, traversal, and non-whitelisted top dirs.
        Returns the absolute target path (parent may not exist yet).
        """
        if not rel_path or rel_path.startswith("/") or "\\" in rel_path:
            raise ValueError(f"invalid resource path: {rel_path!r}")
        parts = Path(rel_path).parts
        if ".." in parts:
            raise ValueError(f"path traversal not allowed: {rel_path!r}")
        if parts[0] not in _SKILL_RESOURCE_DIRS:
            raise ValueError(f"top dir must be one of {_SKILL_RESOURCE_DIRS}, got {parts[0]!r}")
        if len(parts) < 2:
            raise ValueError(
                f"resource path must include a filename under the top dir: {rel_path!r}"
            )
        search_dirs = self._search_dirs()
        if self._workspace_writes_only:
            self._materialize_writable_skill(name)
            search_dirs = [(self._writable_skills_dir(), "workspace")]
        for base, src in search_dirs:
            skill_dir = self._validate_overlay_write_path(base / name)
            if (skill_dir / "SKILL.md").is_file():
                if src == "builtin":
                    # Resource mutations on a builtin skill operate on a
                    # copy-on-write overlay copy; packaged bytes stay immutable.
                    self._materialize_writable_skill(name)
                    skill_dir = self._validate_overlay_write_path(
                        self._writable_skills_dir() / name
                    )
                target = self._validate_overlay_write_path(skill_dir / rel_path).resolve()
                if not str(target).startswith(str(skill_dir.resolve()) + "/"):
                    raise ValueError(f"resolved path escapes skill dir: {rel_path!r}")
                return target
        raise FileNotFoundError(f"Skill '{name}' not found")

    def write_skill_file(self, name: str, rel_path: str, content: str) -> dict:
        """Write a resource file inside a skill dir. Invalidates caches."""
        validate_skill_name(name)
        if len(content.encode("utf-8")) > _MAX_RESOURCE_BYTES:
            raise ValueError(f"resource exceeds {_MAX_RESOURCE_BYTES} bytes")
        target = self._resolve_skill_resource(name, rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, content)
        self.invalidate_cache()
        return {"name": name, "file": rel_path, "bytes": len(content.encode("utf-8"))}

    def remove_skill_file(self, name: str, rel_path: str) -> bool:
        """Remove a resource file inside a skill dir. Invalidates caches."""
        validate_skill_name(name)
        target = self._resolve_skill_resource(name, rel_path)
        if target.is_file():
            target.unlink()
            self.invalidate_cache()
            return True
        return False

    def delete_skill(self, name: str) -> bool:
        """Delete a user/workspace skill. Builtin skills cannot be deleted."""
        validate_skill_name(name)
        for base in self._writable_dirs():
            skill_dir = self._validate_overlay_write_path(base / name)
            if skill_dir.is_dir():
                import shutil as _shutil

                self._validate_overlay_write_path(skill_dir)
                _shutil.rmtree(skill_dir)
                self.invalidate_cache()
                return True
        return False

    def _writable_dirs(self) -> list[Path]:
        dirs: list[Path] = []
        if self._workspace_dir:
            dirs.append(self._validate_overlay_write_path(self._workspace_dir / "skills"))
        if self._user_dir and not self._workspace_writes_only:
            dirs.append(self._user_dir)
        return dirs

    def _validate_overlay_write_path(self, path: Path) -> Path:
        root = self._workspace_overlay_root
        if root is None:
            return path
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise ValueError("workspace overlay root identity changed")
        candidate = Path(path).absolute()
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("workspace overlay write path escapes staging root") from exc
        current = root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("workspace overlay write path must not contain symlinks")
        if not candidate.resolve().is_relative_to(root):
            raise ValueError("workspace overlay write path escapes staging root")
        return candidate

    def archive_skill(self, name: str) -> bool:
        """Move a user/workspace skill into a sibling ``.archive/`` dir (recoverable).

        Builtin skills are never touched (not in writable dirs). The ``.archive``
        dir holds no top-level SKILL.md, so archived skills are invisible to the
        scanner. Returns True if archived.
        """
        validate_skill_name(name)
        for base in self._writable_dirs():
            skill_dir = self._validate_overlay_write_path(base / name)
            if skill_dir.is_dir():
                archive_root = self._validate_overlay_write_path(base / ".archive")
                archive_root.mkdir(parents=True, exist_ok=True)
                archive_root = self._validate_overlay_write_path(archive_root)
                target = self._validate_overlay_write_path(archive_root / name)
                if target.exists():
                    from datetime import UTC, datetime

                    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                    target = self._validate_overlay_write_path(archive_root / f"{name}__{stamp}")
                self._validate_overlay_write_path(skill_dir)
                self._validate_overlay_write_path(target)
                shutil.move(str(skill_dir), str(target))
                self.invalidate_cache()
                logger.info("Archived skill '%s' → %s", name, target)
                return True
        return False

    def restore_skill(self, name: str) -> bool:
        """Move a skill back out of ``.archive/`` into its writable dir."""
        validate_skill_name(name)
        for base in self._writable_dirs():
            archive_root = self._validate_overlay_write_path(base / ".archive")
            src = self._validate_overlay_write_path(archive_root / name)
            if src.is_dir():
                target = self._validate_overlay_write_path(base / name)
                if target.exists():
                    return False
                self._validate_overlay_write_path(src)
                self._validate_overlay_write_path(target)
                shutil.move(str(src), str(target))
                self.invalidate_cache()
                logger.info("Restored skill '%s' from archive", name)
                return True
        return False


class SkillsWatcher:
    """Watch skills directories for changes and trigger reload with debounce."""

    DEBOUNCE_SECONDS = 1.0

    def __init__(
        self,
        loader: SkillsLoader,
        on_change: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._loader = loader
        self._on_change = on_change
        self._observer: BaseObserver | None = None
        self._debounce_task: asyncio.Future[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._generation = 0
        self._pending_paths: set[str] = set()
        self._pending_paths_lock = Lock()

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers.polling import PollingObserver

        if self._running:
            return

        # Promotion replaces complete skill trees atomically.  The native macOS
        # FSEvents observer can abort the process while such a stream is starting or
        # stopping, including in the default (non-frozen) mode.  A polling observer
        # gives every mode the same reliable invalidation semantics on all platforms.
        self._generation += 1
        generation = self._generation
        self._running = True

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                paths = (
                    getattr(event, "src_path", ""),
                    getattr(event, "dest_path", ""),
                )
                relevant_paths = [
                    path
                    for path in paths
                    if path
                    and (
                        Path(path).name == "SKILL.md"
                        or any(part in _SKILL_RESOURCE_DIRS for part in Path(path).parts)
                    )
                ]
                if relevant_paths:
                    watcher._schedule_reload(relevant_paths, generation=generation)

        observer: BaseObserver | None = None
        try:
            observer = PollingObserver()
            handler = _Handler()

            builtin = self._loader._builtin_dir
            if builtin.is_dir():
                observer.schedule(handler, str(builtin), recursive=True)

            user_dir = self._loader._user_dir
            if user_dir and user_dir.is_dir():
                observer.schedule(handler, str(user_dir), recursive=True)

            workspace = self._loader._workspace_dir
            if workspace:
                skills_dir = workspace / "skills"
                if skills_dir.is_dir():
                    observer.schedule(handler, str(skills_dir), recursive=True)

            self._observer = observer
            self._loop = asyncio.get_event_loop()
            observer.start()
        except Exception:
            self._running = False
            self._generation += 1
            self._loop = None
            self._observer = None
            with self._pending_paths_lock:
                self._pending_paths.clear()
            debounce_task = self._debounce_task
            if debounce_task is not None and not debounce_task.done():
                debounce_task.cancel()
            self._debounce_task = None
            if observer is not None:
                try:
                    observer.stop()
                except Exception:
                    logger.debug("SkillsWatcher observer stop failed during cleanup", exc_info=True)
                try:
                    observer.join()
                except Exception:
                    logger.debug("SkillsWatcher observer join failed during cleanup", exc_info=True)
            raise
        logger.info("SkillsWatcher started")

    def stop(self) -> None:
        self._running = False
        self._generation += 1
        self._loop = None
        with self._pending_paths_lock:
            self._pending_paths.clear()
        debounce_task = self._debounce_task
        if debounce_task is not None and not debounce_task.done():
            debounce_task.cancel()
        self._debounce_task = None
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        logger.info("SkillsWatcher stopped")

    def _schedule_reload(
        self,
        paths: list[str] | None = None,
        *,
        generation: int | None = None,
    ) -> None:
        active_generation = self._generation
        if not self._running or (generation is not None and generation != active_generation):
            return
        self._add_pending_paths(paths or [])
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._debounced_reload, None, active_generation)

    def _add_pending_paths(self, paths: list[str]) -> None:
        with self._pending_paths_lock:
            self._pending_paths.update(paths)

    def _debounced_reload(
        self,
        paths: list[str] | None = None,
        generation: int | None = None,
    ) -> None:
        active_generation = self._generation
        if not self._running or (generation is not None and generation != active_generation):
            return
        self._add_pending_paths(paths or [])
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        async def _do_reload() -> None:
            await asyncio.sleep(self.DEBOUNCE_SECONDS)
            if not self._running or self._generation != active_generation:
                return
            with self._pending_paths_lock:
                changed_paths = sorted(self._pending_paths)
                self._pending_paths.clear()
            if not self._running or self._generation != active_generation:
                return
            self._loader.invalidate_cache()
            if self._on_change is None:
                self._loader.load_all()
            else:
                try:
                    self._on_change(changed_paths)
                except Exception:
                    logger.exception("Skills change callback failed")
            logger.info("Skills cache invalidated after file change")

        self._debounce_task = asyncio.ensure_future(_do_reload())
