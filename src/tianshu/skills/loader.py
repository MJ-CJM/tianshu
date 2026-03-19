"""Skills Loader - SKILL.md discovery, parsing, and system prompt injection."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import frontmatter

logger = logging.getLogger(__name__)

_MAX_FILE_SIZE = 256 * 1024  # 256KB
_MAX_CANDIDATES_PER_DIR = 300


class SkillsLoader:
    def __init__(
        self,
        builtin_dir: Path,
        workspace_dir: Path | None = None,
        char_budget: int = 30000,
    ) -> None:
        self._builtin_dir = builtin_dir
        self._workspace_dir = workspace_dir
        self._char_budget = char_budget

    def set_char_budget(self, budget: int) -> None:
        self._char_budget = budget

    def register_skill(self, name: str, content: str) -> None:
        """Register an externally-provided skill (from PluginApi)."""
        if not hasattr(self, "_injected_skills"):
            self._injected_skills: dict[str, str] = {}
        self._injected_skills[name] = content

    def load_all(self) -> str:
        skills: dict[str, str] = {}  # name -> content

        # builtin (low priority)
        self._scan_dir(self._builtin_dir, skills)

        # workspace (high priority, overrides same-name)
        if self._workspace_dir:
            workspace_skills = self._workspace_dir / "skills"
            if workspace_skills.is_dir():
                self._scan_dir(workspace_skills, skills)

        # injected skills (highest priority)
        if hasattr(self, "_injected_skills"):
            skills.update(self._injected_skills)

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
            skill_file = entry / "SKILL.md" if entry.is_dir() else None
            if skill_file and skill_file.is_file():
                self._load_skill(entry.name, skill_file, skills)

    def _load_skill(self, name: str, path: Path, skills: dict[str, str]) -> None:
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
        if not openclaw.get("always", False):
            if not self._check_requirements(openclaw):
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
                if event.src_path.endswith("SKILL.md"):
                    watcher._schedule_reload()

        self._observer = Observer()
        handler = _Handler()

        builtin = self._loader._builtin_dir
        if builtin.is_dir():
            self._observer.schedule(handler, str(builtin), recursive=True)

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
        import asyncio

        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._debounced_reload)

    def _debounced_reload(self) -> None:
        import asyncio

        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()

        async def _do_reload() -> None:
            await asyncio.sleep(self.DEBOUNCE_SECONDS)
            self._loader.load_all()
            logger.info("Skills reloaded after file change")

        self._debounce_task = asyncio.ensure_future(_do_reload())
