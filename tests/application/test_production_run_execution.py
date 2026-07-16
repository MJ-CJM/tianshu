"""Managed production execution never relies on EventBus chaining."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.application.run_execution import (
    ManagedExecutionProjection,
    ManagedPlanningResult,
    ProductionAttemptCompleter,
    ProductionRunRunner,
)
from tianshu.bootstrap.wiring_scheduler import _require_restart_safe_legacy_plan
from tianshu.executor.executor import Executor
from tianshu.gateway.core.edict_bridge import EdictBridge
from tianshu.gateway.edicts_api import follow_up_edict
from tianshu.models import Plan, PlanTask, TaskStatus, UsageSummary
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.events import EventEnvelope

_NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)
_AUTHORITY = AttemptAuthority(
    attempt_id="attempt-1",
    memorial_id="root-1",
    owner_id="worker-1",
    fencing_token=1,
)
_PLAN = Plan(
    tasks=[PlanTask(task_id="main", description="work")],
    priority_order=["main"],
)


class _Planner:
    def __init__(self, result: ManagedPlanningResult) -> None:
        self.result = result
        self.calls: list[AttemptAuthority] = []

    async def plan_attempt(self, authority: AttemptAuthority) -> ManagedPlanningResult:
        self.calls.append(authority)
        return self.result


class _Executor:
    def __init__(self, projection: ManagedExecutionProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[AttemptAuthority, Plan]] = []

    async def execute_attempt(
        self,
        authority: AttemptAuthority,
        plan: Plan,
    ) -> ManagedExecutionProjection:
        self.calls.append((authority, plan))
        return self.projection


async def test_runner_directly_awaits_planner_then_executor() -> None:
    planner = _Planner(ManagedPlanningResult(plan=_PLAN))
    executor = _Executor(
        ManagedExecutionProjection(
            status=TaskStatus.COMPLETED,
            summary="done",
            result="answer",
        )
    )
    runner = ProductionRunRunner(planner, executor)

    result = await runner(_AUTHORITY)

    assert result.disposition is AttemptDisposition.SUCCEEDED
    assert planner.calls == [_AUTHORITY]
    assert executor.calls == [(_AUTHORITY, _PLAN)]
    assert runner.take_projection(_AUTHORITY) == executor.projection


async def test_runner_carries_full_memorial_terminal_evidence() -> None:
    projection = ManagedExecutionProjection(
        status=TaskStatus.COMPLETED,
        summary="done",
        result="result",
        final_output="final",
        usage=UsageSummary(total_tokens=9),
        reasoning_content="reasoning",
    )
    runner = ProductionRunRunner(_Planner(ManagedPlanningResult(plan=_PLAN)), _Executor(projection))

    assert (await runner(_AUTHORITY)).disposition is AttemptDisposition.SUCCEEDED
    assert runner.take_projection(_AUTHORITY) == projection


async def test_retryable_projection_is_classified_for_managed_retry() -> None:
    failure = RedactedError(
        code="provider_unavailable",
        message="Provider temporarily unavailable",
        retryable=True,
        details_hash=None,
    )
    runner = ProductionRunRunner(
        _Planner(ManagedPlanningResult(plan=_PLAN)),
        _Executor(ManagedExecutionProjection(status=TaskStatus.FAILED, error=failure)),
        clock=lambda: _NOW,
    )

    result = await runner(_AUTHORITY)

    assert result.disposition is AttemptDisposition.RETRY
    assert result.failure == failure
    assert result.retry_at == _NOW + timedelta(seconds=1)


async def test_plan_review_suspends_without_entering_executor() -> None:
    planner = _Planner(
        ManagedPlanningResult(plan=_PLAN, suspended=True, decision_request_id="decision-1")
    )
    executor = _Executor(ManagedExecutionProjection(status=TaskStatus.COMPLETED))

    result = await ProductionRunRunner(planner, executor)(_AUTHORITY)

    assert result.disposition is AttemptDisposition.SUSPENDED
    assert executor.calls == []


def test_terminal_completer_consumes_exact_authority_projection() -> None:
    planner = _Planner(ManagedPlanningResult(plan=_PLAN))
    projection = ManagedExecutionProjection(status=TaskStatus.COMPLETED, result="answer")
    runner = ProductionRunRunner(planner, _Executor(projection))
    runner.store_projection(_AUTHORITY, projection)
    calls: list[object] = []

    class _Fenced:
        def complete(self, command: object) -> str:
            calls.append(command)
            return "event-1"

    class _Attempts:
        def complete(self, **kwargs: object) -> bool:
            raise AssertionError("terminal completion must use fenced UoW")

    outcome = AttemptOutcomeV1(
        disposition=AttemptDisposition.SUCCEEDED,
        completed_at=_NOW,
    )

    completed = ProductionAttemptCompleter(_Fenced(), _Attempts(), runner)(
        _AUTHORITY,
        outcome,
    )

    assert completed is True
    assert len(calls) == 1
    assert runner.take_projection(_AUTHORITY) is None


def test_suspended_completer_uses_attempt_ledger_without_terminal_projection() -> None:
    recorded: list[dict[str, object]] = []

    class _Fenced:
        def complete(self, command: object) -> str:
            raise AssertionError("suspension is not a root terminal")

    class _Attempts:
        def complete(self, **kwargs: object) -> bool:
            recorded.append(kwargs)
            return True

    runner = ProductionRunRunner(
        _Planner(ManagedPlanningResult(plan=_PLAN)),
        _Executor(ManagedExecutionProjection(status=TaskStatus.COMPLETED)),
    )
    outcome = AttemptOutcomeV1(
        disposition=AttemptDisposition.SUSPENDED,
        completed_at=_NOW,
    )

    assert ProductionAttemptCompleter(_Fenced(), _Attempts(), runner)(
        _AUTHORITY,
        outcome,
    )
    assert recorded[0]["attempt_id"] == "attempt-1"


def test_production_adapters_contain_no_root_task_creation() -> None:
    for adapter in (
        Executor.handle_plan_completed,
        Executor.handle_resume,
        Executor.retry_dag,
        EdictBridge._follow_up,  # noqa: SLF001
        follow_up_edict,
    ):
        assert "create_task" not in inspect.getsource(adapter), adapter.__qualname__


def test_legacy_plan_without_durable_binding_is_retained_fail_closed(storage) -> None:
    event = EventEnvelope(
        event_id="legacy-plan-1",
        event_type="plan.completed",
        edict_id="edict-1",
        memorial_id="root-1",
        timestamp=_NOW,
        producer="legacy",
        payload={"plan": _PLAN.model_dump(mode="json")},
    )
    with (
        storage.unit_of_work() as unit_of_work,
        pytest.raises(RuntimeError, match="retained.*binding is missing"),
    ):
        _require_restart_safe_legacy_plan(storage, unit_of_work.connection, event)
