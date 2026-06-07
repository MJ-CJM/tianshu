"""Skill management tools — list, view, and manage skills."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTENT_SIZE = 256 * 1024  # 256KB

# Module-level shared set for tracking which skills are active in current execution
_active_skills: set[str] = set()


def get_active_skills() -> set[str]:
    """Return the set of skills viewed during current execution."""
    return _active_skills


def clear_active_skills() -> None:
    """Clear the active skills set (call at end of agent execution)."""
    _active_skills.clear()


class MetricsStore(Protocol):
    """Protocol for skill metrics storage (implemented in Task 8)."""

    def ensure_exists(self, name: str) -> None: ...
    def increment_usage(self, name: str) -> None: ...
    def get(self, name: str) -> Any: ...
    def delete(self, name: str) -> None: ...


async def _skill_list(
    skills: SkillsLoader,
    query: str | None = None,
    include_dormant: bool = False,
    metrics_store: MetricsStore | None = None,
) -> ToolResult:
    """List all available skills with name, description, source, and status."""
    metadata = skills.list_all_metadata()
    if query:
        q = query.lower()
        metadata = [m for m in metadata if q in m["name"].lower()]

    result = []
    for m in metadata:
        entry: dict = {
            "name": m["name"],
            "description": m.get("description", ""),
            "source": m.get("source", "unknown"),
            "status": "healthy",
        }
        if metrics_store:
            metrics = metrics_store.get(m["name"])
            if metrics:
                entry["status"] = metrics.status
                entry["usage_count"] = metrics.usage_count
                entry["success_rate"] = metrics.success_rate
                if not include_dormant and metrics.is_dormant() and metrics.created_by == "agent":
                    continue
        result.append(entry)

    return ok_result(json.dumps(result, ensure_ascii=False, indent=2))


async def _skill_view(
    skills: SkillsLoader,
    name: str,
    metrics_store: MetricsStore | None = None,
    active_skills_ref: set[str] | None = None,
) -> ToolResult:
    """View the full content of a skill."""
    skill = skills.get_skill(name)
    if not skill:
        return error_result(f"Skill '{name}' not found")

    # Track usage metrics (wired in Task 9, C3)
    if metrics_store:
        metrics_store.ensure_exists(name)
        metrics_store.increment_usage(name)

    # Track active skills for success/failure attribution (wired in Task 9, C3)
    if active_skills_ref is not None:
        active_skills_ref.add(name)

    # Build metrics info for response
    metrics_info: dict = {}
    if metrics_store:
        m = metrics_store.get(name)
        if m:
            metrics_info = {
                "usage_count": m.usage_count,
                "success_rate": m.success_rate,
                "status": m.status,
            }

    return ok_result(json.dumps({
        "name": skill["name"],
        "description": skill.get("description", ""),
        "source": skill.get("source", ""),
        "content": skill.get("content", ""),
        "metrics": metrics_info,
    }, ensure_ascii=False, indent=2))


# --- skill_manage action handlers ---


async def _handle_create(
    skills: SkillsLoader, name: str, metrics_store: MetricsStore | None = None, **kwargs: Any,
) -> ToolResult:
    content = kwargs.get("content")
    if not content:
        return error_result("'content' is required for create action")
    if len(content) > _MAX_CONTENT_SIZE:
        return error_result(f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit")
    try:
        result = skills.create_skill(name, content)
        if metrics_store:
            metrics_store.ensure_exists(name)
        return ok_result(json.dumps({"status": "created", "skill": result}, ensure_ascii=False))
    except ValueError as e:
        return error_result(str(e))


async def _handle_edit(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    content = kwargs.get("content")
    if not content:
        return error_result("'content' is required for edit action")
    if len(content) > _MAX_CONTENT_SIZE:
        return error_result(f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit")
    try:
        result = skills.save_skill(name, content)
        return ok_result(json.dumps({"status": "updated", "skill": result}, ensure_ascii=False))
    except FileNotFoundError as e:
        return error_result(str(e))


async def _handle_patch(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    patch_old = kwargs.get("patch_old")
    patch_new = kwargs.get("patch_new")
    if not patch_old or not patch_new:
        return error_result("'patch_old' and 'patch_new' are required for patch action")
    try:
        result = skills.patch_skill(name, patch_old, patch_new)
        return ok_result(json.dumps({"status": "patched", "skill": result}, ensure_ascii=False))
    except (FileNotFoundError, ValueError) as e:
        return error_result(str(e))


async def _handle_delete(
    skills: SkillsLoader, name: str, metrics_store: MetricsStore | None = None, **kwargs: Any,
) -> ToolResult:
    deleted = skills.delete_skill(name)
    if deleted:
        if metrics_store:
            metrics_store.delete(name)
        return ok_result(json.dumps({"status": "deleted", "name": name}))
    return error_result(f"Skill '{name}' not found or is a builtin (cannot delete)")


async def _handle_activate(
    skills: SkillsLoader, name: str, metrics_store: MetricsStore | None = None, **kwargs: Any,
) -> ToolResult:
    if metrics_store:
        metrics_store.ensure_exists(name)
        metrics_store.increment_usage(name)
        return ok_result(json.dumps({"status": "activated", "name": name}))
    return error_result("Metrics store not available, cannot activate")


async def _handle_write_file(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    file_path = kwargs.get("file_path")
    file_content = kwargs.get("file_content")
    if not file_path or file_content is None:
        return error_result("'file_path' and 'file_content' are required for write_file")
    if kwargs.get("_guard_enabled") and file_content is not None:
        from tianshu.skills.guard import SkillsGuard, TrustLevel
        guard = SkillsGuard()
        gres = guard.scan_content(file_content, TrustLevel.AGENT_CREATED)
        if not SkillsGuard.should_allow(gres, TrustLevel.AGENT_CREATED):
            findings = "; ".join(f.message for f in gres.findings)
            return error_result(f"guard blocked resource: {findings}")
    try:
        result = skills.write_skill_file(name, file_path, file_content)
        return ok_result(json.dumps({"status": "file_written", **result}, ensure_ascii=False))
    except (FileNotFoundError, ValueError, OSError) as e:
        return error_result(str(e))


async def _handle_remove_file(skills: SkillsLoader, name: str, **kwargs: Any) -> ToolResult:
    file_path = kwargs.get("file_path")
    if not file_path:
        return error_result("'file_path' is required for remove_file")
    try:
        removed = skills.remove_skill_file(name, file_path)
        if removed:
            return ok_result(json.dumps({"status": "file_removed", "file": file_path}))
        return error_result(f"File '{file_path}' not found in skill '{name}'")
    except (FileNotFoundError, ValueError, OSError) as e:
        return error_result(str(e))


_ACTION_HANDLERS = {
    "create": _handle_create,
    "edit": _handle_edit,
    "patch": _handle_patch,
    "delete": _handle_delete,
    "activate": _handle_activate,
    "write_file": _handle_write_file,
    "remove_file": _handle_remove_file,
}


async def _skill_manage(
    skills: SkillsLoader,
    action: str,
    name: str,
    metrics_store: MetricsStore | None = None,
    **kwargs: Any,
) -> ToolResult:
    """Create, edit, patch, delete, or activate a skill."""
    handler = _ACTION_HANDLERS.get(action)
    if not handler:
        return error_result(f"Invalid action: {action}. Must be create/edit/patch/delete/activate/write_file/remove_file")

    if not _NAME_RE.match(name):
        return error_result(
            f"Invalid skill name '{name}'. Must match: lowercase alphanumeric, "
            "hyphens, dots, underscores; 1-64 chars; start with letter/digit."
        )

    if action in ("create", "delete", "activate"):
        return await handler(skills, name, metrics_store=metrics_store, **kwargs)
    return await handler(skills, name, **kwargs)


def register_skill_tools(
    registry: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: MetricsStore | None = None,
    guard_agent_created: bool = True,
    event_bus: Any | None = None,
) -> None:
    """Register skill_list, skill_view, and skill_manage tools."""

    registry.register(
        "skill_list",
        lambda **kwargs: _skill_list(skills, metrics_store=metrics_store, **kwargs),
        ToolDefinition(
            name="skill_list",
            description=(
                "List all available skills with name, description, and source. "
                "Use this to discover skills before loading them with skill_view."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Filter skills by name substring (optional)",
                    },
                    "include_dormant": {
                        "type": "boolean",
                        "description": "Include dormant skills (default false)",
                        "default": False,
                    },
                },
                "required": [],
            },
            tier=ToolTier.T0_READONLY.value,
        ),
    )

    registry.register(
        "skill_view",
        lambda **kwargs: _skill_view(
            skills,
            metrics_store=metrics_store,
            active_skills_ref=_active_skills,
            **kwargs,
        ),
        ToolDefinition(
            name="skill_view",
            description=(
                "View the full content of a skill by name. "
                "Load a skill before executing a task if it matches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill name to view",
                    },
                },
                "required": ["name"],
            },
            tier=ToolTier.T0_READONLY.value,
        ),
    )

    registry.register(
        "skill_manage",
        lambda **kwargs: _skill_manage(
            skills,
            metrics_store=metrics_store,
            _guard_enabled=guard_agent_created,
            event_bus=event_bus,
            **kwargs,
        ),
        ToolDefinition(
            name="skill_manage",
            description=(
                "Create, edit, patch, delete a skill, or write/remove a bundled "
                "resource file (scripts/references/assets/templates). "
                "Use after figuring out a reusable approach to save it for reuse."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "edit", "patch", "delete", "activate", "write_file", "remove_file"],
                        "description": "The action to perform",
                    },
                    "name": {
                        "type": "string",
                        "description": "Skill name (lowercase alphanumeric, hyphens, dots, underscores)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full SKILL.md content (required for create/edit)",
                    },
                    "patch_old": {
                        "type": "string",
                        "description": "Text to find (required for patch)",
                    },
                    "patch_new": {
                        "type": "string",
                        "description": "Replacement text (required for patch)",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Resource path inside the skill dir "
                                       "(top dir: scripts/references/assets/templates). "
                                       "Required for write_file/remove_file.",
                    },
                    "file_content": {
                        "type": "string",
                        "description": "Resource file content (required for write_file).",
                    },
                },
                "required": ["action", "name"],
            },
            tier=ToolTier.T1_WORKSPACE.value,
            side_effect=True,
        ),
    )
