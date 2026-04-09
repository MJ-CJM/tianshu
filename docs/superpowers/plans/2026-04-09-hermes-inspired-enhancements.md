# Hermes-Inspired Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade tianshu's skill system to be self-improving (progressive loading, agent-writable, auto-review, quality tracking), add fallback model, streaming, and cross-session memory search.

**Architecture:** Six phases (C1→C2→C3 sequential, F/S/M independent). C1 adds progressive skill loading + `skill_manage` tool. C2 adds hook-driven learning loop. C3 adds quality metrics. F adds model fallback. S adds streaming + cancellation. M adds memory search tool.

**Tech Stack:** Python 3.12+, asyncio, SQLite, LiteLLM, FastAPI, Pydantic v2

**User Preference:** Features first, tests last (tests consolidated in final task).

---

## File Map

| Phase | Create | Modify |
|-------|--------|--------|
| C1 | `src/tianshu/tools/skill_tools.py` | `src/tianshu/skills/loader.py`, `src/tianshu/executor/agent.py`, `src/tianshu/persona/prompt_builder.py`, `src/tianshu/app.py` |
| C2 | `src/tianshu/skills/reviewer.py`, `src/tianshu/skills/validator.py` | `src/tianshu/config_manager.py`, `src/tianshu/models/api.py`, `src/tianshu/app.py` |
| C3 | `src/tianshu/skills/metrics.py` | `src/tianshu/storage.py`, `src/tianshu/skills/loader.py`, `src/tianshu/tools/skill_tools.py`, `src/tianshu/executor/agent.py` |
| F | — | `src/tianshu/config_manager.py`, `src/tianshu/executor/agent.py`, `src/tianshu/models/api.py` |
| S | `src/tianshu/executor/streaming.py` | `src/tianshu/executor/agent.py`, `src/tianshu/llm.py`, `src/tianshu/notifier/notifier.py`, `src/tianshu/app.py` |
| M | `src/tianshu/tools/memory_tools.py` | `src/tianshu/app.py` |

---

## Task 1: SkillsLoader — `load_index()` + `patch_skill()`

**Files:**
- Modify: `src/tianshu/skills/loader.py:40-76` (add `load_index`, `load_always`, `patch_skill`)

- [ ] **Step 1: Add `load_index()` method**

Add after `set_char_budget()` (line 32) in `SkillsLoader`:

```python
def load_index(
    self,
    filter_names: list[str] | None = None,
    include_dormant: bool = False,
) -> str:
    """Return skill index (name + description only) for system prompt injection."""
    metadata = self.list_all_metadata()

    if filter_names:
        filter_set = set(filter_names)
        metadata = [m for m in metadata if m["name"] in filter_set]

    lines: list[str] = []
    for m in metadata:
        desc = m.get("description", "")
        lines.append(f"- {m['name']}: {desc}")

    if not lines:
        return ""

    header = (
        "# Available Skills\n"
        "Use skill_list() to see all skills with details. "
        "Use skill_view(name) to load full content.\n\n"
        "<skills_index>\n"
    )
    footer = (
        "\n</skills_index>\n\n"
        "If a skill matches your current task, load it with skill_view().\n"
        "After completing a difficult task, consider saving reusable approaches "
        "as a new skill with skill_manage()."
    )
    return header + "\n".join(lines) + footer
```

- [ ] **Step 2: Add `load_always()` method**

Add after `load_index()`:

```python
def load_always(self, filter_names: list[str] | None = None) -> str:
    """Return full content of skills marked always=true."""
    skills: dict[str, str] = {}
    self._scan_dir(self._builtin_dir, skills)
    if self._user_dir and self._user_dir.is_dir():
        self._scan_dir(self._user_dir, skills)
    if self._workspace_dir:
        ws_skills = self._workspace_dir / "skills"
        if ws_skills.is_dir():
            self._scan_dir(ws_skills, skills)
    if hasattr(self, "_injected_skills"):
        skills.update(self._injected_skills)

    if filter_names:
        filter_set = set(filter_names)
        skills = {k: v for k, v in skills.items() if k in filter_set}

    # Filter to only always=true skills
    always_skills: dict[str, str] = {}
    for name, content in skills.items():
        meta = self._get_skill_metadata(name)
        if meta and meta.get("always", False):
            always_skills[name] = content

    if not always_skills:
        return ""

    parts = [f"## Skill: {name}\n\n{content}" for name, content in always_skills.items()]
    total = 0
    kept: list[str] = []
    for p in parts:
        if total + len(p) > self._char_budget:
            break
        kept.append(p)
        total += len(p)

    return "\n\n---\n\n".join(kept) if kept else ""
```

- [ ] **Step 3: Add helper `_get_skill_metadata()` for always check**

```python
def _get_skill_metadata(self, name: str) -> dict | None:
    """Return parsed openclaw metadata for a skill by name."""
    for base, _source in self._search_dirs():
        skill_file = base / name / "SKILL.md"
        if skill_file.is_file():
            try:
                post = frontmatter.load(str(skill_file))
                meta = post.metadata or {}
                oc = meta.get("metadata", {}).get("openclaw", {})
                return {"always": oc.get("always", False)}
            except Exception:
                return None
    return None
```

- [ ] **Step 4: Add `patch_skill()` method**

