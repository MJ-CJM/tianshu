"""Direct, dispatcher-owned production planning and execution."""

from __future__ import annotations

import hashlib
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, Protocol

from tianshu.application.fenced_run_completion import (
    FencedRunCompletion,
    FencedRunCompletionCommand,
)
from tianshu.application.run_dispatcher import (
    AttemptAuthority,
    AttemptRunResult,
)
from tianshu.models import Plan, TaskStatus, UsageSummary
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError


@dataclass(frozen=True, slots=True)
class ManagedPlanningResult:
    plan: Plan
    suspended: bool = False
    decision_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ManagedExecutionProjection:
    status: TaskStatus
    summary: str | None = None
    result: str | None = None
    final_output: str | None = None
    usage: UsageSummary | None = None
    reasoning_content: str | None = None
    failure_reason: str | None = None
    error: RedactedError | None = None


class _ManagedPlanner(Protocol):
    def plan_attempt(
        self,
        authority: AttemptAuthority,
    ) -> Coroutine[Any, Any, ManagedPlanningResult]: ...


class _ManagedExecutor(Protocol):
    def execute_attempt(
        self,
        authority: AttemptAuthority,
        plan: Plan,
    ) -> Coroutine[Any, Any, ManagedExecutionProjection]: ...


class _AttemptCompleter(Protocol):
    def complete(self, **kwargs: object) -> bool: ...


class ProductionRunRunner:
    """Await the two business stages inside the dispatcher's supervised task."""

    def __init__(self, planner: _ManagedPlanner, executor: _ManagedExecutor) -> None:
        self._planner = planner
        self._executor = executor
        self._projections: dict[tuple[str, str, int], ManagedExecutionProjection] = {}

    async def __call__(self, authority: AttemptAuthority) -> AttemptRunResult:
        try:
            planning = await self._planner.plan_attempt(authority)
            if planning.suspended:
                return AttemptRunResult(AttemptDisposition.SUSPENDED)
            projection = await self._executor.execute_attempt(authority, planning.plan)
        except Exception as exc:
            failure = _redacted_failure(exc)
            self.store_projection(
                authority,
                ManagedExecutionProjection(
                    status=TaskStatus.FAILED,
                    error=failure,
                ),
            )
            return AttemptRunResult(
                AttemptDisposition.FAILED,
                failure=failure,
            )
        self.store_projection(authority, projection)
        if projection.status is TaskStatus.COMPLETED:
            return AttemptRunResult(AttemptDisposition.SUCCEEDED)
        failure = projection.error or RedactedError(
            code="execution_failed",
            message="Managed execution failed",
            retryable=False,
            details_hash=None,
        )
        return AttemptRunResult(AttemptDisposition.FAILED, failure=failure)

    def store_projection(
        self,
        authority: AttemptAuthority,
        projection: ManagedExecutionProjection,
    ) -> None:
        self._projections[_authority_key(authority)] = projection

    def take_projection(
        self,
        authority: AttemptAuthority,
    ) -> ManagedExecutionProjection | None:
        return self._projections.pop(_authority_key(authority), None)


class ProductionAttemptCompleter:
    """Route business terminals through the fenced UoW; suspend in the ledger."""

    def __init__(
        self,
        fenced_completion: FencedRunCompletion,
        attempt_repository: _AttemptCompleter,
        runner: ProductionRunRunner,
    ) -> None:
        self._fenced_completion = fenced_completion
        self._attempt_repository = attempt_repository
        self._runner = runner

    def __call__(
        self,
        authority: AttemptAuthority,
        outcome: AttemptOutcomeV1,
    ) -> bool:
        if outcome.disposition in {
            AttemptDisposition.SUSPENDED,
            AttemptDisposition.RETRY,
        }:
            return self._attempt_repository.complete(
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                outcome=outcome,
            )
        projection = self._runner.take_projection(authority)
        if projection is None:
            return False
        status = (
            TaskStatus.COMPLETED
            if outcome.disposition is AttemptDisposition.SUCCEEDED
            else TaskStatus.FAILED
        )
        failure = outcome.failure or projection.error
        self._fenced_completion.complete(
            FencedRunCompletionCommand(
                authority=authority,
                outcome=outcome,
                memorial_status=status,
                summary=projection.summary,
                result=projection.result,
                final_output=projection.final_output,
                usage=projection.usage,
                reasoning_content=projection.reasoning_content,
                error=failure.message if failure is not None else None,
                failure_reason=projection.failure_reason or (failure.code if failure else None),
            )
        )
        return True


def _authority_key(authority: AttemptAuthority) -> tuple[str, str, int]:
    return authority.attempt_id, authority.owner_id, authority.fencing_token


def _redacted_failure(exc: Exception) -> RedactedError:
    digest = hashlib.sha256(type(exc).__name__.encode()).hexdigest()
    return RedactedError(
        code="managed_execution_error",
        message="Managed execution failed",
        retryable=False,
        details_hash=digest,
    )


__all__ = [
    "ManagedExecutionProjection",
    "ManagedPlanningResult",
    "ProductionAttemptCompleter",
    "ProductionRunRunner",
]
