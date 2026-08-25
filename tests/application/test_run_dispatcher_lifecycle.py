"""Lifecycle truth and bounded shutdown for durable run dispatch."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tianshu.application.run_dispatcher import (
    AttemptAuthority,
    AttemptRunResult,
    RunDispatcher,
    RunShutdownTimeout,
)
from tianshu.application.run_reconciler import RunReconciler
from tianshu.models import Edict, Memorial
from tianshu.models.attempt import AttemptDisposition
from tianshu.storage import Storage
from tianshu.storage.attempt_ledger import AttemptFenceLost
from tianshu.universe.router import ChallengerRouter, EvolutionRuntimeUnavailable

_NOW = datetime(2026, 7, 15, 8, tzinfo=UTC)


class _PassthroughRouter:
    @contextmanager
    def bind_runtime(self, memorial_id: str, *, attempt_id: str | None = None):
        del memorial_id, attempt_id
        yield None


_ROUTER = _PassthroughRouter()


class _ProbeRepository:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def list_dispatchable_memorial_ids(self, **kwargs: object) -> tuple[str, ...]:
        del kwargs
        self.calls += 1
        if self.fail_after is not None and self.calls >= self.fail_after:
            raise RuntimeError("raw sqlite failure")
        return ()

    def claim(self, **kwargs: object) -> None:
        del kwargs
        raise AssertionError("empty probe must not claim")

    def heartbeat(self, **kwargs: object) -> bool:
        del kwargs
        raise AssertionError("empty probe must not heartbeat")

    def complete(self, **kwargs: object) -> bool:
        del kwargs
        raise AssertionError("empty probe must not complete")


async def _unused_runner(authority: AttemptAuthority) -> AttemptRunResult:
    del authority
    raise AssertionError("empty probe must not run")


@pytest.mark.asyncio
async def test_missing_router_rejects_before_claim_or_runner() -> None:
    dispatcher = RunDispatcher(_ProbeRepository(), _unused_runner, owner_id="worker")

    with pytest.raises(RuntimeError, match="challenger_router_required"):
        await dispatcher.dispatch("memorial-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "failure_code"),
    [
        (
            EvolutionRuntimeUnavailable("candidate_overlay_unavailable"),
            "candidate_overlay_unavailable",
        ),
        (LookupError("run assignment not found"), "run_assignment_unavailable"),
    ],
)
async def test_runtime_bind_failure_completes_claimed_attempt_and_cleans_projection(
    error: Exception,
    failure_code: str,
) -> None:
    class FailingRouter:
        @contextmanager
        def bind_runtime(self, memorial_id: str, *, attempt_id: str | None = None):
            del memorial_id, attempt_id
            raise error
            yield  # pragma: no cover

    authority = AttemptAuthority(
        attempt_id="attempt-overlay-failure",
        memorial_id="memorial-overlay-failure",
        owner_id="worker",
        fencing_token=7,
    )
    outcomes = []
    cleaned = []
    dispatcher = RunDispatcher(
        _ProbeRepository(),
        _unused_runner,
        owner_id="worker",
        challenger_router=FailingRouter(),
        completer=lambda actual, outcome: outcomes.append((actual, outcome)) or True,
        exit_cleanup=cleaned.append,
        clock=lambda: _NOW,
    )

    await dispatcher._execute(authority)  # noqa: SLF001

    assert len(outcomes) == 1
    assert outcomes[0][0] == authority
    assert outcomes[0][1].disposition is AttemptDisposition.FAILED
    assert outcomes[0][1].failure.code == failure_code
    assert cleaned == [authority]


@pytest.mark.asyncio
async def test_corrupt_durable_assignment_is_completed_once_without_running(
    tmp_path: Path,
) -> None:
    storage = Storage(str(tmp_path / "corrupt-assignment.db"))
    storage.init_db()
    storage.save_edict(Edict(id="edict-corrupt", goal="test"))
    storage.save_memorial(Memorial(id="memorial-corrupt", edict_id="edict-corrupt"))
    router = ChallengerRouter(storage)
    router.assign("memorial-corrupt")
    storage._conn.execute("DROP TRIGGER run_evolution_assignments_no_update")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_evolution_assignments SET assignment_hash=?",
        ("0" * 64,),
    )
    storage._conn.commit()  # noqa: SLF001
    authority = AttemptAuthority(
        attempt_id="attempt-corrupt",
        memorial_id="memorial-corrupt",
        owner_id="worker",
        fencing_token=11,
    )
    outcomes = []
    cleaned = []
    runner_calls = []

    async def runner(actual: AttemptAuthority) -> AttemptRunResult:
        runner_calls.append(actual)
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        runner,
        owner_id="worker",
        challenger_router=router,
        completer=lambda actual, outcome: outcomes.append((actual, outcome)) or True,
        exit_cleanup=cleaned.append,
        clock=lambda: _NOW,
    )
    try:
        await dispatcher._execute(authority)  # noqa: SLF001
    finally:
        storage.close()

    assert runner_calls == []
    assert len(outcomes) == 1
    assert outcomes[0][0] == authority
    failure = outcomes[0][1].failure
    assert outcomes[0][1].disposition is AttemptDisposition.FAILED
    assert failure.code == "run_assignment_unavailable"
    assert not failure.retryable
    assert cleaned == [authority]


@pytest.mark.asyncio
async def test_false_injected_completion_is_an_explicit_fence_loss() -> None:
    repository = _ProbeRepository()

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    observed: list[tuple[AttemptAuthority, object]] = []

    def completer(authority: AttemptAuthority, outcome: object) -> bool:
        observed.append((authority, outcome))
        return False

    authority = AttemptAuthority(
        attempt_id="attempt-1",
        memorial_id="memorial-1",
        owner_id="worker",
        fencing_token=1,
    )
    dispatcher = RunDispatcher(
        repository,
        runner,
        owner_id="worker",
        clock=lambda: _NOW,
        completer=completer,
        challenger_router=_ROUTER,
    )

    with pytest.raises(AttemptFenceLost, match="completion"):
        await dispatcher._execute(authority)  # noqa: SLF001
    assert observed and observed[0][0] == authority


@pytest.mark.asyncio
async def test_every_dispatch_exit_clears_authority_projection_buffer() -> None:
    repository = _ProbeRepository()

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    cleaned: list[AttemptAuthority] = []
    authority = AttemptAuthority(
        attempt_id="attempt-1",
        memorial_id="memorial-1",
        owner_id="worker",
        fencing_token=1,
    )
    dispatcher = RunDispatcher(
        repository,
        runner,
        owner_id="worker",
        completer=lambda _authority, _outcome: False,
        exit_cleanup=cleaned.append,
        challenger_router=_ROUTER,
    )

    with pytest.raises(AttemptFenceLost):
        await dispatcher._execute(authority)  # noqa: SLF001
    assert cleaned == [authority]


@pytest.mark.asyncio
async def test_runner_executes_inside_existing_assignment_runtime_scope() -> None:
    repository = _ProbeRepository()
    events: list[str] = []

    class BindingRouter:
        @contextmanager
        def bind_runtime(self, memorial_id: str, *, attempt_id: str | None = None):
            events.append(f"enter:{memorial_id}:{attempt_id}")
            try:
                yield
            finally:
                events.append(f"exit:{memorial_id}:{attempt_id}")

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        assert events == [f"enter:{authority.memorial_id}:{authority.attempt_id}"]
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    authority = AttemptAuthority(
        attempt_id="attempt-overlay",
        memorial_id="memorial-overlay",
        owner_id="worker",
        fencing_token=1,
    )
    dispatcher = RunDispatcher(
        repository,
        runner,
        owner_id="worker",
        completer=lambda _authority, _outcome: True,
        challenger_router=BindingRouter(),  # type: ignore[arg-type]
    )

    await dispatcher._execute(authority)  # noqa: SLF001

    assert events == [
        "enter:memorial-overlay:attempt-overlay",
        "exit:memorial-overlay:attempt-overlay",
    ]


def test_heartbeat_interval_must_be_below_lease_deadline() -> None:
    repository = _ProbeRepository()
    with pytest.raises(ValueError, match="below lease_seconds"):
        RunDispatcher(
            repository,
            _unused_runner,
            owner_id="worker",
            lease_seconds=10,
            heartbeat_interval_seconds=10,
        )


@pytest.mark.asyncio
async def test_readiness_requires_successful_probe_and_live_supervised_loop() -> None:
    repository = _ProbeRepository()
    dispatcher = RunDispatcher(repository, _unused_runner, owner_id="worker")
    reconciler = RunReconciler(
        repository,
        dispatcher,
        poll_interval_seconds=0.01,
    )
    assert not reconciler.is_ready
    await reconciler.start()
    assert reconciler.is_ready
    assert reconciler.task is not None and not reconciler.task.done()
    await reconciler.stop()
    assert not reconciler.is_ready
    calls_after_stop = repository.calls
    await asyncio.sleep(0.03)
    assert repository.calls == calls_after_stop


@pytest.mark.asyncio
async def test_fatal_scan_exit_clears_readiness_and_records_stable_code() -> None:
    repository = _ProbeRepository(fail_after=2)
    dispatcher = RunDispatcher(repository, _unused_runner, owner_id="worker")
    reconciler = RunReconciler(
        repository,
        dispatcher,
        poll_interval_seconds=0.01,
    )
    await reconciler.start()
    assert reconciler.is_ready
    assert reconciler.task is not None
    with pytest.raises(RuntimeError, match="raw sqlite failure"):
        await reconciler.task
    assert not reconciler.is_ready
    assert reconciler.failure_code == "scan_failed"
    await reconciler.stop()


def _open_seeded(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id="memorial-1", edict_id="edict-1", attempt=1))
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
        )
        uow.commit()
    return storage


@pytest.mark.asyncio
async def test_shutdown_is_bounded_cancels_remainder_and_leaves_lease(tmp_path: Path) -> None:
    storage = _open_seeded(tmp_path / "bounded.db")
    runner_started = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        runner_started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await release.wait()
            raise

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        stubborn_runner,
        owner_id="worker",
        clock=lambda: _NOW,
        heartbeat_interval_seconds=29,
        shutdown_timeout_seconds=0.04,
        challenger_router=_ROUTER,
    )
    try:
        assert await dispatcher.dispatch("memorial-1")
        await runner_started.wait()
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(RunShutdownTimeout, match="did not stop"):
            await dispatcher.stop()
        assert loop.time() - started < 0.2
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts"
            ).fetchone()[0]
            == "claimed"
        )
        release.set()
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()
        await dispatcher.stop()
        assert dispatcher.is_stopped
    finally:
        release.set()
        if not dispatcher.is_stopped:
            await dispatcher.stop()
        storage.close()


@pytest.mark.asyncio
async def test_stop_reaps_fast_runner_done_before_tracking_callback(tmp_path: Path) -> None:
    storage = _open_seeded(tmp_path / "done-before-callback.db")

    async def quick_runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        quick_runner,
        owner_id="worker",
        clock=lambda: _NOW,
        challenger_router=_ROUTER,
    )
    callback_deferred = asyncio.Event()
    captured: list[tuple[str, str, asyncio.Task[None]]] = []
    original_observer = dispatcher._observe_task  # noqa: SLF001

    def defer_observer(
        attempt_id: str,
        memorial_id: str,
        task: asyncio.Task[None],
    ) -> None:
        captured.append((attempt_id, memorial_id, task))
        callback_deferred.set()

    try:
        assert await dispatcher.dispatch("memorial-1")
        dispatcher._observe_task = defer_observer  # type: ignore[method-assign]  # noqa: SLF001
        await callback_deferred.wait()
        assert len(captured) == 1
        attempt_id, memorial_id, completed = captured[0]
        assert completed.done()
        assert dispatcher._tasks == {attempt_id: completed}  # noqa: SLF001
        assert dispatcher._memorial_tasks == {memorial_id: completed}  # noqa: SLF001
        assert not dispatcher._idle_event.is_set()  # noqa: SLF001
        dispatcher._observe_task = original_observer  # type: ignore[method-assign]  # noqa: SLF001

        await dispatcher.stop()

        assert dispatcher.is_stopped
        assert dispatcher.active_count == 0
        assert dispatcher._tasks == {}  # noqa: SLF001
        assert dispatcher._memorial_tasks == {}  # noqa: SLF001
        assert dispatcher._idle_event.is_set()  # noqa: SLF001
        async with asyncio.timeout(0.01):
            await dispatcher.wait_until_idle()
    finally:
        dispatcher._observe_task = original_observer  # type: ignore[method-assign]  # noqa: SLF001
        await dispatcher.stop()
        storage.close()


@pytest.mark.asyncio
async def test_stop_observes_done_runner_exception_when_callback_is_deferred(
    tmp_path: Path,
) -> None:
    storage = _open_seeded(tmp_path / "done-exception.db")

    async def failing_runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        raise RuntimeError("runner failed")

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        failing_runner,
        owner_id="worker",
        clock=lambda: _NOW,
        challenger_router=_ROUTER,
    )
    callback_deferred = asyncio.Event()
    captured: list[asyncio.Task[None]] = []
    original_observer = dispatcher._observe_task  # noqa: SLF001

    def defer_observer(
        attempt_id: str,
        memorial_id: str,
        task: asyncio.Task[None],
    ) -> None:
        del attempt_id, memorial_id
        captured.append(task)
        callback_deferred.set()

    try:
        assert await dispatcher.dispatch("memorial-1")
        dispatcher._observe_task = defer_observer  # type: ignore[method-assign]  # noqa: SLF001
        await callback_deferred.wait()
        assert len(captured) == 1
        completed = captured[0]
        assert completed.done()
        assert completed._log_traceback  # type: ignore[attr-defined]  # noqa: SLF001
        dispatcher._observe_task = original_observer  # type: ignore[method-assign]  # noqa: SLF001

        await dispatcher.stop()

        assert not completed._log_traceback  # type: ignore[attr-defined]  # noqa: SLF001
        assert dispatcher.is_stopped
        assert dispatcher._tasks == {}  # noqa: SLF001
        assert dispatcher._memorial_tasks == {}  # noqa: SLF001
    finally:
        dispatcher._observe_task = original_observer  # type: ignore[method-assign]  # noqa: SLF001
        await dispatcher.stop()
        storage.close()


@pytest.mark.asyncio
async def test_startup_probe_failure_never_becomes_ready() -> None:
    repository = _ProbeRepository(fail_after=1)
    dispatcher = RunDispatcher(repository, _unused_runner, owner_id="worker")
    reconciler = RunReconciler(repository, dispatcher)
    with pytest.raises(RuntimeError, match="raw sqlite failure"):
        await reconciler.start()
    assert not reconciler.is_ready
    assert reconciler.failure_code == "startup_probe_failed"
    await reconciler.stop()


@pytest.mark.asyncio
async def test_stop_before_start_is_idempotent_and_never_probes() -> None:
    repository = _ProbeRepository()
    dispatcher = RunDispatcher(repository, _unused_runner, owner_id="worker")
    reconciler = RunReconciler(repository, dispatcher)
    await reconciler.stop()
    await reconciler.stop()
    assert repository.calls == 0
    assert not reconciler.is_ready