```python
def patch_skill(self, name: str, old: str, new: str) -> dict:
    """Find-and-replace within a skill's content. Returns updated skill dict."""
    skill = self.get_skill(name)
    if not skill:
        raise FileNotFoundError(f"Skill '{name}' not found")

    content = skill["content"]
    if old not in content:
        raise ValueError(f"Pattern not found in skill '{name}'")

    updated_content = content.replace(old, new, 1)
    return self.save_skill(name, updated_content)
```

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/skills/loader.py
git commit -m "feat(skills): add load_index, load_always, patch_skill to SkillsLoader"
```

---

## Task 2: Skill tools — `skill_list`, `skill_view`, `skill_manage`

**Files:**
- Create: `src/tianshu/tools/skill_tools.py`

- [ ] **Step 1: Create `skill_tools.py` with all three tools**

```python
"""Skill management tools — list, view, and manage skills."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

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

    # Track usage metrics (C3)
    if metrics_store:
        metrics_store.ensure_exists(name)
        metrics_store.increment_usage(name)

    # Track active skills for success/failure attribution (C3)
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
) -> ToolResult:
    """Create, edit, patch, or delete a skill."""
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


def register_skill_tools(registry: ToolRegistry, skills: SkillsLoader) -> None:
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
        lambda **kwargs: _skill_view(skills, **kwargs),
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
        lambda **kwargs: _skill_manage(skills, **kwargs),
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
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/tools/skill_tools.py
git commit -m "feat(tools): add skill_list, skill_view, skill_manage tools"
```

---

## Task 3: Update prompt injection to index mode

**Files:**
- Modify: `src/tianshu/persona/prompt_builder.py:113-118`
- Modify: `src/tianshu/executor/agent.py:423-432`

- [ ] **Step 1: Update PromptBuilder Layer 7**

In `src/tianshu/persona/prompt_builder.py`, replace lines 113-118:

```python
        # Layer 7: Skills (filtered by persona if skills_allowed is set)
        self._skills.set_char_budget(skills_char_budget)
        filter_names = persona.skills_allowed if persona and persona.skills_allowed else None
        skills_text = self._skills.load_all(filter_names=filter_names)
        if skills_text:
            parts.append(skills_text)
```

With:

```python
        # Layer 7: Skills — index + always-on skills
        self._skills.set_char_budget(skills_char_budget)
        filter_names = persona.skills_allowed if persona and persona.skills_allowed else None

        # 7a: Inject skill index (name + description) for progressive loading
        index_text = self._skills.load_index(filter_names=filter_names)
        if index_text:
            parts.append(index_text)

        # 7b: Inject full content for always=true skills
        always_text = self._skills.load_always(filter_names=filter_names)
        if always_text:
            parts.append(always_text)
```

- [ ] **Step 2: Update Agent._build_system_prompt fallback**

In `src/tianshu/executor/agent.py`, replace lines 423-432:

```python
    def _build_system_prompt(self, edict: Edict, skills_char_budget: int) -> str:
        parts = [_SYSTEM_IDENTITY]

        self._skills.set_char_budget(skills_char_budget)
        skills_text = self._skills.load_all()
        if skills_text:
            parts.append(skills_text)

        parts.append(f"Current task ID: {edict.id}")
        return "\n\n".join(parts)
```

With:

```python
    def _build_system_prompt(self, edict: Edict, skills_char_budget: int) -> str:
        parts = [_SYSTEM_IDENTITY]

        self._skills.set_char_budget(skills_char_budget)

        # Progressive loading: index + always-on skills
        index_text = self._skills.load_index()
        if index_text:
            parts.append(index_text)

        always_text = self._skills.load_always()
        if always_text:
            parts.append(always_text)

        parts.append(f"Current task ID: {edict.id}")
        return "\n\n".join(parts)
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/persona/prompt_builder.py src/tianshu/executor/agent.py
git commit -m "feat(skills): switch prompt injection to index + always-on mode"
```

---

## Task 4: Register skill tools in app.py

**Files:**
- Modify: `src/tianshu/app.py:69-70` (add skill tools registration after builtins)

- [ ] **Step 1: Add import and registration**

In `src/tianshu/app.py`, add import at top:

```python
from tianshu.tools.skill_tools import register_skill_tools
```

After the existing `register_builtins(tools, settings.workspace_dir)` line (~line 70), add:

```python
        register_skill_tools(tools, skills_loader)
```

Where `skills_loader` is the `SkillsLoader` instance created around line 81-86. If the `SkillsLoader` is created after the tools registration, move the `register_skill_tools` call to after the `SkillsLoader` instantiation.

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(app): wire up skill_list, skill_view, skill_manage tools"
```

---

## Task 5: SkillValidator — security scanning

**Files:**
- Create: `src/tianshu/skills/validator.py`

- [ ] **Step 1: Create validator module**

```python
"""Skill content validation — security scanning before write."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import frontmatter as fm

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTENT_SIZE = 256 * 1024

# Patterns that indicate potential secrets
_SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"(?:secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style keys
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access keys
]

# Patterns that indicate dangerous commands (warn, not block)
_DANGER_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"sudo\s+", re.IGNORECASE),
    re.compile(r"chmod\s+777", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;", re.IGNORECASE),  # fork bomb
]


@dataclass(frozen=True)
class ValidationFinding:
    level: str  # "error" | "warning"
    check: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    findings: tuple[ValidationFinding, ...] = ()


class SkillValidator:
    """Validate skill content before writing."""

    def validate(self, name: str, content: str) -> ValidationResult:
        findings: list[ValidationFinding] = []

        findings.extend(self._check_name_format(name))
        findings.extend(self._check_size(content))
        findings.extend(self._check_frontmatter(content))
        findings.extend(self._check_no_secrets(content))
        findings.extend(self._check_no_dangerous_commands(content))

        has_errors = any(f.level == "error" for f in findings)
        return ValidationResult(valid=not has_errors, findings=tuple(findings))

    @staticmethod
    def _check_name_format(name: str) -> list[ValidationFinding]:
        if not _NAME_RE.match(name):
            return [ValidationFinding(
                level="error",
                check="name_format",
                message=f"Name '{name}' must match: ^[a-z0-9][a-z0-9._-]{{0,63}}$",
            )]
        return []

    @staticmethod
    def _check_size(content: str) -> list[ValidationFinding]:
        if len(content.encode("utf-8")) > _MAX_CONTENT_SIZE:
            return [ValidationFinding(
                level="error",
                check="size",
                message=f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit",
            )]
        return []

    @staticmethod
    def _check_frontmatter(content: str) -> list[ValidationFinding]:
        try:
            post = fm.loads(content)
        except Exception as e:
            return [ValidationFinding(
                level="error",
                check="frontmatter",
                message=f"Invalid frontmatter: {e}",
            )]

        meta = post.metadata or {}
        findings: list[ValidationFinding] = []
        if not meta.get("name"):
            findings.append(ValidationFinding(
                level="error", check="frontmatter", message="Frontmatter missing required field 'name'",
            ))
        if not meta.get("description"):
            findings.append(ValidationFinding(
                level="error", check="frontmatter", message="Frontmatter missing required field 'description'",
            ))
        return findings

    @staticmethod
    def _check_no_secrets(content: str) -> list[ValidationFinding]:
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                # Show a redacted snippet for context
                start = max(0, match.start() - 10)
                end = min(len(content), match.end() + 10)
                snippet = content[start:end]
                return [ValidationFinding(
                    level="error",
                    check="secrets",
                    message=f"Possible secret detected near: ...{snippet}...",
                )]
        return []

    @staticmethod
    def _check_no_dangerous_commands(content: str) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        for pattern in _DANGER_PATTERNS:
            match = pattern.search(content)
            if match:
                findings.append(ValidationFinding(
                    level="warning",
                    check="dangerous_command",
                    message=f"Dangerous command pattern detected: {match.group()!r}",
                ))
        return findings
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/skills/validator.py
git commit -m "feat(skills): add SkillValidator for security scanning"
```

---

## Task 6: SkillReviewHandler — hook-driven learning

**Files:**
- Create: `src/tianshu/skills/reviewer.py`
- Modify: `src/tianshu/config_manager.py:26-30`
- Modify: `src/tianshu/models/api.py:105-114`

- [ ] **Step 1: Update AgentConfigState with review config**

In `src/tianshu/config_manager.py`, update `AgentConfigState`:

```python
@dataclass(frozen=True)
class AgentConfigState:
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    skills_char_budget: int = 30000
    skill_review_enabled: bool = True
    skill_review_interval: int = 5
