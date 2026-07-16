"""Planner — task decomposition for complex edicts."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from ulid import ULID

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.application.run_execution import ManagedPlanningResult
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import ConfigManager
from tianshu.llm import LLMClient
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.common import TaskStatus
from tianshu.models.decision import DecisionKind, DecisionRecordV1, DecisionStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.memorial import Memorial
from tianshu.models.plan import Plan, PlanTask
from tianshu.models.plan_revision import PlanRevisionV1, build_plan_revision
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.persona.model import DEFAULT_EXECUTOR_ID
from tianshu.planner.prompts import (
    PLANNING_USER_TEMPLATE,
    build_planning_prompt,
    format_officials_roster,
    format_tools_list,
)
from tianshu.security.sensitive_payload import redact_sensitive_mapping
from tianshu.storage import Storage
from tianshu.storage.decision_repo import DecisionDecodeError

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
        approval_manager: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._config_manager = config_manager
        self._selector = official_selector
        self._persona_loader = persona_loader
        self._prompt_builder = prompt_builder
        self._tool_registry = tool_registry
        self._approval_manager = approval_manager
        self._clock = clock or (lambda: datetime.now(UTC))

    def persist_plan_revision(
        self,
        *,
        memorial_id: str,
        plan: Plan,
        parent_revision_id: str | None,
        reason_code: str,
        reason_summary: str,
        scheduled_event_id: str | None = None,
        scheduled_event_hash: str | None = None,
    ) -> PlanRevisionV1:
        """Append one immutable revision at a non-executing planning boundary."""

        memorial = self._storage.get_memorial(memorial_id)
        if memorial is None:
            raise RuntimeError("plan revision memorial is unavailable")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("planner clock must be timezone-aware")
        now = now.astimezone(UTC)
        safe_plan = cast(
            dict[str, JsonValue],
            redact_sensitive_mapping(plan.model_dump(mode="json")),
        )
        with self._storage.unit_of_work() as unit_of_work:
            current = self._storage.run_state_repo.load(
                unit_of_work.connection,
                memorial.id,
            )
            if current is not None and current.phase not in {
                RunPhase.PLANNING,
                RunPhase.PAUSED,
            }:
                raise RuntimeError("plan revision requires a non-executing planning boundary")
            if current is not None and not isinstance(current.continuation, AgentContinuationV1):
                raise RuntimeError("plan revision requires an agent continuation")
            lineage = current.continuation.plan_revisions if current is not None else ()
            expected_parent = lineage[-1].revision_id if lineage else None
            if parent_revision_id != expected_parent:
                raise RuntimeError("plan revision parent does not match the durable lineage head")
            revision = build_plan_revision(
                safe_plan,
                revision_id=str(ULID()),
                parent_revision_id=parent_revision_id,
                reason_code=reason_code,
                reason_summary=reason_summary,
                created_at=now,
            )
            plan_ref = f"plan:{memorial.edict_id}:{len(lineage) + 1}"
            if current is None:
                continuation = AgentContinuationV1(
                    messages=(),
                    pending_tool=None,
                    iteration=0,
                    usage=PersistedUsageSummaryV1.model_validate(
                        memorial.usage.model_dump(mode="python")
                    ),
                    checkpoint_ref=plan_ref,
                    resolved_decision_id=None,
                    side_effect_cursor=0,
                    plan_ref=plan_ref,
                    plan_hash=revision.plan_hash,
                    plan_revision_id=revision.revision_id,
                    plan_revisions=(revision,),
                    plan_snapshot=safe_plan,
                    scheduled_event_id=scheduled_event_id,
                    scheduled_event_hash=scheduled_event_hash,
                )
                state = RunStateV1(
                    memorial_id=memorial.id,
                    edict_id=memorial.edict_id,
                    phase=RunPhase.PLANNING,
                    continuation=continuation,
                    checkpoint_ref=plan_ref,
                    side_effect_cursor=0,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                self._storage.run_state_repo.create(unit_of_work.connection, state)
            else:
                continuation = current.continuation.model_copy(
                    update={
                        "checkpoint_ref": plan_ref,
                        "pending_decision_id": None,
                        "resolved_decision_id": None,
                        "plan_ref": plan_ref,
                        "plan_hash": revision.plan_hash,
                        "plan_revision_id": revision.revision_id,
                        "plan_revisions": (*lineage, revision),
                        "plan_snapshot": safe_plan,
                    }
                )
                state = current.model_copy(
                    update={
                        "phase": RunPhase.PLANNING,
                        "continuation": continuation,
                        "checkpoint_ref": plan_ref,
                        "updated_at": max(current.updated_at, now),
                    }
                )
                self._storage.run_state_repo.compare_and_swap(
                    unit_of_work.connection,
                    state,
                    expected_version=current.version,
                )
            unit_of_work.commit()
        return revision

    async def plan(self, edict: Edict) -> Plan:
        """Generate a plan via LLM, or return a single-task passthrough plan."""
        # 用户直接指派 → 跳过 LLM 规划，直通该 persona
        if edict.assigned_persona_id:
            logger.info(
                "Edict %s: direct assignment to %s, skipping LLM planning",
                edict.id,
                edict.assigned_persona_id,
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
                        edict.id,
                        edict.planner_persona_id,
                        persona.llm_config_name,
                    )
            else:
                logger.warning(
                    "Edict %s: planner persona %s has no llm_config_name",
                    edict.id,
                    edict.planner_persona_id,
                )

        if not state.enabled:
            logger.warning(
                "Edict %s: LLM config disabled (%s), using passthrough", edict.id, config_source
            )
            return self._passthrough_plan(edict)

        logger.debug(
            "[PLAN] Edict %s: start planning, planner_persona=%s",
            edict.id,
            edict.planner_persona_id,
        )
        logger.info(
            "Edict %s: LLM planning with config=%s model=%s",
            edict.id,
            config_source,
            state.model,
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
                    persona_context = await self._prompt_builder.build(
                        edict,
                        persona=planner_persona,
                        skills_char_budget=5000,
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
            edict.id,
            len(persona_context),
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
                    edict.id,
                    len(raw),
                    raw,
                )
                return self._passthrough_plan(edict)

            logger.debug(
                "[PLAN] Edict %s: LLM response len=%d, parsed=%s",
                edict.id,
                len(raw),
                plan_data is not None,
            )

            tasks = [PlanTask(**t) for t in plan_data.get("tasks", [])]
            if not tasks:
                logger.warning("Edict %s: LLM plan has no tasks, using passthrough", edict.id)
                return self._passthrough_plan(edict)

            self._validate_assignments(tasks)
            logger.info(
                "Edict %s: LLM planning produced %d tasks",
                edict.id,
                len(tasks),
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
        if self._is_retained_scheduled_replay(event):
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
            # 通用裁决先落库；pending_review 只是兼容展示投影。
            if memorial_id is None or self._approval_manager is None:
                raise RuntimeError("durable plan review requires a memorial and ApprovalManager")
            memorial = self._storage.get_memorial(memorial_id)
            if memorial is None:
                raise RuntimeError("durable plan review memorial is unavailable")
            from tianshu.models.common import TaskStatus as _TS

            request_decision = getattr(
                self._approval_manager,
                "request_plan_review_decision",
                None,
            )
            if not callable(request_decision):
                raise RuntimeError("invalid ApprovalManager for durable plan review")
            requested = request_decision(
                edict=edict,
                memorial=memorial,
                plan=plan,
                revision=1,
                scheduled_event_id=event.event_id,
                scheduled_event_hash=canonical_sha256(event),
            )
            memorial.status = _TS.NEEDS_REVIEW
            self._storage.update_memorial(memorial)
            await self._bus.emit(
                make_event(
                    "plan.pending_review",
                    edict_id=edict.id,
                    memorial_id=memorial_id,
                    producer="planner",
                    payload={
                        **payload,
                        "decision_request_id": requested.decision_request_id,
                        "revision": requested.payload["revision"],
                        "plan_hash": requested.payload["plan_hash"],
                    },
                )
            )

        else:
            if memorial_id is not None:
                self.persist_plan_revision(
                    memorial_id=memorial_id,
                    plan=plan,
                    parent_revision_id=None,
                    reason_code="initial_plan",
                    reason_summary="initial plan created",
                    scheduled_event_id=event.event_id,
                    scheduled_event_hash=canonical_sha256(event),
                )
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

    def _is_retained_scheduled_replay(self, event: EventEnvelope) -> bool:
        """Recognize only the exact durable scheduled envelope, without re-emitting work."""

        if event.memorial_id is None:
            return False
        with self._storage.unit_of_work() as unit_of_work:
            state = self._storage.run_state_repo.load(
                unit_of_work.connection,
                event.memorial_id,
            )
            unit_of_work.commit()
        if state is None:
            return False
        continuation = state.continuation
        if not isinstance(continuation, AgentContinuationV1) or not continuation.plan_revisions:
            raise RuntimeError("scheduled event conflicts with an existing RunState")
        if continuation.scheduled_event_id is None:
            raise RuntimeError("scheduled event conflicts with an unbound plan lineage")
        if (
            continuation.scheduled_event_id != event.event_id
            or continuation.scheduled_event_hash != canonical_sha256(event)
        ):
            raise RuntimeError("scheduled event identity conflict")
        Plan.model_validate(continuation.plan_snapshot)
        return True

    async def plan_attempt(self, authority: AttemptAuthority) -> ManagedPlanningResult:
        """Plan inside a dispatcher-owned attempt, without an event hand-off."""
        memorial = self._storage.get_memorial(authority.memorial_id)
        if memorial is None or memorial.dag_node_id is not None:
            raise RuntimeError("managed planning root is unavailable")
        edict = self._storage.get_edict(memorial.edict_id)
        if edict is None:
            raise RuntimeError("managed planning edict is unavailable")

        with self._storage.unit_of_work() as unit_of_work:
            run_state = self._storage.run_state_repo.load(
                unit_of_work.connection,
                memorial.id,
            )
            decision_record: DecisionRecordV1 | None = None
            durable_plan: Plan | None = None
            plan_continuation: AgentContinuationV1 | None = None
            if (
                run_state is not None
                and isinstance(run_state.continuation, AgentContinuationV1)
                and run_state.continuation.plan_revisions
                and run_state.continuation.plan_snapshot is not None
            ):
                plan_continuation = run_state.continuation
                durable_plan = Plan.model_validate(run_state.continuation.plan_snapshot)
                decision_id = (
                    plan_continuation.resolved_decision_id
                    or plan_continuation.pending_decision_id
                )
                if decision_id is not None:
                    try:
                        decision_record = self._storage.decision_repo.get(
                            unit_of_work.connection,
                            decision_id,
                        )
                    except DecisionDecodeError as exc:
                        raise RuntimeError("plan review approval authority is malformed") from exc
            unit_of_work.commit()
        if durable_plan is not None and plan_continuation is not None:
            requires_authority = (
                edict.plan_review
                or plan_continuation.pending_decision_id is not None
                or plan_continuation.resolved_decision_id is not None
            )
            if requires_authority:
                durable_plan = self._require_approved_plan_review(
                    memorial=memorial,
                    edict=edict,
                    continuation=plan_continuation,
                    record=decision_record,
                )
        if (
            durable_plan is not None
            and run_state is not None
            and run_state.phase
            in {
                RunPhase.PLANNING,
                RunPhase.EXECUTING,
                RunPhase.PAUSED,
            }
        ):
            return ManagedPlanningResult(plan=durable_plan)

        if memorial.status in {TaskStatus.SUBMITTED, TaskStatus.SCHEDULED}:
            memorial.status = TaskStatus.PLANNING
            self._storage.update_memorial(memorial)
        plan = await self.plan(edict)
        if not edict.plan_review or not plan.tasks:
            self.persist_plan_revision(
                memorial_id=memorial.id,
                plan=plan,
                parent_revision_id=None,
                reason_code="initial_plan",
                reason_summary="initial plan created",
            )
            return ManagedPlanningResult(plan=plan)
        if self._approval_manager is None:
            raise RuntimeError("durable plan review requires ApprovalManager")
        requested = self._approval_manager.request_plan_review_decision(
            edict=edict,
            memorial=memorial,
            plan=plan,
            revision=1,
        )
        memorial.status = TaskStatus.NEEDS_REVIEW
        self._storage.update_memorial(memorial)
        return ManagedPlanningResult(
            plan=plan,
            suspended=True,
            decision_request_id=requested.decision_request_id,
        )

    @staticmethod
    def _require_approved_plan_review(
        *,
        memorial: Memorial,
        edict: Edict,
        continuation: AgentContinuationV1,
        record: DecisionRecordV1 | None,
    ) -> Plan:
        """Return only the exact canonical plan authorized by a durable approval."""

        persisted_plan = record.request.payload.get("plan") if record is not None else None
        current_revision = continuation.plan_revisions[-1]
        if (
            record is None
            or record.request.kind is not DecisionKind.PLAN_REVIEW
            or record.request.memorial_id != memorial.id
            or record.request.edict_id != edict.id
            or record.request.decision_request_id != continuation.resolved_decision_id
            or continuation.pending_decision_id is not None
            or record.request.status is not DecisionStatus.RESOLVED
            or record.resolution is None
            or record.resolution.action != "approve"
            or not isinstance(persisted_plan, dict)
            or persisted_plan != continuation.plan_snapshot
            or record.request.payload.get("plan_ref") != continuation.plan_ref
            or record.request.payload.get("plan_hash") != continuation.plan_hash
            or record.request.payload.get("plan_revision_id") != continuation.plan_revision_id
            or record.request.payload.get("artifact_digest") != current_revision.artifact_digest
            or canonical_sha256(persisted_plan) != continuation.plan_hash
        ):
            raise RuntimeError("plan review approval authority is missing or invalid")
        return Plan.model_validate(persisted_plan)

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
                t.task_id,
                old_id,
                t.assigned_official,
            )

        # Log final assignments for debugging
        assignments = {t.task_id: t.assigned_official for t in tasks}
        logger.info("Plan assignments finalized: %s", assignments)
