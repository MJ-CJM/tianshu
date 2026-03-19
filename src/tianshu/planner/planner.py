"""Planner — task decomposition for complex edicts."""

from __future__ import annotations

import json
import logging

from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.llm import LLMClient
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.plan import Plan, PlanTask
from tianshu.planner.prompts import PLANNING_SYSTEM_PROMPT, PLANNING_USER_TEMPLATE
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
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._config_manager = config_manager
        self._selector = official_selector

    def needs_planning(self, edict: Edict) -> bool:
        """Heuristic: skip planning for simple edicts."""
        if edict.metadata.get("skip_planning"):
            return False
        if (
            len(edict.goal) < 100
            and not edict.constraints
            and not edict.output_format
        ):
            return False
        return True

    async def plan(self, edict: Edict) -> Plan:
        """Generate a plan via LLM, or return a single-task passthrough plan."""
        if not self.needs_planning(edict):
            return self._passthrough_plan(edict)

        state = self._config_manager.state
        if not state.enabled:
            return self._passthrough_plan(edict)

        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            max_retries=state.max_retries,
            temperature=0.3,
            top_p=state.top_p,
            max_tokens=2048,
        )

        user_msg = PLANNING_USER_TEMPLATE.format(
            goal=edict.goal,
            context=edict.context or "None",
            constraints=", ".join(edict.constraints) if edict.constraints else "None",
            output_format=edict.output_format or "None",
        )

        messages = [
            {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        try:
            response = await llm.chat(messages)
            if response.content:
                plan_data = json.loads(response.content)
                tasks = [PlanTask(**t) for t in plan_data.get("tasks", [])]
                self._assign_officials(tasks)
                return Plan(
                    tasks=tasks,
                    priority_order=plan_data.get("priority_order", []),
                )
        except Exception:
            logger.exception("Planning failed for edict %s, using passthrough", edict.id)

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

        plan = await self.plan(edict)

        payload: dict = {"plan": plan.model_dump()}
        memorial_id = event.memorial_id
        await self._bus.emit(
            make_event(
                "plan.completed",
                edict_id=edict.id,
                memorial_id=memorial_id,
                producer="planner",
                payload=payload,
            )
        )

    def _passthrough_plan(self, edict: Edict) -> Plan:
        """Single-task plan that passes the entire goal to the executor."""
        task = PlanTask(
            task_id="main",
            description=edict.goal,
            assigned_official="bingbu",
        )
        return Plan(tasks=[task], priority_order=["main"])

    def _assign_officials(self, tasks: list[PlanTask]) -> None:
        """Assign official to each task via OfficialSelector, default to bingbu."""
        if not self._selector:
            for t in tasks:
                if not t.assigned_official:
                    t.assigned_official = "bingbu"
            return

        from tianshu.persona.selector import OfficialSelector

        selector: OfficialSelector = self._selector
        for t in tasks:
            if t.assigned_official:
                continue
            persona = selector.select("execute")
            t.assigned_official = persona.id if persona else "bingbu"
