"""Planner — task decomposition for complex edicts."""

from __future__ import annotations

import json
import logging
import re

from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.llm import LLMClient
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.plan import Plan, PlanTask
from tianshu.persona.model import DEFAULT_EXECUTOR_ID
from tianshu.planner.prompts import (
    PLANNING_USER_TEMPLATE,
    build_planning_prompt,
    format_officials_roster,
    format_tools_list,
)
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Planner:
    """Subscribes to edict.scheduled and produces plans."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        config_manager: ConfigManager,
        official_selector: object | None = None,
        persona_loader: object | None = None,
        prompt_builder: object | None = None,
        tool_registry: object | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._config_manager = config_manager
        self._selector = official_selector
        self._persona_loader = persona_loader
        self._prompt_builder = prompt_builder
        self._tool_registry = tool_registry

    async def plan(self, edict: Edict) -> Plan:
        """Generate a plan via LLM, or return a single-task passthrough plan."""
        # 用户直接指派 → 跳过 LLM 规划，直通该 persona
        if edict.assigned_persona_id:
            logger.info(
                "Edict %s: direct assignment to %s, skipping LLM planning",
                edict.id, edict.assigned_persona_id,
            )
            return self._passthrough_plan(edict, persona_id=edict.assigned_persona_id)

        # 内阁决策模式 → 总是尝试 LLM 规划

        state = self._config_manager.state
        config_source = "global"
        if edict.planner_persona_id and self._persona_loader:
            persona = self._persona_loader.get(edict.planner_persona_id)
            if persona and persona.llm_config_name:
                named_config = self._config_manager.get_config(persona.llm_config_name)
                if named_config and named_config.enabled:
                    state = named_config
                    config_source = f"persona:{edict.planner_persona_id}({persona.llm_config_name})"
                else:
                    logger.warning(
                        "Edict %s: planner persona %s config '%s' not found or disabled, using global",
                        edict.id, edict.planner_persona_id, persona.llm_config_name,
                    )
            else:
                logger.warning(
                    "Edict %s: planner persona %s has no llm_config_name",
                    edict.id, edict.planner_persona_id,
                )

        if not state.enabled:
            logger.warning("Edict %s: LLM config disabled (%s), using passthrough", edict.id, config_source)
            return self._passthrough_plan(edict)

        logger.debug(
            "[PLAN] Edict %s: start planning, planner_persona=%s",
            edict.id, edict.planner_persona_id,
        )
        logger.info(
            "Edict %s: LLM planning with config=%s model=%s",
            edict.id, config_source, state.model,
        )

        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            max_retries=state.max_retries,
            temperature=0.3,
            top_p=state.top_p,
            max_tokens=2048,
        )

        # 1. 构建规划官人格上下文
        persona_context = ""
        if self._prompt_builder and edict.planner_persona_id and self._persona_loader:
            planner_persona = self._persona_loader.get(edict.planner_persona_id)
            if planner_persona:
                try:
                    persona_context = self._prompt_builder.build(
                        edict, persona=planner_persona, skills_char_budget=5000,
                    )
                except Exception:
                    logger.warning("Failed to build planner persona context, using base prompt")

        # 2. 构建可用官员名册
        officials_roster = ""
        if self._selector:
            from tianshu.persona.selector import OfficialSelector
            selector: OfficialSelector = self._selector
            executors = [p for p in selector.list_all() if p.department != "neige"]
            officials_roster = format_officials_roster(executors)

        # 3. 构建可用工具列表
        tools_list = ""
        if self._tool_registry:
            tool_names = [d.name for d in self._tool_registry.list_definitions()]
            tools_list = format_tools_list(tool_names)

        logger.debug(
            "[PLAN] Edict %s: prompt built, persona_ctx=%d chars, roster=%d officials, tools=%d",
            edict.id, len(persona_context),
            len(executors) if officials_roster else 0,
            len(tool_names) if tools_list else 0,
        )

        # 4. 组装最终 system prompt
        system_prompt = build_planning_prompt(persona_context, officials_roster, tools_list)

        user_msg = PLANNING_USER_TEMPLATE.format(
            goal=edict.goal,
            context=edict.context or "None",
            constraints=", ".join(edict.constraints) if edict.constraints else "None",
            output_format=edict.output_format or "None",
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            response = await llm.chat(messages)

            # deepseek-reasoner 等推理模型可能把内容放在 reasoning_content 而非 content
            raw = response.content or ""
            if not raw.strip() and response.reasoning_content:
                raw = response.reasoning_content
                logger.info("Edict %s: using reasoning_content as plan source", edict.id)

            if not raw.strip():
                logger.warning("Edict %s: LLM returned empty response, using passthrough", edict.id)
                return self._passthrough_plan(edict)

            plan_data = self._extract_json(raw)
            if plan_data is None:
                logger.warning(
                    "Edict %s: could not parse JSON from LLM response (len=%d), using passthrough. Response: %.500s",
                    edict.id, len(raw), raw,
                )
                return self._passthrough_plan(edict)

            logger.debug(
                "[PLAN] Edict %s: LLM response len=%d, parsed=%s",
                edict.id, len(raw), plan_data is not None,
            )

            tasks = [PlanTask(**t) for t in plan_data.get("tasks", [])]
            if not tasks:
                logger.warning("Edict %s: LLM plan has no tasks, using passthrough", edict.id)
                return self._passthrough_plan(edict)

            self._validate_assignments(tasks)
            logger.info(
                "Edict %s: LLM planning produced %d tasks",
                edict.id, len(tasks),
            )
            return Plan(
                tasks=tasks,
                priority_order=plan_data.get("priority_order", []),
            )
        except Exception:
            logger.exception("Edict %s: LLM planning failed, using passthrough", edict.id)

        return self._passthrough_plan(edict)

    async def handle_scheduled(self, event: EventEnvelope) -> None:
        """EventBus handler for edict.scheduled."""
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            logger.error("Planner: edict %s not found", edict_id)
            return

        # Set PLANNING status on memorial
        memorial_id = event.memorial_id
        if memorial_id:
            from tianshu.models.common import TaskStatus
            memorial = self._storage.get_memorial(memorial_id)
            if memorial and memorial.status.value in ("submitted", "scheduled"):
                memorial.status = TaskStatus.PLANNING
                self._storage.update_memorial(memorial)

        plan = await self.plan(edict)

        payload: dict = {"plan": plan.model_dump()}
        memorial_id = event.memorial_id

        if edict.plan_review and len(plan.tasks) > 0:
            # 需要审批 → 发 plan.pending_review，不触发执行
            if memorial_id:
                from tianshu.models.common import TaskStatus as _TS
                memorial = self._storage.get_memorial(memorial_id)
                if memorial:
                    memorial.status = _TS.NEEDS_REVIEW
                    self._storage.update_memorial(memorial)
            await self._bus.emit(
                make_event(
                    "plan.pending_review",
                    edict_id=edict.id,
                    memorial_id=memorial_id,
                    producer="planner",
                    payload=payload,
                )
            )
        else:
            # 无需审批 → 直接 plan.completed，触发执行
            await self._bus.emit(
                make_event(
                    "plan.completed",
                    edict_id=edict.id,
                    memorial_id=memorial_id,
                    producer="planner",
                    payload=payload,
                )
            )

    def _passthrough_plan(self, edict: Edict, persona_id: str = DEFAULT_EXECUTOR_ID) -> Plan:
        """Single-task plan that passes the entire goal to the executor."""
        task = PlanTask(
            task_id="main",
            description=edict.goal,
            assigned_official=persona_id,
        )
        return Plan(tasks=[task], priority_order=["main"])

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown code blocks."""
        # Try direct parse first
        stripped = text.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Try extracting from ```json ... ``` or ``` ... ```
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", stripped, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try finding first { ... } block
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _validate_assignments(self, tasks: list[PlanTask]) -> None:
        """Validate LLM-assigned officials, fallback via selector for invalid ones."""
        valid_ids: set[str] = set()
        if self._persona_loader:
            valid_ids = {p.id for p in self._persona_loader._personas.values()}

        for t in tasks:
            if t.assigned_official and t.assigned_official in valid_ids:
                continue
            # Invalid or missing → selector fallback
            old_id = t.assigned_official or "unset"
            if self._selector and hasattr(self._selector, "select_for_task"):
                persona = self._selector.select_for_task(t.description)
                t.assigned_official = persona.id if persona else DEFAULT_EXECUTOR_ID
            else:
                t.assigned_official = DEFAULT_EXECUTOR_ID
            logger.info(
                "Task %s: persona reassigned %s → %s (selector match on description)",
                t.task_id, old_id, t.assigned_official,
            )

        # Log final assignments for debugging
        assignments = {t.task_id: t.assigned_official for t in tasks}
        logger.info("Plan assignments finalized: %s", assignments)