```

- [ ] **Step 2: Update API models**

In `src/tianshu/models/api.py`, update `AgentConfig`:

```python
class AgentConfig(BaseModel):
    agent_max_iterations: int
    agent_timeout_seconds: int
    skills_char_budget: int
    skill_review_enabled: bool
    skill_review_interval: int
```

Update `AgentConfigUpdateRequest`:

```python
class AgentConfigUpdateRequest(BaseModel):
    agent_max_iterations: int | None = Field(default=None, ge=1, le=200)
    agent_timeout_seconds: int | None = Field(default=None, ge=10, le=3600)
    skills_char_budget: int | None = Field(default=None, ge=1000, le=500000)
    skill_review_enabled: bool | None = None
    skill_review_interval: int | None = Field(default=None, ge=1, le=100)
```

- [ ] **Step 3: Create SkillReviewHandler**

```python
"""Skill review handler — hook-driven learning loop."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import litellm

from tianshu.config_manager import ConfigManager
from tianshu.executor.exit_reason import ExitReason
from tianshu.executor.hooks import HookResult
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.validator import SkillValidator

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """\
Review the task execution below. Decide if any reusable approach \
should be saved as a skill.

Task goal: {goal}
Exit reason: {exit_reason}
Iterations: {iteration_count}
Tool calls summary:
{tool_calls_summary}

Existing skills:
{skills_index}

Respond in JSON:
{{
  "action": "create" | "update" | "skip",
  "skill_name": "name-here",
  "reason": "why this is worth saving",
  "content": "full SKILL.md content with frontmatter (only if create)",
  "patch_old": "text to find (only if update)",
  "patch_new": "replacement text (only if update)"
}}

Rules:
- Only save approaches that required trial-and-error or non-obvious solutions
- Don't save trivial or one-off tasks
- If an existing skill covers this, update it instead of creating a new one
- Respond "skip" if nothing is worth saving
- For create: content must include YAML frontmatter with name and description
"""


class SkillReviewHandler:
    """Evaluates completed tasks and creates/updates skills when valuable."""

    def __init__(
        self,
        skills: SkillsLoader,
        config_manager: ConfigManager,
        validator: SkillValidator | None = None,
    ) -> None:
        self._skills = skills
        self._config = config_manager
        self._validator = validator or SkillValidator()
        self._tasks_since_last_review = 0

    async def on_agent_end(self, **context) -> HookResult | None:
        """AGENT_END hook handler. Triggers skill review if conditions are met."""
        agent_cfg = self._config.agent_config
        if not agent_cfg.skill_review_enabled:
            return None

        if not self._should_review(context, agent_cfg.skill_review_interval):
            return None

        # Run review in background (non-blocking)
        try:
            await self._run_review(context)
        except Exception:
            logger.exception("Skill review failed")

        return None  # Never block on review

    def _should_review(self, context: dict, review_interval: int) -> bool:
        exit_reason = context.get("exit_reason")
        iteration_count = context.get("iteration_count", 0)

        if exit_reason != ExitReason.COMPLETED:
            return False

        if iteration_count < 3:
            return False

        self._tasks_since_last_review += 1
        if self._tasks_since_last_review < review_interval:
            return False

        self._tasks_since_last_review = 0
        return True

    async def _run_review(self, context: dict) -> None:
        """Execute a lightweight LLM call to review task and decide on skill action."""
        config_state = self._config.state
        if not config_state.enabled:
            return

        # Build tool calls summary from events
        events = context.get("events", [])
        tool_lines: list[str] = []
        for evt in events:
            if isinstance(evt, dict) and evt.get("event_type") == "tool_call":
                payload = evt.get("payload", {})
                name = payload.get("tool_name", "unknown")
                args_keys = list(payload.get("args", {}).keys()) if isinstance(payload.get("args"), dict) else []
                status = "error" if payload.get("is_error") else "success"
                tool_lines.append(f"  {name}({', '.join(args_keys)}) → {status}")

        if not tool_lines:
            tool_lines = ["  (no tool calls recorded)"]

        # Build skills index
        index_meta = self._skills.list_all_metadata()
        skills_index = "\n".join(f"  - {m['name']}: {m.get('description', '')}" for m in index_meta)

        edict = context.get("edict")
        goal = getattr(edict, "goal", "unknown") if edict else "unknown"

        prompt = _REVIEW_PROMPT.format(
            goal=goal,
            exit_reason=context.get("exit_reason", "unknown"),
            iteration_count=context.get("iteration_count", 0),
            tool_calls_summary="\n".join(tool_lines),
            skills_index=skills_index or "  (none)",
        )

        # Infer litellm model string
        model = config_state.model
        api_base = config_state.api_base.strip() if config_state.api_base else ""
        if "/" not in model and api_base:
            model = f"openai/{model}"

        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4096,
            "timeout": 60,
            "drop_params": True,
        }
        if config_state.api_key:
            kwargs["api_key"] = config_state.api_key
        if api_base:
            kwargs["api_base"] = api_base

        logger.info("[SKILL_REVIEW] Running review for edict goal: %s", goal[:80])
        response = await litellm.acompletion(**kwargs)
        content = response.choices[0].message.content
        if not content:
            return

        # Parse JSON response
        try:
            # Handle markdown code blocks
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("[SKILL_REVIEW] Failed to parse review response: %s", content[:200])
            return

        action = result.get("action", "skip")
        skill_name = result.get("skill_name", "")
        reason = result.get("reason", "")

        if action == "skip":
            logger.info("[SKILL_REVIEW] Skipped: %s", reason)
            return

        if action == "create":
            skill_content = result.get("content", "")
            if not skill_content:
                logger.warning("[SKILL_REVIEW] Create action but no content provided")
                return

            validation = self._validator.validate(skill_name, skill_content)
            if not validation.valid:
                findings_str = "; ".join(f.message for f in validation.findings if f.level == "error")
                logger.warning("[SKILL_REVIEW] Validation failed for '%s': %s", skill_name, findings_str)
                return

            try:
                self._skills.create_skill(skill_name, skill_content)
                logger.info("[SKILL_REVIEW] Created skill '%s': %s", skill_name, reason)
            except ValueError as e:
                logger.warning("[SKILL_REVIEW] Failed to create skill '%s': %s", skill_name, e)

        elif action == "update":
            patch_old = result.get("patch_old", "")
            patch_new = result.get("patch_new", "")
            if not patch_old or not patch_new:
                logger.warning("[SKILL_REVIEW] Update action but no patch_old/patch_new")
                return
            try:
                self._skills.patch_skill(skill_name, patch_old, patch_new)
                logger.info("[SKILL_REVIEW] Updated skill '%s': %s", skill_name, reason)
            except (FileNotFoundError, ValueError) as e:
                logger.warning("[SKILL_REVIEW] Failed to update skill '%s': %s", skill_name, e)
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/skills/reviewer.py src/tianshu/config_manager.py src/tianshu/models/api.py
git commit -m "feat(skills): add SkillReviewHandler for hook-driven learning loop"
```

---

## Task 7: Wire up SkillReviewHandler in app.py

**Files:**
- Modify: `src/tianshu/app.py` (add import + hook registration)

- [ ] **Step 1: Add import and registration**

Add import:

```python
from tianshu.skills.reviewer import SkillReviewHandler
from tianshu.skills.validator import SkillValidator
```

After the existing hook registrations (~line 306), add:

```python
        # Skill review hook (learning loop)
        skill_validator = SkillValidator()
        skill_reviewer = SkillReviewHandler(skills_loader, config_manager, skill_validator)
        hook_registry.register(
            HookType.AGENT_END,
            skill_reviewer.on_agent_end,
            priority=200,  # Run after memory_manager (priority=100)
        )
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/app.py
git commit -m "feat(app): wire up SkillReviewHandler hook for auto skill learning"
```

---

## Task 8: SkillMetrics — model + SQLite store

**Files:**
- Create: `src/tianshu/skills/metrics.py`
- Modify: `src/tianshu/storage.py` (add `skill_metrics` table)

- [ ] **Step 1: Add `skill_metrics` table to storage**

In `src/tianshu/storage.py`, add to `_create_tables()` (before the closing `"""`):

```sql
                CREATE TABLE IF NOT EXISTS skill_metrics (
                    skill_name    TEXT PRIMARY KEY,
                    usage_count   INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at  TEXT,
                    created_at    TEXT,
                    created_by    TEXT NOT NULL DEFAULT 'manual',
                    source_edict_id TEXT
                );
