"""Supervised execution backed by durable attempt leases and fencing tokens."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from tianshu.executor.adapters import (
    ExecutorGenerationError,
    ExecutorGenerationUnavailable,
)
from tianshu.models.attempt import (
    AttemptDisposition,
    AttemptLeaseV1,
    AttemptOutcomeV1,
)
from tianshu.models.canonical import RedactedError
from tianshu.storage.attempt_ledger import AttemptFenceLost
from tianshu.universe.router import (
    ChallengerRouter,
    EvolutionRuntimeUnavailable,
    GenerationBindingUnavailable,
    GenerationRetired,
    RunAssignmentUnavailable,
    SystemSnapshotUnavailable,
)

logger = logging.getLogger(__name__)


class RunShutdownTimeout(TimeoutError):
    """One or more supervised runners did not stop before the deadline."""


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    """Immutable process-local projection of one durable execution lease."""

    attempt_id: str
    memorial_id: str
    owner_id: str
    fencing_token: int


@dataclass(frozen=True, slots=True)
class AttemptRunResult:
    """Runner result whose completion timestamp is owned by the dispatcher."""

    disposition: AttemptDisposition
    failure: RedactedError | None = None
    retry_at: datetime | None = None

    def to_outcome(self, *, completed_at: datetime) -> AttemptOutcomeV1:
        return AttemptOutcomeV1(
            disposition=self.disposition,
            completed_at=completed_at,
            failure=self.failure,
            retry_at=self.retry_at,
        )


class _AttemptOperations(Protocol):
    def claim(
        self,
        *,
        memorial_id: str,
        owner_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptLeaseV1 | None: ...

    def heartbeat(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool: ...

    def complete(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        outcome: AttemptOutcomeV1,
    ) -> bool: ...


class _HeartbeatLost(RuntimeError):
    pass


AttemptRunner = Callable[[AttemptAuthority], Coroutine[Any, Any, AttemptRunResult]]
AttemptCompleter = Callable[[AttemptAuthority, AttemptOutcomeV1], bool]
AttemptExitCleanup = Callable[[AttemptAuthority], None]
GenerationRelease = Callable[[str], bool]
AttemptFailureAudit = Callable[[AttemptAuthority, str], None]


class RunDispatcher:
    """Claim due attempts and supervise exactly one local runner per won lease."""

    def __init__(
        self,
        repository: _AttemptOperations,
        runner: AttemptRunner,
        *,
        owner_id: str,
        completer: AttemptCompleter | None = None,
        exit_cleanup: AttemptExitCleanup | None = None,
        generation_release: GenerationRelease | None = None,
        failure_audit: AttemptFailureAudit | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 30,
        heartbeat_interval_seconds: float = 10,
        shutdown_timeout_seconds: float = 5,
        challenger_router: ChallengerRouter | None = None,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        self._heartbeat_interval_seconds = _positive_finite(
            heartbeat_interval_seconds,
            "heartbeat_interval_seconds",
        )
        if self._heartbeat_interval_seconds >= lease_seconds:
            raise ValueError("heartbeat_interval_seconds must be below lease_seconds")
        self._shutdown_timeout_seconds = _positive_finite(
            shutdown_timeout_seconds,
            "shutdown_timeout_seconds",
        )
        self._repository = repository
        self._runner = runner
        self._completer = completer or self._complete_with_repository
        self._exit_cleanup = exit_cleanup
        self._generation_release = generation_release
        self._failure_audit = failure_audit
        self._owner_id = owner_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = lease_seconds
        self._challenger_router = challenger_router
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._memorial_tasks: dict[str, asyncio.Task[None]] = {}
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self._stop_requested = False

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    @property
    def is_stopped(self) -> bool:
        return self._stop_requested and not self._tasks

    async def dispatch(self, memorial_id: str) -> bool:
        """Claim and start one due root attempt, returning whether this process won."""
        if self._challenger_router is None:
            raise RuntimeError("challenger_router_required")
        if self._stop_requested:
            return False
        if memorial_id in self._memorial_tasks:
            return False
        claimed = self._repository.claim(
            memorial_id=memorial_id,
            owner_id=self._owner_id,
            now=self._clock(),
            lease_seconds=self._lease_seconds,
        )
        if claimed is None:
            return False
        assert claimed.owner_id is not None
        authority = AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id=claimed.owner_id,
            fencing_token=claimed.fencing_token,
        )
        task = asyncio.create_task(
            self._execute(authority),
            name=f"run-attempt-{authority.attempt_id}",
        )
        self._idle_event.clear()
        self._tasks[authority.attempt_id] = task
        self._memorial_tasks[authority.memorial_id] = task

        def observe(completed: asyncio.Task[None]) -> None:
            self._observe_task(
                authority.attempt_id,
                authority.memorial_id,
                completed,
            )

        task.add_done_callback(observe)
        return True

    async def wait_until_idle(self) -> None:
        """Wait until this dispatcher owns no supervised local attempts."""
        await self._idle_event.wait()

    async def stop(self) -> None:
        """Stop new claims, drain briefly, then cancel remaining supervised work."""
        self._stop_requested = True
        self._reap_completed_tasks()
        active = {task for task in self._tasks.values() if not task.done()}
        if not active:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout_seconds
        _, pending = await asyncio.wait(
            active,
            timeout=self._shutdown_timeout_seconds / 2,
        )
        for task in pending:
            task.cancel()
        if pending:
            _, pending = await asyncio.wait(
                pending,
                timeout=max(deadline - loop.time(), 0),
            )
        self._reap_completed_tasks()
        pending = {task for task in pending if not task.done()}
        if pending:
            raise RunShutdownTimeout("supervised run did not stop before shutdown deadline")

    async def _execute(self, authority: AttemptAuthority) -> None:
        if self._challenger_router is None:
            raise RuntimeError("challenger_router_required")
        runner_task: asyncio.Task[AttemptRunResult] | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        runtime_bound = False
        try:
            with self._challenger_router.bind_runtime(
                authority.memorial_id,
                attempt_id=authority.attempt_id,
            ):
                runtime_bound = True
                runner_task = asyncio.create_task(
                    self._runner(authority),
                    name=f"run-body-{authority.attempt_id}",
                )
                heartbeat_task = asyncio.create_task(
                    self._heartbeat(authority),
                    name=f"run-heartbeat-{authority.attempt_id}",
                )
                done, _ = await asyncio.wait(
                    {runner_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat_task in done:
                    try:
                        await heartbeat_task
                    except _HeartbeatLost:
                        runner_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await runner_task
                        return
                result = await runner_task
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
                outcome = result.to_outcome(completed_at=self._clock())
                if not self._completer(authority, outcome):
                    raise AttemptFenceLost("attempt completion lost its fence")
                if outcome.failure is not None and outcome.failure.code == "generation_retired":
                    self._record_failure_audit(authority, outcome.failure.code)
        except (EvolutionRuntimeUnavailable, ExecutorGenerationError, LookupError) as exc:
            if isinstance(exc, GenerationRetired) or (
                runtime_bound and isinstance(exc, ExecutorGenerationUnavailable)
            ):
                failure_code = "generation_retired"
                failure_message = "pinned runtime generation is unavailable"
            elif isinstance(exc, (GenerationBindingUnavailable, ExecutorGenerationError)):
                failure_code = "generation_binding_unavailable"
                failure_message = "runtime generation binding is unavailable"
            elif isinstance(exc, SystemSnapshotUnavailable):
                failure_code = "system_snapshot_unavailable"
                failure_message = "system snapshot binding is unavailable"
            elif isinstance(exc, (RunAssignmentUnavailable, LookupError)):
                failure_code = "run_assignment_unavailable"
                failure_message = "governed evolution runtime is unavailable"
            else:
                failure_code = "candidate_overlay_unavailable"
                failure_message = "governed evolution runtime is unavailable"
            outcome = AttemptOutcomeV1(
                disposition=AttemptDisposition.FAILED,
                completed_at=self._clock(),
                failure=RedactedError(
                    code=failure_code,
                    message=failure_message,
                    retryable=False,
                    details_hash=None,
                ),
                retry_at=None,
            )
            if not self._completer(authority, outcome):
                raise AttemptFenceLost("attempt completion lost its fence") from exc
            self._record_failure_audit(authority, failure_code)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            if runner_task is not None:
                runner_task.cancel()
            if heartbeat_task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await heartbeat_task
            if runner_task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await runner_task
            try:
                if self._generation_release is not None:
                    self._generation_release(authority.attempt_id)
            finally:
                if self._exit_cleanup is not None:
                    self._exit_cleanup(authority)

    def _record_failure_audit(self, authority: AttemptAuthority, failure_code: str) -> None:
        if self._failure_audit is None:
            return
        try:
            self._failure_audit(authority, failure_code)
        except Exception:
            logger.warning(
                "attempt failure audit could not be persisted",
                extra={"failure_code": failure_code},
            )

    def _complete_with_repository(
        self,
        authority: AttemptAuthority,
        outcome: AttemptOutcomeV1,
    ) -> bool:
        return self._repository.complete(
            attempt_id=authority.attempt_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token,
            outcome=outcome,
        )

    async def _heartbeat(self, authority: AttemptAuthority) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            current = self._repository.heartbeat(
                attempt_id=authority.attempt_id,
                owner_id=authority.owner_id,
                fencing_token=authority.fencing_token,
                now=self._clock(),
            )
            if not current:
                raise _HeartbeatLost("execution attempt heartbeat lost its fence")

    def _observe_task(
        self,
        attempt_id: str,
        memorial_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._tasks.get(attempt_id) is task:
            self._tasks.pop(attempt_id, None)
        if self._memorial_tasks.get(memorial_id) is task:
            self._memorial_tasks.pop(memorial_id, None)
        if not self._tasks:
            self._idle_event.set()
        if not task.cancelled():
            task.exception()

    def _reap_completed_tasks(self) -> None:
        for attempt_id, task in tuple(self._tasks.items()):
            if not task.done():
                continue
            if self._tasks.get(attempt_id) is task:
                self._tasks.pop(attempt_id, None)
            for memorial_id, memorial_task in tuple(self._memorial_tasks.items()):
                if memorial_task is task:
                    self._memorial_tasks.pop(memorial_id, None)
            if not task.cancelled():
                task.exception()
        if not self._tasks:
            self._idle_event.set()


def _positive_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a positive finite number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return converted


__all__ = [
    "AttemptAuthority",
    "AttemptCompleter",
    "AttemptExitCleanup",
    "AttemptRunResult",
    "AttemptRunner",
    "GenerationRelease",
    "RunDispatcher",
    "RunShutdownTimeout",
]
