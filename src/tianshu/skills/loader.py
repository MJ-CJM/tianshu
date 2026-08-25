"""Skills Loader - SKILL.md discovery, parsing, and system prompt injection."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import TypedDict, cast

import frontmatter

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 256 * 1024  # 256KB
_MAX_CANDIDATES_PER_DIR = 300
_L1_MAX_ENTRIES = 8
_SKILL_RESOURCE_DIRS = ("scripts", "references", "assets", "templates")
_MAX_RESOURCE_BYTES = 1024 * 1024  # 1 MiB per resource file
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)


class _RuntimeSkillMember(TypedDict):
    path: str
    kind: str
    content: str | None


def _runtime_skill_overlay() -> tuple[str, dict | None] | None:
    from tianshu.evolution.runtime_context import current_evolution_runtime

    runtime = current_evolution_runtime()
    if (
        runtime is None
        or runtime.overlay.kind is None
        or runtime.overlay.kind.value != "skill"
        or runtime.overlay.subject_key is None
    ):
        return None
    name = runtime.overlay.subject_key.removeprefix("skill:")
    package = runtime.selected_payload
    if package.get("state") == "absent":
        return name, None
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
    post = frontmatter.loads(cast(str, skill_member["content"]))
    metadata = post.metadata or {}
    openclaw = metadata.get("metadata", {}).get("openclaw", {})
    return name, {
        "name": name,
        "description": metadata.get("description", ""),
        "source": "evolution-overlay",
        "always": openclaw.get("always", False),
        "tool_tier": openclaw.get("toolTier"),
        "path": "",
        "content_length": len(post.content),
        "content": post.content,
    }


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

        # L1: In-memory LRU cache for get_skill()
        self._l1_cache: OrderedDict[str, dict] = OrderedDict()
        # L2: File stat snapshot for list_all_metadata() — {path: (mtime_ns, size)}
        self._l2_stats: dict[str, tuple[int, int]] = {}
        self._l2_metadata: list[dict] | None = None
        self._content_digest_cache: str | None = None

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
        if hasattr(self, "_injected_skills"):
            overlay._injected_skills = dict(self._injected_skills)
        return overlay

    def set_char_budget(self, budget: int) -> None:
        self._char_budget = budget

    def invalidate_cache(self) -> None:
        """Clear all cache layers. Called by SkillsWatcher on file changes."""
        self._l1_cache.clear()
        self._l2_stats.clear()
        self._l2_metadata = None
        self._content_digest_cache = None
        logger.debug("Skills cache invalidated")

    def content_digest(self) -> str:
        """Return the canonical digest of disk and PluginApi skill content."""

        if self._content_digest_cache is not None:
            return self._content_digest_cache

        from tianshu.models.canonical import canonical_sha256

        members: dict[str, str] = {}
        layer = 0
        for base, source in self._search_dirs():
            if not base.is_dir():
                continue
            candidates = sorted(base.iterdir())[:_MAX_CANDIDATES_PER_DIR]
            skill_entries = [
                entry
                for entry in candidates
                if entry.is_dir()
                and _is_canonical_discovered_skill_name(entry.name)
                and (entry / "SKILL.md").is_file()
            ]
            if not skill_entries:
                continue
            for entry in skill_entries:
                skill_file = entry / "SKILL.md"
                logical_root = f"layer:{layer}:{source}/{entry.name}"
                members[f"{logical_root}/SKILL.md"] = hashlib.sha256(
                    skill_file.read_bytes()
                ).hexdigest()
                for resource_name in _SKILL_RESOURCE_DIRS:
                    resource_dir = entry / resource_name
                    if not resource_dir.is_dir():
                        continue
                    resource_files = sorted(
                        (path for path in resource_dir.rglob("*") if path.is_file()),
                        key=lambda path: path.relative_to(entry).as_posix(),
                    )
                    for resource_file in resource_files:
                        relative = resource_file.relative_to(entry).as_posix()
                        members[f"{logical_root}/{relative}"] = hashlib.sha256(
                            resource_file.read_bytes()
                        ).hexdigest()
            layer += 1

        if hasattr(self, "_injected_skills"):
            for name, content in sorted(self._injected_skills.items()):
                members[f"injected/{name}/SKILL.md"] = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()

        self._content_digest_cache = canonical_sha256(members)
        return self._content_digest_cache

    def load_index(
        self,
        filter_names: list[str] | None = None,
        include_dormant: bool = False,
        metrics_store: object | None = None,
    ) -> str:
        """Return skill index (name + description only) for system prompt injection."""
        filter_set = _validated_filter_names(filter_names)
        metadata = self.list_all_metadata()

        if filter_set is not None:
            metadata = [m for m in metadata if m["name"] in filter_set]

        # Filter dormant agent-created skills (unless explicitly requested)
        if not include_dormant and metrics_store is not None:
            filtered = []
            for m in metadata:
                metrics = metrics_store.get(m["name"])
                if metrics and metrics.is_dormant() and metrics.created_by == "agent":
                    continue
                filtered.append(m)
            metadata = filtered

        lines: list[str] = []
        for m in metadata:
            desc = m.get("description", "")
            status_marker = ""
            if metrics_store is not None:
                metrics = metrics_store.get(m["name"])
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
        for name in always_names:
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

        skill = self.get_skill(name)
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
        if not hasattr(self, "_injected_skills"):
            self._injected_skills: dict[str, str] = {}
        self._injected_skills[name] = content
        self._content_digest_cache = None

    def load_all(self, filter_names: list[str] | None = None) -> str:
        filter_set = _validated_filter_names(filter_names)
        skills: dict[str, str] = {}  # name -> content

        # builtin (lowest priority)
        self._scan_dir(self._builtin_dir, skills)

        # user dir (medium priority, ~/.tianshu/skills)
        if self._user_dir and self._user_dir.is_dir():
            self._scan_dir(self._user_dir, skills)

        # workspace (high priority, overrides same-name)
        if self._workspace_dir:
            workspace_skills = self._workspace_dir / "skills"
            if workspace_skills.is_dir():
                self._scan_dir(workspace_skills, skills)

        # injected skills (highest priority)
        if hasattr(self, "_injected_skills"):
            skills.update(self._injected_skills)

        runtime_overlay = _runtime_skill_overlay()
        if runtime_overlay is not None:
            runtime_name, runtime_skill = runtime_overlay
            if runtime_skill is None:
                skills.pop(runtime_name, None)
            else:
                skills[runtime_name] = runtime_skill["content"]

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
    def _check_requirements(openclaw: dict) -> bool:
        requires = openclaw.get("requires", {})

        # bins: all must exist
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False

        # anyBins: at least one must exist
        any_bins = requires.get("anyBins", [])
        if any_bins and not any(shutil.which(b) for b in any_bins):
            return False

        # env: all must be set
        for e in requires.get("env", []):
            if e not in os.environ:
                return False

        # os check
        import sys

        allowed_os = openclaw.get("os", [])
        if allowed_os and sys.platform not in allowed_os:
            return False

        return True

    def list_all_metadata(self) -> list[dict]:
        """Return structured metadata for all skills (builtin + workspace + injected)."""
        if self._workspace_writes_only:
            return self._with_runtime_metadata(self._list_overlay_metadata())
        # L2: Check if file stats match cached snapshot
        if self._l2_metadata is not None and self._l2_stats_valid():
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
        if hasattr(self, "_injected_skills"):
            for name, content in self._injected_skills.items():
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

        # Populate L2
        self._l2_metadata = result
        self._l2_stats = new_stats
        return self._with_runtime_metadata(result)

    @staticmethod
    def _with_runtime_metadata(metadata: list[dict]) -> list[dict]:
        runtime_overlay = _runtime_skill_overlay()
        if runtime_overlay is None:
            return list(metadata)
        name, skill = runtime_overlay
        visible = [item for item in metadata if item["name"] != name]
        if skill is not None:
            visible.append({key: value for key, value in skill.items() if key != "content"})
        return visible

    def _list_overlay_metadata(self) -> list[dict]:
        result: list[dict] = []
        stats: dict[str, tuple[int, int]] = {}
        seen: set[str] = set()
        if hasattr(self, "_injected_skills"):
            for name, content in self._injected_skills.items():
                seen.add(name)
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
        for base, source in self._search_dirs():
            candidates: list[dict] = []
            self._collect_metadata(base, source, candidates, stats)
            for metadata in candidates:
                name = metadata["name"]
                if name not in seen:
                    seen.add(name)
                    result.append(metadata)
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

    def get_skill(self, name: str) -> dict | None:
        """Return full content + metadata for a single skill."""
        validate_skill_name(name)
        runtime_overlay = _runtime_skill_overlay()
        if runtime_overlay is not None and runtime_overlay[0] == name:
            return runtime_overlay[1]
        # Check injected first
        if hasattr(self, "_injected_skills") and name in self._injected_skills:
            return {
                "name": name,
                "description": "",
                "source": "injected",
                "always": False,
                "tool_tier": None,
                "path": "",
                "content_length": len(self._injected_skills[name]),
                "content": self._injected_skills[name],
            }

        # L1 cache check
        if name in self._l1_cache:
            self._l1_cache.move_to_end(name)
            return self._l1_cache[name]

        # L3: Full disk read
        for base, source in self._search_dirs():
            skill_file = base / name / "SKILL.md"
            if skill_file.is_file():
                try:
                    post = frontmatter.load(str(skill_file))
                    meta = post.metadata or {}
                    oc = meta.get("metadata", {}).get("openclaw", {})
                    result = {
                        "name": name,
                        "description": meta.get("description", ""),
                        "source": source,
                        "always": oc.get("always", False),
                        "tool_tier": oc.get("toolTier"),
                        "path": str(skill_file),
                        "content_length": len(post.content),
                        "content": post.content,
                    }
                    # Populate L1
                    self._l1_cache[name] = result
                    if len(self._l1_cache) > _L1_MAX_ENTRIES:
                        self._l1_cache.popitem(last=False)
                    return result
                except Exception:
                    logger.warning("Failed to load skill '%s'", name)
                    return None
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
            self._l1_cache.pop(name, None)
            self._l2_metadata = None
            self._content_digest_cache = None
            return self.get_skill(name)  # type: ignore[return-value]
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
                # Invalidate caches for this skill
                self._l1_cache.pop(name, None)
                self._l2_metadata = None
                self._content_digest_cache = None
                return self.get_skill(name)  # type: ignore[return-value]
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
        self._l2_metadata = None  # Invalidate metadata cache
        self._content_digest_cache = None
        return self.get_skill(name)  # type: ignore[return-value]

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
        self._l1_cache.pop(name, None)
        self._l2_metadata = None
        self._content_digest_cache = None
        return {"name": name, "file": rel_path, "bytes": len(content.encode("utf-8"))}

    def remove_skill_file(self, name: str, rel_path: str) -> bool:
        """Remove a resource file inside a skill dir. Invalidates caches."""
        validate_skill_name(name)
        target = self._resolve_skill_resource(name, rel_path)
        if target.is_file():
            target.unlink()
            self._l1_cache.pop(name, None)
            self._l2_metadata = None
            self._content_digest_cache = None
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
                self._l1_cache.pop(name, None)
                self._l2_metadata = None
                self._content_digest_cache = None
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
                self._l1_cache.pop(name, None)
                self._l2_metadata = None
                self._content_digest_cache = None
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
                self._l2_metadata = None
                self._content_digest_cache = None
                logger.info("Restored skill '%s' from archive", name)
                return True
        return False


class SkillsWatcher:
    """Watch skills directories for changes and trigger reload with debounce."""

    DEBOUNCE_SECONDS = 1.0

    def __init__(self, loader: SkillsLoader) -> None:
        self._loader = loader
        self._observer: object | None = None
        self._debounce_task: object | None = None
        self._loop: object | None = None

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                paths = (
                    getattr(event, "src_path", ""),
                    getattr(event, "dest_path", ""),
                )
                if any(
                    path
                    and (
                        Path(path).name == "SKILL.md"
                        or any(part in _SKILL_RESOURCE_DIRS for part in Path(path).parts)
                    )
                    for path in paths
                ):
                    watcher._schedule_reload()

        self._observer = Observer()
        handler = _Handler()

        builtin = self._loader._builtin_dir
        if builtin.is_dir():
            self._observer.schedule(handler, str(builtin), recursive=True)

        user_dir = self._loader._user_dir
        if user_dir and user_dir.is_dir():
            self._observer.schedule(handler, str(user_dir), recursive=True)

        workspace = self._loader._workspace_dir
        if workspace:
            skills_dir = workspace / "skills"
            if skills_dir.is_dir():
                self._observer.schedule(handler, str(skills_dir), recursive=True)

        import asyncio

        self._loop = asyncio.get_event_loop()
        self._observer.start()
        logger.info("SkillsWatcher started")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        logger.info("SkillsWatcher stopped")

    def _schedule_reload(self) -> None:

        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._debounced_reload)

    def _debounced_reload(self) -> None:
        import asyncio

        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        async def _do_reload() -> None:
            await asyncio.sleep(self.DEBOUNCE_SECONDS)
            self._loader.invalidate_cache()
            self._loader.load_all()
            logger.info("Skills reloaded after file change")

        self._debounce_task = asyncio.ensure_future(_do_reload())
