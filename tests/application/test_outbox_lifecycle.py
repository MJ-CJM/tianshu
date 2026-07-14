"""Lifecycle truth for the single durable outbox worker."""

from __future__ import annotations

import asyncio

import pytest


def _lifecycle_types():  # type: ignore[no-untyped-def]
    from tianshu.application.outbox import OutboxLifecycle, OutboxLifecycleState

    return OutboxLifecycle, OutboxLifecycleState


class _ControlledDispatcher:
    def __init__(self) -> None:
        self.probe_entered = asyncio.Event()
        self.release_probe = asyncio.Event()
        self.release_run = asyncio.Event()
        self.run_calls = 0
        self.stop_calls = 0
        self.is_stopped = True

    async def drain_once(self, *, limit: int = 50) -> int:
        del limit
        self.probe_entered.set()
        await self.release_probe.wait()
        return 0

    async def run(self) -> None:
        self.run_calls += 1
        self.is_stopped = False
        try:
            await self.release_run.wait()
        finally:
            self.is_stopped = True

    async def stop(self) -> None:
        self.stop_calls += 1
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
            await self.release_run.wait()
            raise RuntimeError("Bearer secret-must-not-enter-lifecycle-state")
        finally:
            self.is_stopped = True


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
