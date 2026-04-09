"""Skill review handler — hook-driven learning loop."""

from __future__ import annotations

import json
import logging
from typing import Any

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

    async def on_agent_end(self, **context: Any) -> HookResult | None:
        """AGENT_END hook handler. Triggers skill review if conditions are met."""
        agent_cfg = self._config.agent_config
        if not agent_cfg.skill_review_enabled:
            return None

        if not self._should_review(context, agent_cfg.skill_review_interval):
            return None

        # Run review (non-blocking for the hook)
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

        tool_lines = self._build_tool_summary(context.get("events", []))
        skills_index = self._build_skills_index()

        edict = context.get("edict")
        goal = getattr(edict, "goal", "unknown") if edict else "unknown"

        prompt = _REVIEW_PROMPT.format(
            goal=goal,
            exit_reason=context.get("exit_reason", "unknown"),
            iteration_count=context.get("iteration_count", 0),
            tool_calls_summary="\n".join(tool_lines),
            skills_index=skills_index or "  (none)",
        )

        response = await self._call_llm(config_state, prompt)
        if not response:
            return

        self._apply_review_result(response)

    @staticmethod
    def _build_tool_summary(events: list) -> list[str]:
        tool_lines: list[str] = []
        for evt in events:
            if isinstance(evt, dict) and evt.get("event_type") == "tool_call":
                payload = evt.get("payload", {})
                name = payload.get("tool_name", "unknown")
                args_keys = (
                    list(payload.get("args", {}).keys())
                    if isinstance(payload.get("args"), dict)
                    else []
                )
                status = "error" if payload.get("is_error") else "success"
                tool_lines.append(f"  {name}({', '.join(args_keys)}) → {status}")
        return tool_lines or ["  (no tool calls recorded)"]

    def _build_skills_index(self) -> str:
        index_meta = self._skills.list_all_metadata()
        return "\n".join(
            f"  - {m['name']}: {m.get('description', '')}" for m in index_meta
        )

    @staticmethod
    async def _call_llm(config_state: Any, prompt: str) -> str | None:
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

        logger.info("[SKILL_REVIEW] Running review LLM call")
        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or None

    def _apply_review_result(self, content: str) -> None:
        """Parse LLM JSON response and apply skill create/update."""
        try:
            cleaned = content.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("[SKILL_REVIEW] Failed to parse response: %s", content[:200])
            return

        action = result.get("action", "skip")
        skill_name = result.get("skill_name", "")
        reason = result.get("reason", "")

        if action == "skip":
            logger.info("[SKILL_REVIEW] Skipped: %s", reason)
            return

        if action == "create":
            self._handle_create(skill_name, result.get("content", ""), reason)
        elif action == "update":
            self._handle_update(skill_name, result.get("patch_old", ""), result.get("patch_new", ""), reason)

    def _handle_create(self, name: str, content: str, reason: str) -> None:
        if not content:
            logger.warning("[SKILL_REVIEW] Create action but no content provided")
            return

        validation = self._validator.validate(name, content)
        if not validation.valid:
            findings_str = "; ".join(f.message for f in validation.findings if f.level == "error")
            logger.warning("[SKILL_REVIEW] Validation failed for '%s': %s", name, findings_str)
            return

        try:
            self._skills.create_skill(name, content)
            logger.info("[SKILL_REVIEW] Created skill '%s': %s", name, reason)
        except ValueError as e:
            logger.warning("[SKILL_REVIEW] Failed to create skill '%s': %s", name, e)

    def _handle_update(self, name: str, patch_old: str, patch_new: str, reason: str) -> None:
        if not patch_old or not patch_new:
            logger.warning("[SKILL_REVIEW] Update action but no patch_old/patch_new")
            return
        try:
            self._skills.patch_skill(name, patch_old, patch_new)
            logger.info("[SKILL_REVIEW] Updated skill '%s': %s", name, reason)
        except (FileNotFoundError, ValueError) as e:
            logger.warning("[SKILL_REVIEW] Failed to update skill '%s': %s", name, e)
