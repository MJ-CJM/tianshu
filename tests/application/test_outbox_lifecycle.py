"""Lifecycle truth for the single durable outbox worker."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tianshu.application.outbox import OutboxDispatcher, OutboxShutdownTimeout
from tianshu.bus.event_bus import EventBus
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository


def _lifecycle_types():  # type: ignore[no-untyped-def]
    from tianshu.application.outbox import OutboxLifecycle, OutboxLifecycleState

    return OutboxLifecycle, OutboxLifecycleState


def _add_startup_event(storage: Storage, event_id: str) -> OutboxRepository:
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="test.lifecycle.startup",
                timestamp=datetime.now(UTC),
                producer="tests",
            ),
        )
        unit_of_work.commit()
    return OutboxRepository(storage.unit_of_work)


class _ControlledDispatcher:
    def __init__(self) -> None:
        self.probe_entered = asyncio.Event()
        self.release_probe = asyncio.Event()
        self.ready = asyncio.Event()
        self.release_run = asyncio.Event()
        self.run_calls = 0
        self.stop_calls = 0
        self.is_stopped = True

    async def wait_until_ready(self) -> None:
        await self.ready.wait()

    async def run(self) -> None:
        self.run_calls += 1
        self.is_stopped = False
        try:
            self.probe_entered.set()
            await self.release_probe.wait()
            self.ready.set()
            await self.release_run.wait()
        finally:
            self.is_stopped = True

    async def stop(self) -> None:
        self.stop_calls += 1
        self.release_probe.set()
        self.release_run.set()
        await asyncio.sleep(0)


async def test_start_is_not_ready_until_probe_succeeds_and_starts_one_run_task() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _ControlledDispatcher()
    lifecycle = OutboxLifecycle(dispatcher)

    assert lifecycle.state is State.STOPPED
    assert not lifecycle.is_ready

    start_task = asyncio.create_task(lifecycle.start())
    await asyncio.wait_for(dispatcher.probe_entered.wait(), timeout=1)
    assert lifecycle.state is State.STARTING
    assert not lifecycle.is_ready

    dispatcher.release_probe.set()
    await asyncio.wait_for(start_task, timeout=1)
    assert lifecycle.state is State.RUNNING
    assert lifecycle.is_ready
    assert dispatcher.run_calls == 1
    run_task = lifecycle.task
    assert run_task is not None and not run_task.done()

    with pytest.raises(RuntimeError, match="already started"):
        await lifecycle.start()
    assert lifecycle.task is run_task
    assert dispatcher.run_calls == 1

    await lifecycle.stop()
    assert lifecycle.state is State.STOPPED
    assert not lifecycle.is_ready
    assert dispatcher.stop_calls == 1
    assert not any(task.get_name() == "outbox-startup-barrier" for task in asyncio.all_tasks())


async def test_unexpected_run_failure_is_fatal_and_not_ready() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _ControlledDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    run_task = lifecycle.task
    assert run_task is not None
    dispatcher.release_run.set()
    await asyncio.wait_for(run_task, timeout=1)
    await asyncio.sleep(0)

    assert lifecycle.state is State.FATAL
    assert not lifecycle.is_ready
    assert lifecycle.failure_code == "unexpected_exit"
    await lifecycle.stop()
    assert lifecycle.state is State.FATAL


class _FailingDispatcher(_ControlledDispatcher):
    async def run(self) -> None:
        self.run_calls += 1
        self.is_stopped = False
        try:
            self.probe_entered.set()
            await self.release_probe.wait()
            self.ready.set()
            await self.release_run.wait()
            raise RuntimeError("Bearer secret-must-not-enter-lifecycle-state")
        finally:
            self.is_stopped = True


class _InitialFailingDispatcher(_ControlledDispatcher):
    async def run(self) -> None:
        self.run_calls += 1
        self.is_stopped = False
        try:
            self.probe_entered.set()
            await self.release_probe.wait()
            raise RuntimeError("Bearer startup-secret-must-not-enter-state")
        finally:
            self.is_stopped = True


async def test_initial_drain_failure_is_fatal_and_propagates_without_waiter_leak() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _InitialFailingDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)

    with pytest.raises(RuntimeError, match="startup-secret"):
        await lifecycle.start()

    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "startup_probe_failed"
    assert not lifecycle.is_ready
    assert lifecycle.task is not None and lifecycle.task.done()
    assert not any(task.get_name() == "outbox-startup-barrier" for task in asyncio.all_tasks())


async def test_stop_during_initial_drain_never_republishes_running() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _ControlledDispatcher()
    lifecycle = OutboxLifecycle(dispatcher)
    start_task = asyncio.create_task(lifecycle.start())
    await asyncio.wait_for(dispatcher.probe_entered.wait(), timeout=1)

    await lifecycle.stop()
    with pytest.raises(RuntimeError, match="stopped during startup"):
        await start_task

    assert lifecycle.state is State.STOPPED
    assert not lifecycle.is_ready
    assert lifecycle.task is not None and lifecycle.task.done()
    assert not any(task.get_name() == "outbox-startup-barrier" for task in asyncio.all_tasks())


async def test_real_initial_drain_is_owned_by_run_task_before_ready(storage: Storage) -> None:
    OutboxLifecycle, State = _lifecycle_types()
    repository = _add_startup_event(storage, "owned-initial-drain")
    event_bus = EventBus()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(_event: EventEnvelope) -> None:
        entered.set()
        await release.wait()

    event_bus.on(
        "test.lifecycle.startup",
        handler,
        consumer_name="test.lifecycle-owned.v1",
    )
    dispatcher = OutboxDispatcher(
        repository,
        event_bus,
        owner_id="lifecycle-owner",
        poll_interval_seconds=60,
    )
    lifecycle = OutboxLifecycle(dispatcher)
    start_task = asyncio.create_task(lifecycle.start())

    await asyncio.wait_for(entered.wait(), timeout=1)
    assert lifecycle.state is State.STARTING
    assert not lifecycle.is_ready
    assert lifecycle.task is not None and not lifecycle.task.done()
    assert dispatcher._drain_task is not None  # noqa: SLF001 - ownership proof
    assert not dispatcher._drain_task.done()  # noqa: SLF001

    release.set()
    await asyncio.wait_for(start_task, timeout=1)
    assert lifecycle.state is State.RUNNING
    assert lifecycle.is_ready
    await lifecycle.stop()
    assert lifecycle.state is State.STOPPED
    assert lifecycle.task is not None and lifecycle.task.done()

    storage.close()
    await asyncio.sleep(0.02)
    assert lifecycle.task.done(), "confirmed stop must leave no late SQLite user"
    assert not any(task.get_name() == "outbox-startup-barrier" for task in asyncio.all_tasks())


async def test_cancellation_suppressing_initial_drain_times_out_until_late_release(
    storage: Storage,
) -> None:
    OutboxLifecycle, State = _lifecycle_types()
    repository = _add_startup_event(storage, "suppressed-initial-drain")
    event_bus = EventBus()
    entered = asyncio.Event()
    release = asyncio.Event()
    cancellations = 0

    async def handler(_event: EventEnvelope) -> None:
        nonlocal cancellations
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancellations += 1

    event_bus.on(
        "test.lifecycle.startup",
        handler,
        consumer_name="test.lifecycle-suppressed.v1",
    )
    dispatcher = OutboxDispatcher(
        repository,
        event_bus,
        owner_id="lifecycle-owner",
        poll_interval_seconds=60,
        shutdown_timeout_seconds=0.01,
    )
    lifecycle = OutboxLifecycle(dispatcher)
    start_task = asyncio.create_task(lifecycle.start())
    await asyncio.wait_for(entered.wait(), timeout=1)

    with pytest.raises(OutboxShutdownTimeout):
        await lifecycle.stop()
    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_timeout"
    assert not dispatcher.is_stopped
    assert not start_task.done()
    assert cancellations >= 1
    with storage._lock:  # noqa: SLF001 - timeout must retain live shared SQLite
        assert storage._conn.execute("SELECT 1").fetchone()[0] == 1  # noqa: SLF001

    release.set()
    with pytest.raises(RuntimeError, match="stopped during startup"):
        await asyncio.wait_for(start_task, timeout=1)
    assert dispatcher.is_stopped
    assert lifecycle.task is not None and lifecycle.task.done()
    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_timeout"
    assert not any(task.get_name() == "outbox-startup-barrier" for task in asyncio.all_tasks())


async def test_fatal_exception_records_only_stable_failure_code() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _FailingDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    dispatcher.release_run.set()
    run_task = lifecycle.task
    assert run_task is not None
    with pytest.raises(RuntimeError, match="secret-must-not-enter"):
        await run_task
    await asyncio.sleep(0)

    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "run_failed"
    assert "secret" not in repr(lifecycle.failure_code)
    await lifecycle.stop()


async def test_run_failure_during_stop_is_still_fatal() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _FailingDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    await lifecycle.stop()

    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "run_failed"
    assert not lifecycle.is_ready


class _UnstoppableDispatcher(_ControlledDispatcher):
    async def stop(self) -> None:
        from tianshu.application.outbox import OutboxShutdownTimeout

        self.stop_calls += 1
        raise OutboxShutdownTimeout("still draining")


async def test_shutdown_timeout_stays_fatal_and_surfaces_failure() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _UnstoppableDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    with pytest.raises(TimeoutError, match="still draining"):
        await lifecycle.stop()

    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_timeout"
    assert not lifecycle.is_ready
    run_task = lifecycle.task
    assert run_task is not None and not run_task.done()

    dispatcher.release_run.set()
    await asyncio.wait_for(run_task, timeout=1)
    await asyncio.sleep(0)
    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_timeout"


class _BlockingStopDispatcher(_ControlledDispatcher):
    def __init__(self) -> None:
        super().__init__()
        self.stop_entered = asyncio.Event()
        self.release_stop = asyncio.Event()

    async def stop(self) -> None:
        self.stop_calls += 1
        self.stop_entered.set()
        await self.release_stop.wait()
        self.release_run.set()
        await asyncio.sleep(0)


async def test_stopping_state_is_explicit_and_not_ready() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _BlockingStopDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    stop_task = asyncio.create_task(lifecycle.stop())
    await asyncio.wait_for(dispatcher.stop_entered.wait(), timeout=1)
    assert lifecycle.state is State.STOPPING
    assert not lifecycle.is_ready

    dispatcher.release_stop.set()
    await asyncio.wait_for(stop_task, timeout=1)
    assert lifecycle.state is State.STOPPED


class _BrokenStopDispatcher(_ControlledDispatcher):
    async def stop(self) -> None:
        raise RuntimeError("raw shutdown failure")


async def test_unexpected_shutdown_failure_is_fatal_and_propagates() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _BrokenStopDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    with pytest.raises(RuntimeError, match="raw shutdown failure"):
        await lifecycle.stop()
    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_failed"
    assert not lifecycle.is_ready

    dispatcher.release_run.set()
    run_task = lifecycle.task
    assert run_task is not None
    await asyncio.wait_for(run_task, timeout=1)


class _IncompleteStopDispatcher(_ControlledDispatcher):
    async def stop(self) -> None:
        self.stop_calls += 1


async def test_unconfirmed_stop_is_fatal_and_surfaces_timeout() -> None:
    OutboxLifecycle, State = _lifecycle_types()
    dispatcher = _IncompleteStopDispatcher()
    dispatcher.release_probe.set()
    lifecycle = OutboxLifecycle(dispatcher)
    await lifecycle.start()

    with pytest.raises(TimeoutError, match="did not confirm"):
        await lifecycle.stop()
    assert lifecycle.state is State.FATAL
    assert lifecycle.failure_code == "shutdown_incomplete"
    assert not lifecycle.is_ready

    dispatcher.release_run.set()
    run_task = lifecycle.task
    assert run_task is not None
    await asyncio.wait_for(run_task, timeout=1)
