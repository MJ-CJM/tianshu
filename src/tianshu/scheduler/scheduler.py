"""Lightweight async scheduler — immediate, once, and cron."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter
from ulid import ULID

from tianshu.bus.event_bus import EventBus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class _Job:
    __slots__ = ("job_id", "edict_id", "schedule_type", "task", "next_run")

    def __init__(
        self,
        job_id: str,
        edict_id: str,
        schedule_type: str,
        task: asyncio.Task | None = None,
        next_run: datetime | None = None,
    ) -> None:
        self.job_id = job_id
        self.edict_id = edict_id
        self.schedule_type = schedule_type
        self.task = task
        self.next_run = next_run


class Scheduler:
    """Manages edict scheduling: immediate, once (delayed), cron (future)."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._jobs: dict[str, _Job] = {}
        self._running = False
        self._cron_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        logger.info("Scheduler started")

    async def stop(self) -> None:
        self._running = False
        for job in list(self._jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
        self._jobs.clear()
        logger.info("Scheduler stopped")

    async def schedule(self, edict: Edict, memorial_id: str | None = None) -> str:
        """Schedule an edict based on its schedule config. Returns job_id."""
        job_id = str(ULID())
        schedule = edict.schedule

        if schedule.type == "immediate":
            job = _Job(job_id, edict.id, "immediate")
            self._jobs[job_id] = job
            await self._emit_scheduled(edict, memorial_id=memorial_id)
        elif schedule.type == "once":
            if not schedule.at:
                await self._emit_scheduled(edict, memorial_id=memorial_id)
                job = _Job(job_id, edict.id, "once")
                self._jobs[job_id] = job
            else:
                delay = (schedule.at - datetime.now(UTC)).total_seconds()
                if delay <= 0:
                    await self._emit_scheduled(edict, memorial_id=memorial_id)
                    job = _Job(job_id, edict.id, "once")
                    self._jobs[job_id] = job
                else:
                    task = asyncio.create_task(
                        self._delayed_emit(edict, delay)
                    )
                    job = _Job(
                        job_id, edict.id, "once", task=task, next_run=schedule.at
                    )
                    self._jobs[job_id] = job
        elif schedule.type == "cron":
            if not schedule.cron:
                logger.warning(
                    "Cron edict %s has no cron expression, falling back to immediate",
                    edict.id,
                )
                job = _Job(job_id, edict.id, "cron")
                self._jobs[job_id] = job
                await self._emit_scheduled(edict, memorial_id=memorial_id)
            else:
                cron = croniter(schedule.cron, datetime.now(UTC))
                next_run = cron.get_next(datetime)
                task = asyncio.create_task(
                    self._cron_loop(edict, schedule.cron, job_id)
                )
                job = _Job(job_id, edict.id, "cron", task=task, next_run=next_run)
                self._jobs[job_id] = job

        return job_id

    async def cancel(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job and job.task and not job.task.done():
            job.task.cancel()

    async def list_jobs(self) -> list[dict]:
        return [
            {
                "job_id": j.job_id,
                "edict_id": j.edict_id,
                "schedule_type": j.schedule_type,
                "next_run": j.next_run.isoformat() if j.next_run else None,
            }
            for j in self._jobs.values()
        ]

    async def handle_submitted(self, event: EventEnvelope) -> None:
        """EventBus handler for edict.submitted."""
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            logger.error("Scheduler: edict %s not found", edict_id)
            return
        await self.schedule(edict, memorial_id=event.memorial_id)

    async def _emit_scheduled(self, edict: Edict, memorial_id: str | None = None) -> None:
        payload: dict = {"goal": edict.goal}
        await self._bus.emit(
            make_event(
                "edict.scheduled",
                edict_id=edict.id,
                memorial_id=memorial_id,
                producer="scheduler",
                payload=payload,
            )
        )

    async def _cron_loop(self, edict: Edict, cron_expr: str, job_id: str) -> None:
        """Repeatedly emit edict.scheduled at cron intervals."""
        try:
            while self._running:
                cron = croniter(cron_expr, datetime.now(UTC))
                next_run = cron.get_next(datetime)
                delay = (next_run - datetime.now(UTC)).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                fresh_edict = self._storage.get_edict(edict.id)
                if not fresh_edict or fresh_edict.status.value != "open":
                    logger.info(
                        "Cron job %s stopped: edict %s is no longer open",
                        job_id,
                        edict.id,
                    )
                    break
                await self._emit_scheduled(fresh_edict)
                if job_id in self._jobs:
                    next_cron = croniter(cron_expr, datetime.now(UTC))
                    self._jobs[job_id].next_run = next_cron.get_next(datetime)
        except asyncio.CancelledError:
            logger.info("Cron loop cancelled for edict %s", edict.id)

    async def _delayed_emit(self, edict: Edict, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._emit_scheduled(edict)
        except asyncio.CancelledError:
            logger.info("Delayed schedule cancelled for edict %s", edict.id)
