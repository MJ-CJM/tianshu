"""Deterministic recovery loop for due and expired durable attempts."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from tianshu.application.run_dispatcher import RunDispatcher, RunShutdownTimeout


class _DispatchScan(Protocol):
    def list_dispatchable_memorial_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[str, ...]: ...


class RunReconcilerState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    FATAL = "fatal"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RunReconciler:
    """Supervise stable scans and ask the dispatcher to claim each candidate."""

    def __init__(
        self,
        repository: _DispatchScan,
        dispatcher: RunDispatcher,
        *,
        clock: Callable[[], datetime] | None = None,
        before_scan: Callable[[], int] | None = None,
        scan_limit: int = 50,
        poll_interval_seconds: float = 1,
    ) -> None:
        if type(scan_limit) is not int or scan_limit <= 0:
            raise ValueError("scan_limit must be a positive integer")
        if (
            isinstance(poll_interval_seconds, bool)
            or not isinstance(poll_interval_seconds, (int, float))
            or not math.isfinite(float(poll_interval_seconds))
            or poll_interval_seconds <= 0
        ):
            raise ValueError("poll_interval_seconds must be a positive finite number")
        self._repository = repository
        self._dispatcher = dispatcher
        self._clock = clock or (lambda: datetime.now(UTC))
        self._before_scan = before_scan
        self._scan_limit = scan_limit
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._state = RunReconcilerState.STOPPED
        self._failure_code: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._first_probe = asyncio.Event()
        self._stop_requested = False

    @property
    def state(self) -> RunReconcilerState:
        return self._state

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def failure_code(self) -> str | None:
        return self._failure_code

    @property
    def is_ready(self) -> bool:
        task = self._task
        return (
            self._state is RunReconcilerState.RUNNING
            and task is not None
            and not task.done()
            and self._first_probe.is_set()
            and not self._stop_requested
        )

    async def start(self) -> None:
        if self._task is not None or self._state is not RunReconcilerState.STOPPED:
            raise RuntimeError("run reconciler is already started")
        if self._stop_requested:
            raise RuntimeError("stopped run reconciler cannot be restarted")
        self._state = RunReconcilerState.STARTING
        task = asyncio.create_task(self.run(), name="run-reconciler")
        self._task = task
        task.add_done_callback(self._observe_run_exit)
        probe_waiter = asyncio.create_task(
            self._first_probe.wait(),
            name="run-reconciler-startup-probe",
        )
        try:
            done, _ = await asyncio.wait(
                {task, probe_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if probe_waiter in done:
                await probe_waiter
                await asyncio.sleep(0)
                if task.done():
                    await task
                    raise RuntimeError("run reconciler exited during startup")
                self._state = RunReconcilerState.RUNNING
                return
            self._state = RunReconcilerState.FATAL
            self._failure_code = "startup_probe_failed"
            await task
            raise RuntimeError("run reconciler exited before its startup probe")
        finally:
            if not probe_waiter.done():
                probe_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await probe_waiter

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self.reconcile_once()
            self._first_probe.set()
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )

    async def reconcile_once(self) -> int:
        if self._stop_requested:
            return 0
        if self._before_scan is not None:
            await asyncio.to_thread(self._before_scan)
        memorial_ids = self._repository.list_dispatchable_memorial_ids(
            now=self._clock(),
            limit=self._scan_limit,
        )
        claimed = 0
        for memorial_id in memorial_ids:
            claimed += int(await self._dispatcher.dispatch(memorial_id))
        return claimed

    async def stop(self) -> None:
        task = self._task
        was_fatal = self._state is RunReconcilerState.FATAL
        self._stop_requested = True
        self._stop_event.set()
        if task is not None:
            if not was_fatal:
                self._state = RunReconcilerState.STOPPING
            with suppress(asyncio.CancelledError, Exception):
                await task
        try:
            await self._dispatcher.stop()
        except RunShutdownTimeout:
            self._state = RunReconcilerState.FATAL
            self._failure_code = "shutdown_timeout"
            raise
        if not was_fatal and self._state is not RunReconcilerState.FATAL:
            self._state = RunReconcilerState.STOPPED

    def _observe_run_exit(self, task: asyncio.Task[None]) -> None:
        if self._stop_requested:
            if not task.cancelled():
                task.exception()
            return
        if task.cancelled():
            self._failure_code = "run_cancelled"
        elif task.exception() is not None:
            self._failure_code = (
                "scan_failed" if self._first_probe.is_set() else "startup_probe_failed"
            )
        else:
            self._failure_code = "unexpected_exit"
        self._state = RunReconcilerState.FATAL


__all__ = ["RunReconciler", "RunReconcilerState"]