```

- [ ] **Step 2: Create `metrics.py`**

```python
"""Skill quality metrics — SQLite-backed usage tracking and health scoring."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class SkillMetrics:
    skill_name: str
    usage_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: str | None = None
    created_at: str | None = None
    created_by: str = "manual"
    source_edict_id: str | None = None

    @property
    def success_rate(self) -> float | None:
        """Return success rate, or None if insufficient data (< 3 uses)."""
        if self.usage_count < 3:
            return None
        return self.success_count / self.usage_count

    @property
    def status(self) -> str:
        """Return health status: healthy / warning / retire_suggested."""
        rate = self.success_rate
        if rate is None:
            return "healthy"
        if rate < 0.3 and self.usage_count >= 5:
            return "retire_suggested"
        if rate < 0.6:
            return "warning"
        return "healthy"

    def is_dormant(self, dormant_days: int = 90) -> bool:
        """Check if skill hasn't been used in dormant_days."""
        if not self.last_used_at:
            return False
        try:
            last = datetime.fromisoformat(self.last_used_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            delta = datetime.now(UTC) - last
            return delta.days > dormant_days
        except (ValueError, TypeError):
            return False


class SkillMetricsStore:
    """SQLite-backed CRUD for skill_metrics table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, skill_name: str) -> SkillMetrics | None:
        row = self._conn.execute(
            "SELECT * FROM skill_metrics WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_metrics(row)

    def get_all(self) -> list[SkillMetrics]:
        rows = self._conn.execute("SELECT * FROM skill_metrics").fetchall()
        return [self._row_to_metrics(r) for r in rows]

    def ensure_exists(self, skill_name: str, created_by: str = "manual", source_edict_id: str | None = None) -> None:
        """Create metrics row if it doesn't exist."""
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO skill_metrics (skill_name, created_at, created_by, source_edict_id)
               VALUES (?, ?, ?, ?)""",
            (skill_name, now, created_by, source_edict_id),
        )
        self._conn.commit()

    def increment_usage(self, skill_name: str) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """UPDATE skill_metrics
               SET usage_count = usage_count + 1, last_used_at = ?
               WHERE skill_name = ?""",
            (now, skill_name),
        )
        self._conn.commit()

    def increment_success(self, skill_name: str) -> None:
        self._conn.execute(
            "UPDATE skill_metrics SET success_count = success_count + 1 WHERE skill_name = ?",
            (skill_name,),
        )
        self._conn.commit()

    def increment_failure(self, skill_name: str) -> None:
        self._conn.execute(
            "UPDATE skill_metrics SET failure_count = failure_count + 1 WHERE skill_name = ?",
            (skill_name,),
        )
        self._conn.commit()

    def delete(self, skill_name: str) -> None:
        self._conn.execute("DELETE FROM skill_metrics WHERE skill_name = ?", (skill_name,))
        self._conn.commit()

    @staticmethod
    def _row_to_metrics(row: sqlite3.Row) -> SkillMetrics:
        return SkillMetrics(
            skill_name=row["skill_name"],
            usage_count=row["usage_count"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            source_edict_id=row["source_edict_id"],
        )
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/skills/metrics.py src/tianshu/storage.py
git commit -m "feat(skills): add SkillMetrics model and SQLite store"
```

---

## Task 9: Wire metrics into skill_tools + agent

**Files:**
- Modify: `src/tianshu/tools/skill_tools.py` (add metrics tracking to skill_view, skill_manage)
- Modify: `src/tianshu/executor/agent.py` (track active_skills, update success/failure on exit)
- Modify: `src/tianshu/app.py` (create SkillMetricsStore, pass to tools and agent)

- [ ] **Step 1: Update `register_skill_tools` to accept metrics store**

In `src/tianshu/tools/skill_tools.py`, update function signature and tool handlers:

Add import:

```python
from tianshu.skills.metrics import SkillMetricsStore
```

Update `register_skill_tools` signature:

```python
def register_skill_tools(
    registry: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: SkillMetricsStore | None = None,
) -> None:
```

Update `_skill_view` to track usage:

```python
async def _skill_view(
    skills: SkillsLoader,
    name: str,
    metrics_store: SkillMetricsStore | None = None,
    active_skills_ref: set | None = None,
) -> ToolResult:
    """View the full content of a skill."""
    skill = skills.get_skill(name)
    if not skill:
        return error_result(f"Skill '{name}' not found")

    # Track usage metrics
    if metrics_store:
        metrics_store.ensure_exists(name)
        metrics_store.increment_usage(name)

    # Track in active_skills for success/failure attribution
    if active_skills_ref is not None:
        active_skills_ref.add(name)

    return ok_result(json.dumps({
        "name": skill["name"],
        "description": skill.get("description", ""),
        "source": skill.get("source", ""),
        "content": skill.get("content", ""),
    }, ensure_ascii=False, indent=2))
```

Update `_skill_manage` to init metrics on create:

```python
async def _skill_manage(
    skills: SkillsLoader,
    action: str,
    name: str,
    content: str | None = None,
    patch_old: str | None = None,
    patch_new: str | None = None,
    metrics_store: SkillMetricsStore | None = None,
) -> ToolResult:
```

In the `action == "create"` branch, after successful creation:

```python
            if metrics_store:
                edict_id = None  # Will be set by caller context if available
                metrics_store.ensure_exists(name, created_by="agent", source_edict_id=edict_id)
```

In the `action == "delete"` branch, after successful deletion:

```python
            if metrics_store:
                metrics_store.delete(name)
```

Update the lambda registrations to pass `metrics_store`:

```python
    # Shared mutable set for tracking which skills are active in current execution
    active_skills: set[str] = set()

    registry.register(
        "skill_view",
        lambda **kwargs: _skill_view(skills, metrics_store=metrics_store, active_skills_ref=active_skills, **kwargs),
        ...
    )

    registry.register(
        "skill_manage",
        lambda **kwargs: _skill_manage(skills, metrics_store=metrics_store, **kwargs),
        ...
    )
```

Add a module-level set and accessors. The `register_skill_tools` function binds this same set as `active_skills_ref` in the `skill_view` lambda:

```python
# Module-level shared set for tracking which skills are active in current execution
_active_skills: set[str] = set()


def get_active_skills() -> set[str]:
    """Return the set of skills viewed during current execution."""
    return _active_skills


def clear_active_skills() -> None:
    """Clear the active skills set (call at end of agent execution)."""
    _active_skills.clear()
```

In `register_skill_tools`, use `_active_skills` directly:

```python
    registry.register(
        "skill_view",
        lambda **kwargs: _skill_view(skills, metrics_store=metrics_store, active_skills_ref=_active_skills, **kwargs),
        ...
    )
```

- [ ] **Step 2: Update Agent to track success/failure**

In `src/tianshu/executor/agent.py`, add import:

```python
from tianshu.tools.skill_tools import get_active_skills, clear_active_skills
```

In `_build_result()` (around line 393), before the return statement:

```python
        # Update skill metrics based on exit reason
        try:
            from tianshu.skills.metrics import SkillMetricsStore
            metrics_store = getattr(self, "_metrics_store", None)
            if metrics_store:
                active = get_active_skills()
                for skill_name in active:
                    if exit_reason == ExitReason.COMPLETED:
                        metrics_store.increment_success(skill_name)
                    else:
                        metrics_store.increment_failure(skill_name)
                clear_active_skills()
        except Exception:
            logger.debug("Skill metrics update failed", exc_info=True)
```

Add `metrics_store` to Agent `__init__`:

```python
    def __init__(
        self,
        ...
        metrics_store: object | None = None,
    ) -> None:
        ...
        self._metrics_store = metrics_store
```

- [ ] **Step 3: Wire up in app.py**

Add import:

```python
from tianshu.skills.metrics import SkillMetricsStore
```

After `storage.init_db()`, create the store:

```python
        metrics_store = SkillMetricsStore(storage._conn)
```

Update `register_skill_tools` call:

```python
        register_skill_tools(tools, skills_loader, metrics_store=metrics_store)
```

Pass `metrics_store` to Agent constructor:

```python
        agent = Agent(
            ...,
            metrics_store=metrics_store,
        )
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/tools/skill_tools.py src/tianshu/executor/agent.py src/tianshu/app.py
git commit -m "feat(skills): wire metrics tracking into skill_view and agent execution"
```

---

## Task 10: Dormant/decay filtering in loader

**Files:**
- Modify: `src/tianshu/skills/loader.py` (add dormant filtering to `load_index`)
- Modify: `src/tianshu/tools/skill_tools.py` (add status + dormant to `skill_list`)

- [ ] **Step 1: Update `load_index` to accept metrics store for filtering**

In `src/tianshu/skills/loader.py`, update `load_index()`:

```python
def load_index(
    self,
    filter_names: list[str] | None = None,
    include_dormant: bool = False,
    metrics_store: object | None = None,
) -> str:
    """Return skill index (name + description only) for system prompt injection."""
    metadata = self.list_all_metadata()

    if filter_names:
        filter_set = set(filter_names)
        metadata = [m for m in metadata if m["name"] in filter_set]

    # Filter dormant skills (unless explicitly requested)
    if not include_dormant and metrics_store is not None:
        filtered = []
        for m in metadata:
            metrics = metrics_store.get(m["name"])
            if metrics and metrics.is_dormant() and metrics.created_by == "agent":
                continue  # Hide dormant agent-created skills
            filtered.append(m)
        metadata = filtered

    lines: list[str] = []
    for m in metadata:
        desc = m.get("description", "")
        # Add warning marker if metrics indicate low quality
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
        "\n</skills_index>\n\n"
        "If a skill matches your current task, load it with skill_view().\n"
        "After completing a difficult task, consider saving reusable approaches "
        "as a new skill with skill_manage()."
    )
    return header + "\n".join(lines) + footer
```

- [ ] **Step 2: Update `skill_list` to include status and metrics**

In `src/tianshu/tools/skill_tools.py`, update `_skill_list`:

```python
async def _skill_list(
    skills: SkillsLoader,
    category: str | None = None,
    include_dormant: bool = False,
    metrics_store: SkillMetricsStore | None = None,
) -> ToolResult:
    """List all available skills with name, description, source, and status."""
    metadata = skills.list_all_metadata()
    if category:
        metadata = [m for m in metadata if category.lower() in m["name"].lower()]

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
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/skills/loader.py src/tianshu/tools/skill_tools.py
git commit -m "feat(skills): add dormant filtering and quality status to skill index"
```

---

## Task 11: Fallback Model

**Files:**
- Modify: `src/tianshu/config_manager.py:26-30`
- Modify: `src/tianshu/executor/agent.py` (LLM call fallback)
- Modify: `src/tianshu/models/api.py`

- [ ] **Step 1: Add fallback config**

In `src/tianshu/config_manager.py`, update `AgentConfigState`:

```python
@dataclass(frozen=True)
class AgentConfigState:
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    skills_char_budget: int = 30000
    skill_review_enabled: bool = True
    skill_review_interval: int = 5
    fallback_llm_config_name: str | None = None
```

In `src/tianshu/models/api.py`, update `AgentConfig` and `AgentConfigUpdateRequest`:

```python
class AgentConfig(BaseModel):
    agent_max_iterations: int
    agent_timeout_seconds: int
    skills_char_budget: int
    skill_review_enabled: bool
    skill_review_interval: int
    fallback_llm_config_name: str | None
```

```python
class AgentConfigUpdateRequest(BaseModel):
    ...
    fallback_llm_config_name: str | None = None
```

- [ ] **Step 2: Add fallback logic in agent execute**

In `src/tianshu/executor/agent.py`, locate the LLM call (around line 220: `response = await llm.chat(...)`). Wrap it with fallback:

```python
            try:
                response = await llm.chat(current_messages, tools=openai_tools)
            except Exception as llm_err:
                # Attempt fallback model if configured
                fallback_name = agent_cfg.fallback_llm_config_name
                if fallback_name and "fallback" not in recovery_attempts:
                    fallback_cfg = self._config_manager.get_config(fallback_name)
                    if fallback_cfg and fallback_cfg.enabled:
                        logger.warning(
                            "[AGENT] Primary LLM failed, switching to fallback '%s': %s",
                            fallback_name, llm_err,
                        )
                        fallback_llm = LLMClient(
                            model=fallback_cfg.model,
                            api_key=fallback_cfg.api_key,
                            api_base=fallback_cfg.api_base,
                            max_retries=fallback_cfg.max_retries,
                            temperature=fallback_cfg.temperature,
                            top_p=fallback_cfg.top_p,
                            max_tokens=fallback_cfg.max_tokens,
                        )
                        response = await fallback_llm.chat(current_messages, tools=openai_tools)
                        recovery_attempts["fallback"] = fallback_name
                    else:
                        raise
                else:
                    raise
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/config_manager.py src/tianshu/executor/agent.py src/tianshu/models/api.py
git commit -m "feat(agent): add fallback model support on LLM failure"
```

---

## Task 12: Streaming — protocols

**Files:**
- Create: `src/tianshu/executor/streaming.py`

- [ ] **Step 1: Create streaming protocols**

```python
"""Streaming callback and cancellation token for agent execution."""

from __future__ import annotations

import asyncio
from typing import Protocol

from tianshu.tools.types import ToolResult


class StreamCallback(Protocol):
    """Protocol for receiving streaming events from agent execution."""

    async def on_delta(self, text: str) -> None:
        """Called for each text token from the LLM."""
        ...

    async def on_tool_call_start(self, name: str) -> None:
        """Called when a tool execution begins."""
        ...

    async def on_tool_call_end(self, name: str, result: ToolResult) -> None:
        """Called when a tool execution completes."""
        ...


class CancellationToken:
    """Thread-safe cancellation signal for agent execution."""

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation. Returns True if cancelled."""
        try:
            await asyncio.wait_for(self._cancelled.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/executor/streaming.py
git commit -m "feat(executor): add StreamCallback protocol and CancellationToken"
```

---

## Task 13: Streaming — LLM + Agent integration

**Files:**
- Modify: `src/tianshu/llm.py` (add `chat_stream` method)
- Modify: `src/tianshu/executor/agent.py` (accept and use stream_callback + cancellation_token)

- [ ] **Step 1: Add `chat_stream` to LLMClient**

In `src/tianshu/llm.py`, add after `chat()` method:

```python
    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ):
        """Streaming chat — yields LLMResponse chunks. Final chunk has full usage."""
        model = self._model
        api_base = self._api_base.strip() if self._api_base else ""
        if "/" not in model and api_base:
            base_lower = api_base.lower()
            prefix = "openai"
            for hint, provider in _PROVIDER_HINTS.items():
                if hint in base_lower:
                    prefix = provider
                    break
            model = f"{prefix}/{model}"

        kwargs: dict = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
            "drop_params": True,
            "stream": True,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if api_base:
            kwargs["api_base"] = api_base
        if tools:
            kwargs["tools"] = tools

        response = await litellm.acompletion(**kwargs)

        collected_content = ""
        collected_tool_calls: list[dict] = []
        finish_reason = None
        usage = UsageSummary()

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            finish_reason = chunk.choices[0].finish_reason or finish_reason

            # Text content
            if delta.content:
                collected_content += delta.content
                yield LLMResponse(
                    content=delta.content,
                    tool_calls=None,
                    usage=UsageSummary(),
                    finish_reason=None,
                )

            # Tool calls (accumulated across chunks)
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    while len(collected_tool_calls) <= idx:
                        collected_tool_calls.append({"id": "", "name": "", "args": ""})
                    tc = collected_tool_calls[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["args"] += tc_delta.function.arguments

            # Usage (typically in the final chunk)
            if chunk.usage:
                usage = UsageSummary(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                    total_tokens=chunk.usage.total_tokens or 0,
                )

        # Final response with complete data
        final_tool_calls = None
        if collected_tool_calls:
            final_tool_calls = [
                {"id": tc["id"], "name": tc["name"], "args": tc["args"]}
                for tc in collected_tool_calls
                if tc["name"]
            ]

        yield LLMResponse(
            content=collected_content or None,
            tool_calls=final_tool_calls or None,
            usage=usage,
            finish_reason=finish_reason,
        )
```

Note: Move `_PROVIDER_HINTS` to module-level (extracted from `chat()`) to reuse.

- [ ] **Step 2: Update Agent.execute to accept streaming params**

In `src/tianshu/executor/agent.py`, add imports:

```python
from tianshu.executor.streaming import CancellationToken, StreamCallback
```

Update `execute` signature:

```python
    async def execute(
        self,
        edict: Edict,
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
        tool_filter: list[str] | None = None,
        persona: object | None = None,
        stream_callback: StreamCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> AgentResult:
```

In the main loop, before LLM call, check cancellation:

```python
            # Check cancellation
            if cancellation_token and cancellation_token.is_cancelled:
                return self._build_result(
                    state, ExitReason.CANCELLED,
                    usage=usage, events=events, recovery=recovery_attempts,
                )
```

Replace the LLM call with streaming-aware version:

```python
            if stream_callback:
                # Streaming path
                final_response = None
                async for chunk in llm.chat_stream(current_messages, tools=openai_tools):
                    if cancellation_token and cancellation_token.is_cancelled:
                        return self._build_result(
                            state, ExitReason.CANCELLED,
                            usage=usage, events=events, recovery=recovery_attempts,
                        )
                    if chunk.content and not chunk.tool_calls:
                        await stream_callback.on_delta(chunk.content)
                    final_response = chunk
                response = final_response
            else:
                response = await llm.chat(current_messages, tools=openai_tools)
```

Before tool execution, notify stream callback:

```python
                if stream_callback:
                    await stream_callback.on_tool_call_start(tc["name"])
```

After tool execution:

```python
                if stream_callback:
                    await stream_callback.on_tool_call_end(tc["name"], tool_result)
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/llm.py src/tianshu/executor/agent.py
git commit -m "feat(streaming): add chat_stream to LLMClient and streaming support in Agent"
```

---

## Task 14: Streaming — WebSocket notifier

**Files:**
- Modify: `src/tianshu/notifier/notifier.py` (add StreamCallback implementation)

- [ ] **Step 1: Add WebSocket stream callback**

In `src/tianshu/notifier/notifier.py`, add after the `Notifier` class:

```python
class WebSocketStreamCallback:
    """StreamCallback implementation that pushes deltas to WebSocket clients."""

    def __init__(self, notifier: Notifier, edict_id: str) -> None:
        self._notifier = notifier
        self._edict_id = edict_id

    async def on_delta(self, text: str) -> None:
        await self._notifier.broadcast_ws({
            "type": "stream.delta",
            "edict_id": self._edict_id,
            "text": text,
        })

    async def on_tool_call_start(self, name: str) -> None:
        await self._notifier.broadcast_ws({
            "type": "stream.tool_start",
            "edict_id": self._edict_id,
            "tool_name": name,
        })

    async def on_tool_call_end(self, name: str, result) -> None:
        await self._notifier.broadcast_ws({
            "type": "stream.tool_end",
            "edict_id": self._edict_id,
            "tool_name": name,
            "is_error": getattr(result, "is_error", False),
        })
```

- [ ] **Step 2: Commit**

```bash
git add src/tianshu/notifier/notifier.py
git commit -m "feat(notifier): add WebSocketStreamCallback for real-time streaming"
```

---

## Task 15: Cross-session memory search tool

**Files:**
- Create: `src/tianshu/tools/memory_tools.py`
- Modify: `src/tianshu/app.py` (register tool)

- [ ] **Step 1: Create `memory_tools.py`**

```python
"""Memory search tool — cross-session recall for agent long-term experience."""

from __future__ import annotations

import json
import logging

from tianshu.storage import Storage
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, error_result, ok_result

logger = logging.getLogger(__name__)


async def _memory_search(
    storage: Storage,
    query: str,
    limit: int = 10,
    category: str | None = None,
) -> ToolResult:
    """Search memory entries using full-text search."""
    if not query.strip():
        return error_result("Query cannot be empty")

    limit = min(max(1, limit), 50)

    try:
        from tianshu.memory.fts import fts_search
        ids = fts_search(storage._conn, query, persona_id=None, limit=limit)

        if not ids:
            return ok_result(json.dumps({"results": [], "message": "No matching memories found"}))

        placeholders = ",".join("?" for _ in ids)
        sql = f"""
            SELECT id, persona_id, category, content, edict_id, created_at
            FROM memory_entries
            WHERE id IN ({placeholders})
        """
        params: list = list(ids)
        if category:
            sql += " AND category = ?"
            params.append(category)

        rows = storage._conn.execute(sql, params).fetchall()

        results = [
            {
                "id": row["id"],
                "persona_id": row["persona_id"],
                "category": row["category"],
                "content": row["content"][:500],  # Truncate for token efficiency
                "edict_id": row["edict_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        return ok_result(json.dumps({"results": results, "total": len(results)}, ensure_ascii=False, indent=2))

    except Exception as e:
        logger.exception("Memory search failed")
        return error_result(f"Memory search failed: {e}")


def register_memory_tools(registry: ToolRegistry, storage: Storage) -> None:
    """Register memory_search tool."""

    registry.register(
        "memory_search",
        lambda **kwargs: _memory_search(storage, **kwargs),
        ToolDefinition(
            name="memory_search",
            description=(
                "Search past task memories and insights using keywords. "
                "Returns matching memory entries with summaries. "
                "Use this to recall past experiences and approaches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10, max 50)",
                        "default": 10,
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter by memory category (observation/insight/entity/summary)",
                        "enum": ["observation", "insight", "entity", "summary"],
                    },
                },
                "required": ["query"],
            },
            tier=0,
        ),
    )
```

- [ ] **Step 2: Register in app.py**

Add import:

```python
from tianshu.tools.memory_tools import register_memory_tools
```

After `register_skill_tools(...)`:

```python
        register_memory_tools(tools, storage)
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/tools/memory_tools.py src/tianshu/app.py
git commit -m "feat(tools): add memory_search tool for cross-session recall"
```

---

## Task 16: Tests

**Files:**
- Create: `tests/test_skill_loader_index.py`
- Create: `tests/test_skill_tools.py`
- Create: `tests/test_skill_validator.py`
- Create: `tests/test_skill_reviewer.py`
- Create: `tests/test_skill_metrics.py`
- Create: `tests/test_fallback_model.py`
- Create: `tests/test_streaming.py`
- Create: `tests/test_memory_tools.py`

> **Note:** This task covers all tests for the preceding features. Each test file maps to one component. Implement after all feature tasks are complete.

- [ ] **Step 1: Test SkillsLoader.load_index + patch_skill**

```python
# tests/test_skill_loader_index.py
"""Tests for SkillsLoader progressive loading (load_index, load_always, patch_skill)."""

import pytest
from pathlib import Path

from tianshu.skills.loader import SkillsLoader


@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skills directory with test skills."""
    # Skill 1: always=true
    s1 = tmp_path / "always-skill"
    s1.mkdir()
    (s1 / "SKILL.md").write_text(
        "---\nname: always-skill\ndescription: Always loaded\n"
        "metadata:\n  openclaw:\n    always: true\n---\nAlways content here."
    )
    # Skill 2: normal
    s2 = tmp_path / "normal-skill"
    s2.mkdir()
    (s2 / "SKILL.md").write_text(
        "---\nname: normal-skill\ndescription: Normal skill\n---\nNormal content."
    )
    return tmp_path


@pytest.fixture
def loader(skills_dir):
    return SkillsLoader(builtin_dir=skills_dir, char_budget=50000)


def test_load_index_returns_names_and_descriptions(loader):
    result = loader.load_index()
    assert "always-skill: Always loaded" in result
    assert "normal-skill: Normal skill" in result
    assert "<skills_index>" in result


def test_load_index_filter_names(loader):
    result = loader.load_index(filter_names=["always-skill"])
    assert "always-skill" in result
    assert "normal-skill" not in result


def test_load_always_returns_only_always_skills(loader):
    result = loader.load_always()
    assert "Always content here" in result
    assert "Normal content" not in result


def test_patch_skill_replaces_content(loader):
    result = loader.patch_skill("normal-skill", "Normal content", "Updated content")
    assert result["content"] == "Updated content."


def test_patch_skill_not_found(loader):
    with pytest.raises(FileNotFoundError):
        loader.patch_skill("nonexistent", "old", "new")


def test_patch_skill_pattern_not_found(loader):
    with pytest.raises(ValueError, match="Pattern not found"):
        loader.patch_skill("normal-skill", "nonexistent pattern", "new")
```

- [ ] **Step 2: Test skill tools**

```python
# tests/test_skill_tools.py
"""Tests for skill_list, skill_view, skill_manage tools."""

import json
import pytest
from pathlib import Path

from tianshu.skills.loader import SkillsLoader
from tianshu.tools.skill_tools import _skill_list, _skill_view, _skill_manage


@pytest.fixture
def skills_dir(tmp_path):
    s1 = tmp_path / "test-skill"
    s1.mkdir()
    (s1 / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: A test\n---\nTest content."
    )
    return tmp_path


@pytest.fixture
def loader(skills_dir, tmp_path):
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()
    return SkillsLoader(builtin_dir=skills_dir, user_dir=user_dir, char_budget=50000)


@pytest.mark.asyncio
async def test_skill_list_returns_all(loader):
    result = await _skill_list(loader)
    data = json.loads(result.content)
    names = [s["name"] for s in data]
    assert "test-skill" in names


@pytest.mark.asyncio
async def test_skill_view_returns_content(loader):
    result = await _skill_view(loader, name="test-skill")
    assert not result.is_error
    data = json.loads(result.content)
    assert "Test content" in data["content"]


@pytest.mark.asyncio
async def test_skill_view_not_found(loader):
    result = await _skill_view(loader, name="nonexistent")
    assert result.is_error


@pytest.mark.asyncio
async def test_skill_manage_create(loader):
    content = "---\nname: new-skill\ndescription: New\n---\nNew content."
    result = await _skill_manage(loader, action="create", name="new-skill", content=content)
    assert not result.is_error
    data = json.loads(result.content)
    assert data["status"] == "created"


@pytest.mark.asyncio
async def test_skill_manage_invalid_name(loader):
    result = await _skill_manage(loader, action="create", name="INVALID!", content="x")
    assert result.is_error
    assert "Invalid skill name" in result.content


@pytest.mark.asyncio
async def test_skill_manage_delete(loader):
    # Create then delete
    content = "---\nname: del-me\ndescription: Delete\n---\nDelete."
    await _skill_manage(loader, action="create", name="del-me", content=content)
    result = await _skill_manage(loader, action="delete", name="del-me")
    assert not result.is_error
```

- [ ] **Step 3: Test SkillValidator**

```python
# tests/test_skill_validator.py
"""Tests for SkillValidator security scanning."""

from tianshu.skills.validator import SkillValidator


def test_valid_skill():
    v = SkillValidator()
    content = "---\nname: good-skill\ndescription: Good\n---\nSafe content."
    result = v.validate("good-skill", content)
    assert result.valid


def test_invalid_name():
    v = SkillValidator()
    result = v.validate("INVALID!", "---\nname: x\ndescription: x\n---\ncontent")
    assert not result.valid
    assert any(f.check == "name_format" for f in result.findings)


def test_missing_frontmatter_name():
    v = SkillValidator()
    result = v.validate("ok-name", "---\ndescription: x\n---\ncontent")
    assert not result.valid
    assert any("name" in f.message for f in result.findings)


def test_detects_api_key():
    v = SkillValidator()
    content = "---\nname: x\ndescription: x\n---\napi_key = sk-1234567890abcdefghijklmnopqrst"
    result = v.validate("x", content)
    assert not result.valid
    assert any(f.check == "secrets" for f in result.findings)


def test_dangerous_command_is_warning():
    v = SkillValidator()
    content = "---\nname: x\ndescription: x\n---\nRun: sudo rm -rf / to clean up"
    result = v.validate("x", content)
    # Dangerous commands are warnings, not errors
    assert result.valid
    assert any(f.level == "warning" for f in result.findings)
```

- [ ] **Step 4: Test SkillMetrics**

```python
# tests/test_skill_metrics.py
"""Tests for SkillMetrics model and SkillMetricsStore."""

import sqlite3
import pytest

from tianshu.skills.metrics import SkillMetrics, SkillMetricsStore


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE skill_metrics (
            skill_name TEXT PRIMARY KEY,
            usage_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            last_used_at TEXT,
            created_at TEXT,
            created_by TEXT DEFAULT 'manual',
            source_edict_id TEXT
        )
    """)
    return c


@pytest.fixture
def store(conn):
    return SkillMetricsStore(conn)


def test_ensure_exists_and_get(store):
    store.ensure_exists("test-skill", created_by="agent")
    m = store.get("test-skill")
    assert m is not None
    assert m.skill_name == "test-skill"
    assert m.created_by == "agent"
    assert m.usage_count == 0


def test_increment_usage(store):
    store.ensure_exists("s1")
    store.increment_usage("s1")
    store.increment_usage("s1")
    m = store.get("s1")
    assert m.usage_count == 2
    assert m.last_used_at is not None


def test_success_rate(store):
    store.ensure_exists("s1")
    for _ in range(5):
        store.increment_usage("s1")
    for _ in range(3):
        store.increment_success("s1")
    for _ in range(2):
        store.increment_failure("s1")
    m = store.get("s1")
    assert m.success_rate == pytest.approx(0.6)
    assert m.status == "warning"  # < 60% but >= 30%


def test_status_healthy_insufficient_data():
    m = SkillMetrics(skill_name="x", usage_count=2, success_count=0, failure_count=2)
    assert m.status == "healthy"  # < 3 uses, not enough data


def test_status_retire_suggested():
    m = SkillMetrics(skill_name="x", usage_count=10, success_count=2, failure_count=8)
    assert m.status == "retire_suggested"  # 20% success rate


def test_dormant():
    m = SkillMetrics(skill_name="x", last_used_at="2025-01-01T00:00:00+00:00")
    assert m.is_dormant(dormant_days=90)
```

- [ ] **Step 5: Test streaming protocols**

```python
# tests/test_streaming.py
"""Tests for StreamCallback and CancellationToken."""

import asyncio
import pytest

from tianshu.executor.streaming import CancellationToken


@pytest.mark.asyncio
async def test_cancellation_token_default_not_cancelled():
    token = CancellationToken()
    assert not token.is_cancelled


@pytest.mark.asyncio
async def test_cancellation_token_cancel():
    token = CancellationToken()
    token.cancel()
    assert token.is_cancelled


@pytest.mark.asyncio
async def test_cancellation_token_wait_timeout():
    token = CancellationToken()
    result = await token.wait(timeout=0.01)
    assert result is False


@pytest.mark.asyncio
async def test_cancellation_token_wait_cancelled():
    token = CancellationToken()
    asyncio.get_event_loop().call_later(0.01, token.cancel)
    result = await token.wait(timeout=1.0)
    assert result is True
```

- [ ] **Step 6: Commit all tests**

```bash
git add tests/test_skill_loader_index.py tests/test_skill_tools.py tests/test_skill_validator.py tests/test_skill_metrics.py tests/test_streaming.py
git commit -m "test: add tests for skill system, validator, metrics, and streaming"
```

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/ -v --tb=short`

Expected: All tests PASS.

---

## Dependency Graph

```
Task 1 (loader changes)
  ↓
Task 2 (skill tools) ──→ Task 4 (wire app.py)
  ↓
Task 3 (prompt injection)
  ↓
Task 5 (validator) ──→ Task 6 (reviewer) ──→ Task 7 (wire reviewer)
  ↓
Task 8 (metrics store) ──→ Task 9 (wire metrics) ──→ Task 10 (dormant filter)

Task 11 (fallback) — independent, can run after Task 6 (shares config changes)
Task 12 (streaming protocols) ──→ Task 13 (agent streaming) ──→ Task 14 (WS notifier)
Task 15 (memory search) — independent

Task 16 (all tests) — after all above
```
