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
        await self._restore_jobs()
        self._review_timeout_task = asyncio.create_task(self._review_timeout_loop())
        logger.info("Scheduler started")

    async def _restore_jobs(self) -> None:
        """Restore persisted jobs from DB on startup."""
        rows = self._storage.list_active_scheduler_jobs()
        restored = 0
        for row in rows:
            edict_id = row["edict_id"]
            job_id = row["job_id"]
            schedule_type = row["schedule_type"]
            edict = self._storage.get_edict(edict_id)
            if not edict or edict.status.value != "open":
                self._storage.delete_scheduler_job(job_id)
                continue
            if schedule_type == "cron" and row.get("cron_expr"):
                task = asyncio.create_task(
                    self._cron_loop(edict, row["cron_expr"], job_id)
                )
                cron = croniter(row["cron_expr"], datetime.now(UTC))
                next_run = cron.get_next(datetime)
                job = _Job(job_id, edict_id, "cron", task=task, next_run=next_run)
                self._jobs[job_id] = job
                restored += 1
            elif schedule_type == "once" and row.get("next_run"):
                target = datetime.fromisoformat(row["next_run"])
                delay = (target - datetime.now(UTC)).total_seconds()
                if delay <= 0:
                    await self._emit_scheduled(edict)
                    self._storage.delete_scheduler_job(job_id)
                else:
                    task = asyncio.create_task(self._delayed_emit(edict, delay))
                    job = _Job(job_id, edict_id, "once", task=task, next_run=target)
                    self._jobs[job_id] = job
                    restored += 1
        if restored:
            logger.info("Restored %d scheduler jobs from DB", restored)

    async def _review_timeout_loop(self) -> None:
        """Periodically check for NEEDS_REVIEW memorials that have timed out."""
        try:
            while self._running:
                await asyncio.sleep(300)  # Check every 5 minutes
                try:
                    memorials, _ = self._storage.list_memorials(
                        status="needs_review", limit=100,
                    )
                    now = datetime.now(UTC)
                    for memorial in memorials:
                        if not memorial.completed_at:
                            continue
                        elapsed = (now - memorial.completed_at).total_seconds()
                        # Default timeout: 1 hour (3600s)
                        if elapsed > 3600:
                            logger.warning(
                                "Memorial %s NEEDS_REVIEW timeout after %.0fs, escalating",
                                memorial.id,
                                elapsed,
                            )
                            payload = {
                                "memorial_id": memorial.id,
                                "edict_id": memorial.edict_id,
                                "elapsed_seconds": elapsed,
                            }
                            await self._bus.emit(
                                make_event(
                                    "review.timeout",
                                    edict_id=memorial.edict_id,
                                    memorial_id=memorial.id,
                                    producer="scheduler",
                                    payload=payload,
                                )
                            )
                except Exception:
                    logger.exception("Review timeout check failed")
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        self._running = False
        for job in list(self._jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
        if hasattr(self, "_review_timeout_task") and not self._review_timeout_task.done():
            self._review_timeout_task.cancel()
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
                    # Persist for restart recovery
                    self._storage.save_scheduler_job(
                        job_id, edict.id, "once", next_run=schedule.at,
                    )
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
                # Persist cron jobs for restart recovery
                self._storage.save_scheduler_job(
                    job_id, edict.id, "cron",
                    cron_expr=schedule.cron, next_run=next_run,
                )

        return job_id

    async def cancel(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job and job.task and not job.task.done():
            job.task.cancel()
        self._storage.delete_scheduler_job(job_id)

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
        # Set SCHEDULED status on memorial
        if memorial_id:
            memorial = self._storage.get_memorial(memorial_id)
            if memorial and memorial.status.value == "submitted":
                from tianshu.models.common import TaskStatus
                memorial.status = TaskStatus.SCHEDULED
                self._storage.update_memorial(memorial)
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
                    next_dt = next_cron.get_next(datetime)
                    self._jobs[job_id].next_run = next_dt
                    self._storage.update_scheduler_job_next_run(job_id, next_dt)
        except asyncio.CancelledError:
            logger.info("Cron loop cancelled for edict %s", edict.id)

    async def _delayed_emit(self, edict: Edict, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._emit_scheduled(edict)
        except asyncio.CancelledError:
            logger.info("Delayed schedule cancelled for edict %s", edict.id)
