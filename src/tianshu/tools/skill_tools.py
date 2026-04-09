"""Skill management tools — list, view, and manage skills."""

from __future__ import annotations

import json
import logging
import re

from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, error_result, ok_result

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTENT_SIZE = 256 * 1024  # 256KB


async def _skill_list(
    skills: SkillsLoader,
    category: str | None = None,
    include_dormant: bool = False,
) -> ToolResult:
    """List all available skills with name, description, and source."""
    metadata = skills.list_all_metadata()
    if category:
        metadata = [m for m in metadata if category.lower() in m["name"].lower()]
    result = [
        {
            "name": m["name"],
            "description": m.get("description", ""),
            "source": m.get("source", "unknown"),
        }
        for m in metadata
    ]
    return ok_result(json.dumps(result, ensure_ascii=False, indent=2))


async def _skill_view(
    skills: SkillsLoader,
    name: str,
    metrics_store: object | None = None,
    active_skills_ref: set | None = None,
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


async def _skill_manage(
    skills: SkillsLoader,
    action: str,
    name: str,
    content: str | None = None,
    patch_old: str | None = None,
    patch_new: str | None = None,
    metrics_store: object | None = None,
) -> ToolResult:
    """Create, edit, patch, delete, or activate a skill."""
    if action not in ("create", "edit", "patch", "delete", "activate"):
        return error_result(f"Invalid action: {action}. Must be create/edit/patch/delete/activate")

    if not _NAME_RE.match(name):
        return error_result(
            f"Invalid skill name '{name}'. Must match: lowercase alphanumeric, "
            "hyphens, dots, underscores; 1-64 chars; start with letter/digit."
        )

    if action == "create":
        if not content:
            return error_result("'content' is required for create action")
        if len(content) > _MAX_CONTENT_SIZE:
            return error_result(f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit")
        try:
            result = skills.create_skill(name, content)
            return ok_result(json.dumps({"status": "created", "skill": result}, ensure_ascii=False))
        except ValueError as e:
            return error_result(str(e))

    elif action == "edit":
        if not content:
            return error_result("'content' is required for edit action")
        if len(content) > _MAX_CONTENT_SIZE:
            return error_result(f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit")
        try:
            result = skills.save_skill(name, content)
            return ok_result(json.dumps({"status": "updated", "skill": result}, ensure_ascii=False))
        except FileNotFoundError as e:
            return error_result(str(e))

    elif action == "patch":
        if not patch_old or not patch_new:
            return error_result("'patch_old' and 'patch_new' are required for patch action")
        try:
            result = skills.patch_skill(name, patch_old, patch_new)
            return ok_result(json.dumps({"status": "patched", "skill": result}, ensure_ascii=False))
        except (FileNotFoundError, ValueError) as e:
            return error_result(str(e))

    elif action == "delete":
        deleted = skills.delete_skill(name)
        if deleted:
            if metrics_store:
                metrics_store.delete(name)
            return ok_result(json.dumps({"status": "deleted", "name": name}))
        return error_result(f"Skill '{name}' not found or is a builtin (cannot delete)")

    elif action == "activate":
        # Reactivate a dormant skill by touching its last_used_at
        if metrics_store:
            metrics_store.ensure_exists(name)
            metrics_store.increment_usage(name)
            return ok_result(json.dumps({"status": "activated", "name": name}))
        return error_result("Metrics store not available, cannot activate")

    return error_result(f"Unknown action: {action}")


def register_skill_tools(
    registry: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: object | None = None,
    active_skills_ref: set | None = None,
) -> None:
    """Register skill_list, skill_view, and skill_manage tools."""

    registry.register(
        "skill_list",
        lambda **kwargs: _skill_list(skills, **kwargs),
        ToolDefinition(
            name="skill_list",
            description=(
                "List all available skills with name, description, and source. "
                "Use this to discover skills before loading them with skill_view."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Filter skills by category keyword (optional)",
                    },
                    "include_dormant": {
                        "type": "boolean",
                        "description": "Include dormant skills (default false)",
                        "default": False,
                    },
                },
                "required": [],
            },
            tier=0,
        ),
    )

    registry.register(
        "skill_view",
        lambda **kwargs: _skill_view(
            skills,
            metrics_store=metrics_store,
            active_skills_ref=active_skills_ref,
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
            tier=0,
        ),
    )

    registry.register(
        "skill_manage",
        lambda **kwargs: _skill_manage(
            skills,
            metrics_store=metrics_store,
            **kwargs,
        ),
        ToolDefinition(
            name="skill_manage",
            description=(
                "Create, edit, patch, or delete a skill. "
                "Use after completing a difficult task to save reusable approaches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "edit", "patch", "delete", "activate"],
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
                },
                "required": ["action", "name"],
            },
            tier=2,
        ),
    )
