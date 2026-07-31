"""Managed production execution never relies on EventBus chaining."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.application.fenced_run_completion import FencedRunCompletion
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.application.run_execution import (
    ManagedExecutionProjection,
    ManagedPlanningResult,
    ProductionAttemptCompleter,
    ProductionRunRunner,
)
from tianshu.bootstrap.wiring_scheduler import _require_restart_safe_legacy_plan
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.executor import Executor
from tianshu.gateway.core.edict_bridge import EdictBridge
from tianshu.gateway.edicts_api import follow_up_edict, update_edict_status
from tianshu.gateway.execution_api import retry_dag
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, Plan, PlanTask, TaskStatus, UsageSummary
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.events import EventEnvelope
from tianshu.scheduler.scheduler import Scheduler

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


async def test_real_managed_executor_timeout_retries_then_dead_letters(
    storage,
    config_manager,
) -> None:
    edict = Edict(id="edict-1", goal="work", runtime={"retry_limit": 1})
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id=root.id,
            available_at=_NOW,
            max_attempts=2,
        )
        unit_of_work.commit()
    executor = Executor(EventBus(), storage, config_manager, HookRegistry())
    agent = AsyncMock()
    agent.execute.side_effect = TimeoutError("provider timeout")
    executor.set_agent(agent)
    runner = ProductionRunRunner(
        _Planner(ManagedPlanningResult(plan=_PLAN)),
        executor,
        clock=lambda: _NOW,
    )
    completer = ProductionAttemptCompleter(
        FencedRunCompletion(storage.unit_of_work, storage.attempt_repo),
        storage.attempt_repo,
        runner,
    )

    first_claim = storage.attempt_repo.claim(
        memorial_id=root.id,
        owner_id="worker-1",
        now=_NOW,
        lease_seconds=30,
    )
    assert first_claim is not None
    first_authority = AttemptAuthority(
        attempt_id=first_claim.attempt_id,
        memorial_id=root.id,
        owner_id="worker-1",
        fencing_token=first_claim.fencing_token,
    )
    first = await runner(first_authority)
    assert first.disposition is AttemptDisposition.RETRY
    assert completer(
        first_authority,
        AttemptOutcomeV1(
            disposition=first.disposition,
            completed_at=_NOW,
            failure=first.failure,
            retry_at=first.retry_at,
        ),
    )
    assert storage.get_memorial(root.id).status not in {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.failed'"
        ).fetchone()[0]
        == 0
    )

    second_claim = storage.attempt_repo.claim(
        memorial_id=root.id,
        owner_id="worker-2",
        now=_NOW + timedelta(seconds=1),
        lease_seconds=30,
    )
    assert second_claim is not None
    second_authority = AttemptAuthority(
        attempt_id=second_claim.attempt_id,
        memorial_id=root.id,
        owner_id="worker-2",
        fencing_token=second_claim.fencing_token,
    )
    second = await runner(second_authority)
    assert second.disposition is AttemptDisposition.RETRY
    assert completer(
        second_authority,
        AttemptOutcomeV1(
            disposition=second.disposition,
            completed_at=_NOW + timedelta(seconds=1),
            failure=second.failure,
            retry_at=second.retry_at,
        ),
    )
    assert storage.get_memorial(root.id).status is TaskStatus.FAILED
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.failed'"
        ).fetchone()[0]
        == 1
    )


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


def test_pre_runner_failure_without_projection_is_fenced_immediately() -> None:
    runner = ProductionRunRunner(
        _Planner(ManagedPlanningResult(plan=_PLAN)),
        _Executor(ManagedExecutionProjection(status=TaskStatus.COMPLETED)),
    )
    calls: list[object] = []

    class _Fenced:
        def complete(self, command: object) -> str:
            calls.append(command)
            return "event-1"

    class _Attempts:
        def complete(self, **kwargs: object) -> bool:
            raise AssertionError("terminal completion must use fenced UoW")

    failure = RedactedError(
        code="run_assignment_unavailable",
        message="governed evolution runtime is unavailable",
        retryable=False,
        details_hash=None,
    )
    outcome = AttemptOutcomeV1(
        disposition=AttemptDisposition.FAILED,
        completed_at=_NOW,
        failure=failure,
    )

    completed = ProductionAttemptCompleter(_Fenced(), _Attempts(), runner)(
        _AUTHORITY,
        outcome,
    )

    assert completed is True
    assert len(calls) == 1
    command = calls[0]
    assert command.error == failure.message
    assert command.failure_reason == failure.code


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


def test_production_cancellation_adapters_delegate_to_one_fenced_service() -> None:
    for adapter in (
        update_edict_status,
        Executor.cancel_dag,
        Scheduler.cancel,
        ApprovalManager._handle_cancel,  # noqa: SLF001
    ):
        source = inspect.getsource(adapter)
        assert "cancel_root" in source, adapter.__qualname__


def test_dag_retry_api_requires_and_delegates_stable_idempotency_key() -> None:
    api_source = inspect.getsource(retry_dag)
    executor_source = inspect.getsource(Executor.retry_dag)
    assert "Idempotency-Key" in api_source
    assert "idempotency_key=" in api_source
    assert "managed_run_ingress.retry_dag" in executor_source


def test_follow_up_adapters_leave_replay_busy_and_parent_order_to_ingress() -> None:
    http_source = inspect.getsource(follow_up_edict)
    bridge_source = inspect.getsource(EdictBridge.continue_or_create)
    follow_up_source = inspect.getsource(EdictBridge._follow_up)  # noqa: SLF001
    assert "has_active" not in http_source
    assert "has_active" not in bridge_source
    assert "parent_memorial_id=" not in http_source
    assert "parent_memorial_id=" not in follow_up_source


def test_legacy_executor_adapters_delegate_whole_event_to_ingress() -> None:
    for adapter in (Executor.handle_plan_completed, Executor.handle_resume):
        source = inspect.getsource(adapter)
        assert "adopt_legacy(event)" in source
        assert "adopt_existing" not in source


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
