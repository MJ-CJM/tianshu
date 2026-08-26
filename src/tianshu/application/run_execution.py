"""Direct, dispatcher-owned production planning and execution."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from tianshu.application.fenced_run_completion import (
    FencedRunCompletion,
    FencedRunCompletionCommand,
)
from tianshu.application.managed_attempt import (
    ManagedRunSuspended,
    bind_managed_attempt_authority,
)
from tianshu.application.run_dispatcher import (
    AttemptAuthority,
    AttemptRunResult,
)
from tianshu.models import Plan, TaskStatus, UsageSummary
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.failure import classify_exception_failure

logger = logging.getLogger(__name__)


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

    def is_suspended(self, **kwargs: object) -> bool: ...


class ProductionRunRunner:
    """Await the two business stages inside the dispatcher's supervised task."""

    def __init__(
        self,
        planner: _ManagedPlanner,
        executor: _ManagedExecutor,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._projections: dict[tuple[str, str, int], ManagedExecutionProjection] = {}

    async def __call__(self, authority: AttemptAuthority) -> AttemptRunResult:
        try:
            with bind_managed_attempt_authority(authority):
                planning = await self._planner.plan_attempt(authority)
                if planning.suspended:
                    return AttemptRunResult(AttemptDisposition.SUSPENDED)
                projection = await self._executor.execute_attempt(authority, planning.plan)
        except ManagedRunSuspended:
            return AttemptRunResult(AttemptDisposition.SUSPENDED)
        except Exception as exc:
            # 前端仍收脱敏 RedactedError(跨租户安全),但服务端须留原始异常供排查——
            # 否则像"pi 缺 anthropic key"这类可操作原因会成为无日志黑洞。
            logger.exception("managed execution raised (redacted to client): %s", exc)
            failure = _redacted_failure(exc)
            self.store_projection(
                authority,
                ManagedExecutionProjection(
                    status=TaskStatus.FAILED,
                    error=failure,
                ),
            )
            return self._failure_result(failure)
        self.store_projection(authority, projection)
        if projection.status is TaskStatus.COMPLETED:
            return AttemptRunResult(AttemptDisposition.SUCCEEDED)
        failure = projection.error or RedactedError(
            code="execution_failed",
            message="Managed execution failed",
            retryable=False,
            details_hash=None,
        )
        return self._failure_result(failure)

    def _failure_result(self, failure: RedactedError) -> AttemptRunResult:
        if failure.retryable:
            return AttemptRunResult(
                AttemptDisposition.RETRY,
                failure=failure,
                retry_at=self._clock().astimezone(UTC) + timedelta(seconds=1),
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

    def discard_projection(self, authority: AttemptAuthority) -> None:
        self._projections.pop(_authority_key(authority), None)


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
        }:
            is_suspended = getattr(self._attempt_repository, "is_suspended", None)
            if is_suspended is not None and is_suspended(
                attempt_id=authority.attempt_id,
                memorial_id=authority.memorial_id,
                fencing_token=authority.fencing_token,
            ):
                return True
            return self._attempt_repository.complete(
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                outcome=outcome,
            )
        if outcome.disposition is AttemptDisposition.RETRY:
            return self._fenced_completion.retry_or_dead_letter(authority, outcome)
        projection = self._runner.take_projection(authority)
        if projection is None:
            if outcome.disposition is not AttemptDisposition.FAILED:
                return False
            projection = ManagedExecutionProjection(
                status=TaskStatus.FAILED,
                error=outcome.failure,
                failure_reason=outcome.failure.code if outcome.failure is not None else None,
            )
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
    from tianshu.models.executor_generation import ExecutorGenerationUnavailable

    if isinstance(exc, ExecutorGenerationUnavailable):
        return RedactedError(
            code="generation_retired",
            message="Pinned runtime generation is unavailable",
            retryable=False,
            details_hash=None,
        )
    digest = hashlib.sha256(type(exc).__name__.encode()).hexdigest()
    reason = classify_exception_failure(exc)
    return RedactedError(
        code=reason.value,
        message="Managed execution failed",
        retryable=reason.is_retryable,
        details_hash=digest,
    )


__all__ = [
    "ManagedExecutionProjection",
    "ManagedPlanningResult",
    "ProductionAttemptCompleter",
    "ProductionRunRunner",
]
