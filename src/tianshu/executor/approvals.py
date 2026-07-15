"""Approval (decree) management — T3 real-time approval via asyncio.Event."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.application.ingress import (
    make_ingress_auth_context,
    requested_contract_for_edict,
)
from tianshu.bus.event_bus import EventBus
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.common import TaskStatus, UsageSummary
from tianshu.models.decision import (
    DecisionKind,
    DecisionRecordV1,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.decree import Decree
from tianshu.models.edict import Edict, title_from_goal
from tianshu.models.events import EventEnvelope, make_event
from tianshu.models.memorial import Memorial
from tianshu.models.plan import Plan
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_state import (
    AgentContinuationV1,
    IterationSummaryV1,
    OuterLoopContinuationV1,
    PersistedChatMessageV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
    ToolProposalV1,
)
from tianshu.security.sensitive_payload import redact_sensitive_mapping
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from tianshu.tools.policy_store import SessionRuleStore

APPROVAL_TIMEOUT = 300.0  # 5 minutes


class ApprovalManager:
    """Manages approval workflow for memorials that need human review."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        session_rule_store: SessionRuleStore | None = None,
        edict_application_service: EdictApplicationService | None = None,
        decision_service: DecisionService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._session_rule_store = session_rule_store
        self._edict_application = edict_application_service or EdictApplicationService(storage)
        self._decision_service = decision_service
        self._outbox = OutboxRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pending: dict[str, asyncio.Event] = {}
        self._results: dict[str, Decree] = {}
        # Spec Section 4: 记录 wait_for_approval 时的 tool_name，方便 _handle_approve 生成 session rule
        self._pending_tool: dict[str, str] = {}

    async def on_before_tool_call(self, **context: object) -> object:
        """Deprecated pre-Step-2 entry point.

        入口判断已迁移到 PolicyHook（Spec Section 3 规则 4 ApprovalRequiredListRule）。
        保留方法签名以兼容已有 HookRegistry 注册，但直接返回 None 放行。

        实时审批的 wait_for_approval / submit_decree 仍然由本类提供，
        由 PolicyHook 在 require_approval 分支里直接调用。
        """
        return None

    def request_tool_decision(
        self,
        *,
        edict: Edict,
        memorial: Memorial,
        invocation_id: str,
        tool_name: str,
        tool_args: dict[str, object],
        tool_tier: str,
        policy_rule_id: str | None,
        messages: list[dict],
        iteration: int,
        usage: UsageSummary,
    ) -> DecisionRequestV1:
        """Persist one tool decision and its pre-effect Agent continuation atomically."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable tool decisions")
        now = self._clock().astimezone(UTC)
        safe_arguments = cast(
            dict[str, JsonValue],
            redact_sensitive_mapping(tool_args),
        )
        persisted_messages = tuple(self._persist_message(message) for message in messages)
        persisted_usage = PersistedUsageSummaryV1.model_validate(usage.model_dump(mode="python"))
        continuation = AgentContinuationV1(
            messages=persisted_messages,
            pending_tool=ToolProposalV1(
                tool_call_id=invocation_id,
                tool_name=tool_name,
                arguments=safe_arguments,
                arguments_hash=canonical_sha256(safe_arguments),
                tool_tier=tool_tier,
                policy_rule_id=policy_rule_id,
                proposed_at=now,
            ),
            iteration=iteration,
            usage=persisted_usage,
            checkpoint_ref=None,
            resolved_decision_id=None,
            side_effect_cursor=0,
        )
        run_state = RunStateV1(
            memorial_id=memorial.id,
            edict_id=edict.id,
            phase=RunPhase.WAITING_DECISION,
            continuation=continuation,
            checkpoint_ref=None,
            side_effect_cursor=0,
            version=1,
            created_at=now,
            updated_at=now,
        )
        command = RequestDecisionCommand(
            kind=DecisionKind.TOOL,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key=invocation_id,
            payload={
                "schema_version": 1,
                "tool_name": tool_name,
                "arguments": safe_arguments,
                "tool_tier": tool_tier,
                "policy_rule_id": policy_rule_id,
            },
            expires_at=now + timedelta(seconds=APPROVAL_TIMEOUT),
        )
        return self._decision_service.request_with_run_state(
            command,
            run_state,
            auth=self._policy_auth(invocation_id),
        )

    def resolve_tool_decision_as_silijian(
        self,
        request: DecisionRequestV1,
        *,
        reason: str,
    ) -> DecisionResolutionV1:
        """Resolve an already-durable tool request through fixed service identity."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable tool decisions")
        return self._decision_service.resolve(
            request.decision_request_id,
            ResolveDecisionCommand(
                action="approve",
                reason=reason,
                payload={"schema_version": 1, "grant_scope": "once", "grant_reason": reason},
                expected_version=request.version,
            ),
            auth=self._silijian_auth(request.decision_request_id),
        )

    async def wait_for_tool_decision(
        self,
        decision_request_id: str,
        *,
        timeout_seconds: float = APPROVAL_TIMEOUT,
        poll_interval_seconds: float = 0.1,
    ) -> DecisionResolutionV1 | None:
        """Poll durable authority; no process-local waiter is required."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable tool decisions")
        service = self._decision_service

        async def poll() -> DecisionResolutionV1 | None:
            while True:
                record = service.get(decision_request_id)
                if record is None:
                    return None
                if record.request.status is DecisionStatus.PENDING:
                    await asyncio.sleep(poll_interval_seconds)
                    continue
                return self._consume_tool_decision_terminal(record)

        try:
            return await asyncio.wait_for(poll(), timeout=timeout_seconds)
        except TimeoutError:
            service.expire_due()
            record = service.get(decision_request_id)
            return self._consume_tool_decision_terminal(record)

    def _consume_tool_decision_terminal(
        self,
        record: DecisionRecordV1 | None,
    ) -> DecisionResolutionV1 | None:
        if record is None or record.request.status is DecisionStatus.PENDING:
            return None
        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable tool decisions")
        decision_request_id = record.request.decision_request_id
        if record.request.status is DecisionStatus.RESOLVED:
            self._decision_service.mark_run_state_resolved(decision_request_id)
            try:
                self._project_tool_decree(record)
            except Exception:
                logger.exception(
                    "tool decision %s committed but Decree projection failed",
                    decision_request_id,
                )
            return record.resolution
        self._decision_service.mark_run_state_terminal(decision_request_id)
        return None

    async def handle_decision_resolved(self, event: EventEnvelope) -> None:
        """Replay one-way compatibility projections after generic resolution."""

        kind = event.payload.get("kind")
        if kind not in {
            DecisionKind.TOOL.value,
            DecisionKind.OUTER_LOOP.value,
            DecisionKind.PLAN_REVIEW.value,
        }:
            return
        decision_request_id = event.payload.get("decision_request_id")
        if not isinstance(decision_request_id, str) or self._decision_service is None:
            raise ValueError("resolved decision event is missing durable identity")
        record = self._decision_service.get(decision_request_id)
        if record is None or record.resolution is None:
            raise ValueError("resolved decision is unavailable")
        if record.request.kind.value != kind:
            raise ValueError("resolved decision kind does not match durable authority")
        if kind == DecisionKind.TOOL.value:
            await self._project_tool_resolution(record)
            return
        self._decision_service.mark_run_state_resolved(decision_request_id)
        if kind == DecisionKind.PLAN_REVIEW.value:
            await self._project_plan_review(record)

    async def handle_decision_expired(self, event: EventEnvelope) -> None:
        """Release durable suspensions and fail closed plan reviews after expiry."""

        kind = event.payload.get("kind")
        if kind not in {
            DecisionKind.TOOL.value,
            DecisionKind.OUTER_LOOP.value,
            DecisionKind.PLAN_REVIEW.value,
        }:
            return
        decision_request_id = event.payload.get("decision_request_id")
        if not isinstance(decision_request_id, str) or self._decision_service is None:
            raise ValueError("expired decision event is missing durable identity")
        record = self._decision_service.get(decision_request_id)
        if record is None or record.request.kind.value != kind:
            raise ValueError("expired decision kind does not match durable authority")
        state = self._decision_service.mark_run_state_terminal(decision_request_id)
        if kind != DecisionKind.PLAN_REVIEW.value:
            return
        memorial = self._storage.get_memorial(state.memorial_id)
        if memorial is None:
            raise ValueError("expired plan review is unavailable")
        if memorial.status in {TaskStatus.NEEDS_REVIEW, TaskStatus.PLANNING}:
            memorial.status = TaskStatus.FAILED
            memorial.error = "规划审批已超时"
            self._storage.update_memorial(memorial)
        self._storage.append_event_envelope(
            EventEnvelope(
                event_id=f"{decision_request_id}:plan.review_expired",
                event_type="plan.review_expired",
                edict_id=record.request.edict_id,
                memorial_id=record.request.memorial_id,
                producer="approval_manager.plan_review_projection.v1",
                timestamp=record.request.updated_at,
                payload={"decision_request_id": decision_request_id},
            )
        )

    @staticmethod
    def _policy_auth(invocation_id: str) -> AuthContext:
        correlation = hashlib.sha256(invocation_id.encode("utf-8")).hexdigest()[:32]
        return AuthContext(
            principal=Principal(
                id="system:policy-hook",
                kind=PrincipalKind.SERVICE,
                display_name="Policy Hook",
                scopes=frozenset({"decision:request"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id=f"tool:{correlation}",
        )

    @staticmethod
    def _silijian_auth(decision_request_id: str) -> AuthContext:
        correlation = hashlib.sha256(decision_request_id.encode("utf-8")).hexdigest()[:32]
        return AuthContext(
            principal=Principal(
                id="system:silijian",
                kind=PrincipalKind.SERVICE,
                display_name="Silijian",
                scopes=frozenset({"decision:resolve"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id=f"silijian:{correlation}",
        )

    @staticmethod
    def _legacy_tool_adapter_auth(decision_request_id: str) -> AuthContext:
        correlation = hashlib.sha256(decision_request_id.encode("utf-8")).hexdigest()[:32]
        return AuthContext(
            principal=Principal(
                id="system:legacy-tool-adapter",
                kind=PrincipalKind.SERVICE,
                display_name="Legacy Tool Adapter",
                scopes=frozenset({"decision:resolve"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id=f"legacy-tool:{correlation}",
        )

    @staticmethod
    def _outer_loop_auth(edict_id: str) -> AuthContext:
        correlation = hashlib.sha256(edict_id.encode("utf-8")).hexdigest()[:32]
        return AuthContext(
            principal=Principal(
                id="system:outer-loop",
                kind=PrincipalKind.SERVICE,
                display_name="Outer Loop",
                scopes=frozenset({"decision:request"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id=f"outer-loop:{correlation}",
        )

    @staticmethod
    def _plan_review_auth(plan_ref: str) -> AuthContext:
        correlation = hashlib.sha256(plan_ref.encode("utf-8")).hexdigest()[:32]
        return AuthContext(
            principal=Principal(
                id="system:planner",
                kind=PrincipalKind.SERVICE,
                display_name="Planner",
                scopes=frozenset({"decision:request"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id=f"plan-review:{correlation}",
        )

    @staticmethod
    def _redacted_text(value: str | None) -> str | None:
        if value is None:
            return None
        safe = cast(str, redact_sensitive_mapping({"value": value})["value"])
        return "[REDACTED]" if "[REDACTED" in safe else safe

    @staticmethod
    def _outer_loop_human_decision(resolution: DecisionResolutionV1) -> object:
        from tianshu.executor.orchestrator.human_decision import HumanDecision
        from tianshu.models.acceptance import AcceptanceCriteria

        acceptance = resolution.payload.get("acceptance")
        return HumanDecision(
            action=cast(
                Literal["continue", "accept_as_is", "abort", "modify_acceptance"],
                resolution.action,
            ),
            feedback=cast(str | None, resolution.payload.get("feedback")),
            new_acceptance=(
                AcceptanceCriteria.model_validate(acceptance)
                if isinstance(acceptance, dict)
                else None
            ),
        )

    @staticmethod
    def _persist_message(message: dict) -> PersistedChatMessageV1:
        safe_message = redact_sensitive_mapping(message)
        return PersistedChatMessageV1.model_validate_json(canonical_json_bytes(safe_message))

    def _project_tool_decree(self, record: DecisionRecordV1) -> Decree | None:
        if record.request.kind is not DecisionKind.TOOL or record.resolution is None:
            return None
        resolution = record.resolution
        action = cast(Literal["approve", "reject", "guide"], resolution.action)
        comment = (
            str(resolution.payload["guidance"])
            if action == "guide" and "guidance" in resolution.payload
            else resolution.reason
        )
        decree = Decree(
            id=record.request.decision_request_id,
            memorial_id=record.request.memorial_id,
            action=action,
            comment=comment,
            actor=resolution.actor_principal_id,
            created_at=resolution.resolved_at,
            grant_scope=cast(
                Literal["once", "edict", "always"] | None,
                resolution.payload.get("grant_scope"),
            ),
            grant_reason=cast(str | None, resolution.payload.get("grant_reason")),
        )
        self._storage.save_decree_if_absent(decree)
        return decree

    async def _project_tool_resolution(self, record: DecisionRecordV1) -> None:
        """Build replay-safe legacy Decree, event, rule, and local wake projections."""

        decree = self._project_tool_decree(record)
        if decree is None or record.resolution is None:
            return
        request = record.request
        resolution = record.resolution
        tool_name = str(request.payload.get("tool_name") or "")
        event_type = {
            "approve": "decree.approved",
            "guide": "decree.guided",
            "reject": "decree.rejected",
        }[resolution.action]
        projected = EventEnvelope(
            event_id=f"{request.decision_request_id}:{event_type}",
            event_type=event_type,
            edict_id=request.edict_id,
            memorial_id=request.memorial_id,
            producer="approval_manager.tool_projection.v1",
            timestamp=resolution.resolved_at,
            payload={
                "decision_request_id": request.decision_request_id,
                "decree_id": decree.id,
                "comment": decree.comment,
                "mid_execution": True,
                "tool_name": tool_name,
                "grant_scope": decree.grant_scope,
                "requested_grant_scope": resolution.payload.get("requested_grant_scope"),
                "grant_downgraded": resolution.payload.get("grant_downgraded", False),
                "grant_downgrade_reason": resolution.payload.get("grant_downgrade_reason"),
                "actor": resolution.actor_principal_id,
            },
        )
        if self._storage.append_event_envelope(projected):
            await self._bus.emit(projected)
        await self._project_tool_session_rule(record, decree)
        self._results[request.memorial_id] = decree
        event = self._pending.get(request.memorial_id)
        if event is not None:
            event.set()

    async def _project_tool_session_rule(
        self,
        record: DecisionRecordV1,
        decree: Decree,
    ) -> None:
        if (
            self._session_rule_store is None
            or record.resolution is None
            or record.resolution.action != "approve"
            or decree.grant_scope not in {"edict", "always"}
        ):
            return
        from tianshu.tools.policy_store import SessionRule, compute_fingerprint

        tool_name = str(record.request.payload.get("tool_name") or "")
        arguments = record.request.payload.get("arguments")
        safe_arguments = cast(dict, arguments) if isinstance(arguments, dict) else {}
        granted_at = record.resolution.resolved_at
        scope = cast(Literal["edict", "always"], decree.grant_scope)
        rule = SessionRule(
            rule_id=f"{record.request.decision_request_id}:session-rule",
            tool_name=tool_name,
            arg_fingerprint=compute_fingerprint(tool_name, safe_arguments),
            scope=scope,
            edict_id=(record.request.edict_id if scope == "edict" else None),
            granted_at=granted_at,
            granted_by_decree_id=decree.id,
            source="approval",
            reason=decree.grant_reason or f"granted by decision {decree.id}",
            expires_at=(granted_at + timedelta(days=30) if scope == "always" else None),
        )
        await self._session_rule_store.create(rule)
        self._storage.append_event_envelope(
            EventEnvelope(
                event_id=f"{record.request.decision_request_id}:policy.session_rule_created",
                event_type="policy.session_rule_created",
                edict_id=record.request.edict_id,
                memorial_id=record.request.memorial_id,
                producer="approval_manager.tool_projection.v1",
                timestamp=granted_at,
                payload={
                    "decision_request_id": record.request.decision_request_id,
                    "rule_id": rule.rule_id,
                    "tool_name": tool_name,
                    "scope": rule.scope,
                    "arg_fingerprint": rule.arg_fingerprint,
                    "decree_id": decree.id,
                },
            )
        )

    async def _project_plan_review(self, record: DecisionRecordV1) -> None:
        if record.request.kind is not DecisionKind.PLAN_REVIEW or record.resolution is None:
            return
        resolution = record.resolution
        if resolution.action not in {"approve", "reject"}:
            raise ValueError("unsupported plan review projection")
        memorial = self._storage.get_memorial(record.request.memorial_id)
        if memorial is None:
            raise ValueError("plan review memorial is unavailable")
        plan = record.request.payload.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("plan review payload is unavailable")
        event_type = "plan.approved" if resolution.action == "approve" else "plan.rejected"
        projected = EventEnvelope(
            event_id=f"{record.request.decision_request_id}:{event_type}",
            event_type=event_type,
            edict_id=record.request.edict_id,
            memorial_id=record.request.memorial_id,
            producer="approval_manager.plan_review_projection.v1",
            timestamp=resolution.resolved_at,
            payload={
                "actor": resolution.actor_principal_id,
                "decision_request_id": record.request.decision_request_id,
                "plan": plan,
            },
        )
        self._storage.append_event_envelope(projected)
        if resolution.action == "reject":
            if memorial.status in {TaskStatus.NEEDS_REVIEW, TaskStatus.PLANNING}:
                memorial.status = TaskStatus.FAILED
                memorial.error = "规划方案被驳回"
                self._storage.update_memorial(memorial)
            return
        if memorial.status is TaskStatus.NEEDS_REVIEW:
            memorial.status = TaskStatus.PLANNING
            self._storage.update_memorial(memorial)
        completed = EventEnvelope(
            event_id=f"{record.request.decision_request_id}:plan.completed",
            event_type="plan.completed",
            edict_id=record.request.edict_id,
            memorial_id=record.request.memorial_id,
            producer="approval_manager.plan_review_projection.v1",
            timestamp=resolution.resolved_at,
            payload={
                "plan": plan,
                "decision_request_id": record.request.decision_request_id,
            },
        )
        self._enqueue_plan_completed(completed)

    def _enqueue_plan_completed(self, event: EventEnvelope) -> None:
        """Persist stable plan-completion work for dispatcher-owned delivery."""

        expected_payload = canonical_json_bytes(event.payload).decode("utf-8")
        expected_aggregate = "edict" if event.edict_id is not None else "system"
        with self._storage.unit_of_work() as unit_of_work:
            existing = self._outbox.get(unit_of_work.connection, event.event_id)
            if existing is None:
                self._outbox.add(unit_of_work.connection, event)
            elif (
                existing.event_type != event.event_type
                or existing.aggregate_type != expected_aggregate
                or existing.edict_id != event.edict_id
                or existing.memorial_id != event.memorial_id
                or existing.producer != event.producer
                or existing.payload_json != expected_payload
                or existing.occurred_at != event.timestamp.isoformat()
            ):
                raise ValueError("outbox event identity conflicts with durable work")
            unit_of_work.commit()

    async def wait_for_approval(
        self,
        memorial_id: str,
        tool_name: str,
    ) -> Decree | None:
        """Block until a decree is submitted for this memorial, or timeout."""
        evt = asyncio.Event()
        self._pending[memorial_id] = evt
        self._pending_tool[memorial_id] = tool_name

        logger.info(
            "Waiting for approval on memorial %s (tool: %s)",
            memorial_id,
            tool_name,
        )

        try:
            await asyncio.wait_for(evt.wait(), timeout=APPROVAL_TIMEOUT)
            return self._results.pop(memorial_id, None)
        except TimeoutError:
            logger.warning(
                "Approval timeout for memorial %s, auto-rejecting",
                memorial_id,
            )
            return None
        finally:
            self._pending.pop(memorial_id, None)
            self._pending_tool.pop(memorial_id, None)

    # --- 长任务 outer loop L3 审批接口（独立于 tool-call 审批）---

    def request_outer_loop_decision(
        self,
        *,
        edict: Edict,
        memorial: Memorial,
        state: object,
        checkpoint_ref: str | None,
        side_effect_cursor: int,
        timeout_seconds: float = 86400.0,
    ) -> DecisionRequestV1:
        """Persist one reconstruction-grade L3 suspension atomically."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable outer-loop decisions")
        from tianshu.executor.orchestrator.state import OuterLoopState

        if (
            not isinstance(state, OuterLoopState)
            or state.edict_id != edict.id
            or state.current_level != "L3"
        ):
            raise ValueError("invalid outer-loop state")
        now = self._clock().astimezone(UTC)
        history: list[IterationSummaryV1] = []
        for record in state.history:
            record_usage = (
                record.critic_result.usage
                if record.critic_result is not None and record.critic_result.usage is not None
                else UsageSummary()
            )
            usage = PersistedUsageSummaryV1.model_validate(
                record_usage.model_copy(update={"cost_cny": record.cost_cny}).model_dump(
                    mode="python"
                )
            )
            history.append(
                IterationSummaryV1(
                    iteration=record.iteration,
                    level=record.level,
                    output_artifact_ref=None,
                    critic_verdict=(
                        record.critic_result.verdict if record.critic_result is not None else None
                    ),
                    critic_issue_class=self._redacted_text(
                        record.critic_result.issue_class
                        if record.critic_result is not None
                        else None
                    ),
                    feedback=self._redacted_text(
                        record.critic_result.feedback if record.critic_result is not None else None
                    ),
                    usage=usage,
                    completed_at=record.finished_at,
                )
            )
        last = state.history[-1] if state.history else None
        continuation = OuterLoopContinuationV1(
            level=state.current_level,
            iteration=state.iteration,
            best_output=self._redacted_text(last.actor_output if last is not None else None),
            feedback=self._redacted_text(
                last.critic_result.feedback
                if last is not None and last.critic_result is not None
                else None
            ),
            steer=self._redacted_text(state.steer_note),
            history=tuple(history),
            same_issue_streak=state.same_issue_streak,
            last_critic_issue_class=self._redacted_text(state.last_critic_issue_class),
            l1_rounds_used=state.l1_rounds_used,
            l2_rounds_used=state.l2_rounds_used,
            consultation_advice=self._redacted_text(state.consultation_advice),
            usage=PersistedUsageSummaryV1.model_validate(memorial.usage.model_dump(mode="python")),
            total_cost_cny=Decimal(str(state.total_cost_cny)),
            checkpoint_ref=checkpoint_ref,
            resolved_decision_id=None,
            side_effect_cursor=side_effect_cursor,
        )
        run_state = RunStateV1(
            memorial_id=memorial.id,
            edict_id=edict.id,
            phase=RunPhase.WAITING_DECISION,
            continuation=continuation,
            checkpoint_ref=checkpoint_ref,
            side_effect_cursor=side_effect_cursor,
            version=1,
            created_at=now,
            updated_at=now,
        )
        payload = {
            "schema_version": 1,
            **continuation.model_dump(
                mode="json",
                exclude={"pending_decision_id", "resolved_decision_id"},
            ),
        }
        command = RequestDecisionCommand(
            kind=DecisionKind.OUTER_LOOP,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key=f"outer-loop:{state.current_level}:{state.iteration}",
            payload=cast(dict[str, JsonValue], payload),
            expires_at=now + timedelta(seconds=timeout_seconds),
        )
        return self._decision_service.request_with_run_state(
            command,
            run_state,
            auth=self._outer_loop_auth(edict.id),
        )

    async def wait_for_outer_loop_decision(
        self,
        decision_request_id: str,
        timeout_seconds: float = 86400.0,
        poll_interval_seconds: float = 0.1,
    ) -> object | None:
        """Poll durable outer-loop authority and return its compatibility decision."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable outer-loop decisions")
        service = self._decision_service

        async def poll() -> DecisionRecordV1 | None:
            while True:
                record = service.get(decision_request_id)
                if record is None or record.request.kind is not DecisionKind.OUTER_LOOP:
                    return None
                if record.request.status is not DecisionStatus.PENDING:
                    return record
                await asyncio.sleep(poll_interval_seconds)

        try:
            record = await asyncio.wait_for(poll(), timeout=timeout_seconds)
        except TimeoutError:
            service.expire_due()
            record = service.get(decision_request_id)
        if record is None or record.request.status is DecisionStatus.PENDING:
            return None
        if record.request.status is not DecisionStatus.RESOLVED or record.resolution is None:
            service.mark_run_state_terminal(decision_request_id)
            return None
        service.mark_run_state_resolved(decision_request_id)
        return self._outer_loop_human_decision(record.resolution)

    def submit_outer_loop_decision(
        self,
        edict_id: str,
        decision: object,
        *,
        auth: AuthContext,
    ) -> bool:
        """Resolve the latest durable outer-loop request for one Edict."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable outer-loop decisions")
        request = next(
            (
                item
                for item in reversed(
                    self._decision_service.list_pending(kind=DecisionKind.OUTER_LOOP)
                )
                if item.edict_id == edict_id
            ),
            None,
        )
        if request is None:
            return False
        from tianshu.executor.orchestrator.human_decision import HumanDecision

        parsed = (
            decision
            if isinstance(decision, HumanDecision)
            else HumanDecision.model_validate(decision)
        )
        payload: dict[str, JsonValue] = {"schema_version": 1}
        if parsed.action == "continue":
            payload["feedback"] = parsed.feedback
        elif parsed.action == "modify_acceptance":
            if parsed.new_acceptance is None:
                return False
            payload["acceptance"] = cast(
                dict[str, JsonValue], parsed.new_acceptance.model_dump(mode="json")
            )
        self._decision_service.resolve(
            request.decision_request_id,
            ResolveDecisionCommand(
                action=parsed.action,
                reason="outer-loop decision",
                payload=payload,
                expected_version=request.version,
            ),
            auth=auth,
        )
        return True

    def list_pending_outer_loop(self) -> list[dict]:
        """List restart-visible outer-loop requests for compatibility UIs."""

        if self._decision_service is None:
            return []
        return [
            {
                "decision_request_id": request.decision_request_id,
                "edict_id": request.edict_id,
                **request.payload,
            }
            for request in self._decision_service.list_pending(kind=DecisionKind.OUTER_LOOP)
        ]

    def request_plan_review_decision(
        self,
        *,
        edict: Edict,
        memorial: Memorial,
        plan: Plan,
        revision: int,
        timeout_seconds: float = 86400.0,
    ) -> DecisionRequestV1:
        """Persist a canonical plan and its explicit non-tool waiting continuation."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable plan review")
        if memorial.edict_id != edict.id or revision < 1:
            raise ValueError("invalid plan review identity")
        now = self._clock().astimezone(UTC)
        safe_plan = cast(
            dict[str, JsonValue],
            redact_sensitive_mapping(plan.model_dump(mode="json")),
        )
        plan_hash = canonical_sha256(safe_plan)
        plan_ref = f"plan:{edict.id}:{revision}"
        continuation = AgentContinuationV1(
            messages=(),
            pending_tool=None,
            iteration=0,
            usage=PersistedUsageSummaryV1.model_validate(memorial.usage.model_dump(mode="python")),
            checkpoint_ref=plan_ref,
            resolved_decision_id=None,
            side_effect_cursor=0,
            plan_ref=plan_ref,
            plan_hash=plan_hash,
        )
        run_state = RunStateV1(
            memorial_id=memorial.id,
            edict_id=edict.id,
            phase=RunPhase.WAITING_DECISION,
            continuation=continuation,
            checkpoint_ref=plan_ref,
            side_effect_cursor=0,
            version=1,
            created_at=now,
            updated_at=now,
        )
        command = RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key=f"plan-review:{revision}",
            payload={
                "schema_version": 1,
                "revision": revision,
                "plan_ref": plan_ref,
                "plan_hash": plan_hash,
                "plan": safe_plan,
            },
            expires_at=now + timedelta(seconds=timeout_seconds),
        )
        return self._decision_service.request_with_run_state(
            command,
            run_state,
            auth=self._plan_review_auth(plan_ref),
        )

    def list_pending_plan_reviews(self) -> list[DecisionRequestV1]:
        if self._decision_service is None:
            return []
        return self._decision_service.list_pending(kind=DecisionKind.PLAN_REVIEW)

    def submit_plan_review_decision(
        self,
        edict_id: str,
        *,
        action: Literal["approve", "reject"],
        auth: AuthContext,
    ) -> DecisionResolutionV1:
        """Resolve the latest pending plan review through authenticated authority."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable plan review")
        if action not in {"approve", "reject"}:
            raise ValueError("unsupported plan review action")
        request = next(
            (
                item
                for item in reversed(self.list_pending_plan_reviews())
                if item.edict_id == edict_id
            ),
            None,
        )
        if request is None:
            raise ValueError("no pending plan review")
        return self._decision_service.resolve(
            request.decision_request_id,
            ResolveDecisionCommand(
                action=action,
                reason="plan review",
                payload={"schema_version": 1},
                expected_version=request.version,
            ),
            auth=auth,
        )

    def list_pending_tool_calls(self) -> list[dict]:
        """Project restart-visible pending TOOL decisions from durable authority."""

        if self._decision_service is None:
            return []
        return [
            {
                "decision_request_id": request.decision_request_id,
                "memorial_id": request.memorial_id,
                "edict_id": request.edict_id,
                "tool_name": request.payload.get("tool_name"),
                "rule_id": request.payload.get("policy_rule_id"),
                "reason": None,
                "tool_tier": request.payload.get("tool_tier"),
                "args_summary": request.payload.get("arguments") or {},
                "created_at": request.created_at.isoformat(),
            }
            for request in self._decision_service.list_pending(kind=DecisionKind.TOOL)
        ]

    def pending_tool_decision_id_for_memorial(self, memorial_id: str) -> str:
        """Resolve the legacy memorial alias only when it is unambiguous."""

        matches = [
            item["decision_request_id"]
            for item in self.list_pending_tool_calls()
            if item["memorial_id"] == memorial_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one pending tool decision for memorial '{memorial_id}'"
            )
        return cast(str, matches[0])

    async def resolve_tool_decision(
        self,
        decision_request_id: str,
        action: Literal["approve", "reject", "guide"],
        *,
        comment: str | None = None,
        grant_scope: Literal["once", "edict", "always"] | None = None,
        grant_reason: str | None = None,
        auth: AuthContext,
    ) -> DecisionRecordV1:
        """Resolve durable TOOL authority, then emit deterministic compatibility views."""

        if self._decision_service is None:
            raise RuntimeError("DecisionService is required for durable tool decisions")
        if action not in {"approve", "reject", "guide"}:
            raise ValueError("unsupported tool decision action")
        if action != "approve" and grant_scope is not None:
            raise ValueError("grant_scope is only valid for approval")
        if action == "guide" and (comment is None or not comment.strip()):
            raise ValueError("guide requires non-blank comment")
        if action == "approve" and grant_scope not in {None, "once", "edict", "always"}:
            raise ValueError("unsupported grant_scope")

        service = self._decision_service
        existing = service.get(decision_request_id)
        if existing is None:
            raise ValueError(f"Tool decision '{decision_request_id}' not found")
        if existing.request.kind is not DecisionKind.TOOL:
            raise ValueError("decision is not a tool decision")
        if existing.request.status is DecisionStatus.RESOLVED:
            try:
                await self._project_tool_resolution(existing)
            except Exception:
                logger.exception(
                    "tool decision %s resolved but compatibility projection failed",
                    decision_request_id,
                )
            return existing

        requested_scope = grant_scope or "once"
        effective_scope = requested_scope
        downgrade_reason: str | None = None
        if action == "approve" and effective_scope == "always":
            from tianshu.tools.policy_store import assert_can_grant

            try:
                assert_can_grant(str(existing.request.payload.get("tool_name") or ""), "always")
            except ValueError as exc:
                effective_scope = "once"
                downgrade_reason = str(exc)
        payload: dict[str, JsonValue] = {"schema_version": 1}
        if action == "approve":
            payload.update(
                grant_scope=effective_scope,
                grant_reason=grant_reason,
                requested_grant_scope=requested_scope,
                grant_downgraded=downgrade_reason is not None,
                grant_downgrade_reason=downgrade_reason,
            )
        elif action == "guide":
            payload["guidance"] = cast(str, comment).strip()
        try:
            service.resolve(
                decision_request_id,
                ResolveDecisionCommand(
                    action=action,
                    reason=(comment or grant_reason or "tool decision").strip(),
                    payload=payload,
                    expected_version=existing.request.version,
                ),
                auth=auth,
            )
        except DecisionConflict:
            winner = service.get(decision_request_id)
            if winner is None or winner.resolution is None:
                raise
        record = service.get(decision_request_id)
        if record is None or record.resolution is None:
            raise RuntimeError("durable tool resolution is unavailable")
        try:
            await self._project_tool_resolution(record)
        except Exception:
            logger.exception(
                "tool decision %s committed but compatibility projection failed",
                decision_request_id,
            )
        return record

    async def submit_tool_decision(
        self,
        memorial_id: str,
        action: Literal["approve", "reject", "guide"],
        *,
        comment: str | None = None,
        grant_scope: Literal["once", "edict", "always"] | None = None,
        grant_reason: str | None = None,
        actor: str = "human",
    ) -> Decree:
        """Deprecated memorial alias; delegates to durable authority.

        ``actor`` is intentionally ignored: compatibility callers do not get to
        manufacture authority. 3C3B will replace those callers with AuthContext.
        """

        del actor
        decision_request_id = self.pending_tool_decision_id_for_memorial(memorial_id)
        record = await self.resolve_tool_decision(
            decision_request_id,
            action,
            comment=comment,
            grant_scope=grant_scope,
            grant_reason=grant_reason,
            auth=self._legacy_tool_adapter_auth(decision_request_id),
        )
        decree = self._project_tool_decree(record)
        if decree is None:
            raise RuntimeError("tool decree projection is unavailable")
        return decree

    async def submit_decree(self, decree: Decree) -> None:
        """Process a decree and update memorial status accordingly."""
        memorial = self._storage.get_memorial(decree.memorial_id)
        if not memorial:
            raise ValueError(f"Memorial '{decree.memorial_id}' not found")

        self._storage.save_decree(decree)

        if decree.action == "approve":
            await self._handle_approve(memorial, decree)
        elif decree.action == "reject":
            await self._handle_reject(memorial, decree)
        elif decree.action == "retry":
            await self._handle_retry(memorial, decree)
        elif decree.action == "amend":
            await self._handle_amend(memorial, decree)
        elif decree.action == "cancel":
            await self._handle_cancel(memorial, decree)

        # Wake up any waiting approval
        evt = self._pending.get(decree.memorial_id)
        if evt:
            self._results[decree.memorial_id] = decree
            evt.set()

    async def _handle_approve(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "approved"
        memorial.status = TaskStatus.COMPLETED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.approved",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

        # Spec Section 4: grant_scope 升级为 session rule
        if decree.grant_scope and decree.grant_scope != "once" and self._session_rule_store:
            await self._write_session_rule_from_decree(memorial, decree)

    async def _handle_reject(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.error = decree.comment or "Rejected by reviewer"
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.rejected",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id, "comment": decree.comment},
            )
        )

    async def _handle_retry(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        new_memorial = Memorial(
            edict_id=memorial.edict_id,
            instruction=memorial.instruction,
            attempt=memorial.attempt + 1,
            parent_memorial_id=memorial.id,
        )
        self._storage.save_memorial(new_memorial)

        await self._bus.emit(
            make_event(
                "decree.retry",
                edict_id=memorial.edict_id,
                memorial_id=new_memorial.id,
                producer="approval_manager",
                payload={
                    "decree_id": decree.id,
                    "original_memorial_id": memorial.id,
                    "attempt": new_memorial.attempt,
                },
            )
        )

    async def _handle_amend(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.FAILED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

        if decree.amended_goal:
            new_edict = Edict(
                goal=decree.amended_goal,
                title=title_from_goal(decree.amended_goal),
                context=f"Amended from memorial {memorial.id}",
                submitter=f"approval:{decree.actor}",
            )
            correlation_id = f"amend:{decree.id}"
            command = SubmitEdictCommand(
                edict=new_edict,
                idempotency_key=correlation_id,
                requested_contract=requested_contract_for_edict(new_edict),
                extra_payload={
                    "amended_from": memorial.id,
                    "decree_id": decree.id,
                },
            )
            self._edict_application.submit(
                command,
                auth=make_ingress_auth_context(
                    principal_id=f"approval:{decree.actor}",
                    principal_kind=PrincipalKind.SERVICE,
                    source=AuthenticationSource.TRUSTED_LOCAL,
                    client_kind=ClientKind.SYSTEM,
                    correlation_id=correlation_id,
                ),
                producer="approval_manager",
                correlation_id=correlation_id,
            )

    async def _handle_cancel(self, memorial: Memorial, decree: Decree) -> None:
        memorial.review_status = "rejected"
        memorial.status = TaskStatus.CANCELLED
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "decree.cancelled",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="approval_manager",
                payload={"decree_id": decree.id},
            )
        )

    async def _write_session_rule_from_decree(
        self,
        memorial: Memorial,
        decree: Decree,
    ) -> None:
        """根据 decree.grant_scope 写 session rule，供后续调用直接命中。"""
        from tianshu.tools.policy_store import (
            assert_can_grant,
            compute_fingerprint,
            make_session_rule,
        )

        store = self._session_rule_store
        if store is None:
            return
        tool_name = self._pending_tool.get(decree.memorial_id) or ""
        if not tool_name:
            logger.warning(
                "decree %s: no tool_name recorded for memorial %s, skip session rule",
                decree.id,
                decree.memorial_id,
            )
            return

        scope = cast(Literal["edict", "always"], decree.grant_scope)
        # bash + always 被硬约束禁止
        try:
            assert_can_grant(tool_name, scope)
        except ValueError as e:
            logger.warning("decree %s: %s — downgrading to once", decree.id, e)
            return

        args = self._fetch_latest_approval_args(memorial.id, memorial.edict_id, tool_name)
        fingerprint = compute_fingerprint(tool_name, args)

        rule = make_session_rule(
            tool_name=tool_name,
            arg_fingerprint=fingerprint,
            scope=scope,
            source="approval",
            reason=decree.grant_reason or f"granted by decree {decree.id}",
            edict_id=memorial.edict_id if scope == "edict" else None,
            granted_by_decree_id=decree.id,
        )
        try:
            await store.create(rule)
        except Exception:
            logger.exception("failed to create session rule from decree %s", decree.id)
            return

        self._storage.append_event(
            memorial.edict_id,
            memorial.id,
            "policy.session_rule_created",
            {
                "rule_id": rule.rule_id,
                "tool_name": tool_name,
                "scope": rule.scope,
                "source": rule.source,
                "arg_fingerprint": rule.arg_fingerprint,
                "decree_id": decree.id,
            },
        )

    def _fetch_latest_approval_args(
        self,
        memorial_id: str,
        edict_id: str,
        tool_name: str,
    ) -> dict:
        """从 events 表反查最近一次 tool.approval_required 的 args_summary。"""
        try:
            rows = self._storage.get_events(edict_id)
        except Exception:
            return {}
        for row in reversed(rows or []):
            if row.get("memorial_id") != memorial_id:
                continue
            if row.get("event_type") != "tool.approval_required":
                continue
            payload = row.get("payload") or {}
            if payload.get("tool_name") != tool_name:
                continue
            return payload.get("args_summary") or {}
        return {}
