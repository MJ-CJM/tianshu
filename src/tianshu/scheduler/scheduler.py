"""Lightweight async scheduler — immediate, once, and cron."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from ulid import ULID

from tianshu.bus.event_bus import EventBus
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope, make_event
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

logger = logging.getLogger(__name__)

_AsyncFn = Callable[[], Coroutine[Any, Any, None]]

# 孤儿任务回收（Multica 借鉴 #1）
ORPHAN_SWEEP_INTERVAL_SECONDS = 120
ORPHAN_IDLE_THRESHOLD_SECONDS = 900  # 15min 无心跳视为孤儿


def submission_job_id(event_id: str) -> str:
    """Bound an arbitrary durable event ID to one scheduler-effect identity."""
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"submitted-{digest}"


_submission_job_id = submission_job_id


def _resolve_tz(tz_name: str | None) -> tzinfo:
    """解析时区名为 tzinfo；非法/空时回退 UTC 并 warn。"""
    if not tz_name or tz_name.upper() == "UTC":
        return UTC
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown timezone %r in cron schedule, falling back to UTC",
            tz_name,
        )
        return UTC


def _next_cron_utc(cron_expr: str, tz_name: str | None = "UTC") -> datetime:
    """按指定时区计算 cron 表达式的下次触发时刻，统一返回 UTC datetime。

    croniter 在传入 timezone-aware datetime 时会按该时区做日历推算（处理 DST），
    返回的 datetime 也带同一时区；这里把它转成 UTC 以便存储和 sleep 计算。
    """
    tz = _resolve_tz(tz_name)
    base = datetime.now(tz)
    cron = croniter(cron_expr, base)
    next_local = cron.get_next(datetime)
    return next_local.astimezone(UTC)


class _Job:
    __slots__ = (
        "job_id",
        "edict_id",
        "schedule_type",
        "task",
        "next_run",
        "initial_memorial_id",
    )

    def __init__(
        self,
        job_id: str,
        edict_id: str,
        schedule_type: str,
        task: asyncio.Task | None = None,
        next_run: datetime | None = None,
        initial_memorial_id: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.edict_id = edict_id
        self.schedule_type = schedule_type
        self.task = task
        self.next_run = next_run
        self.initial_memorial_id = initial_memorial_id


class Scheduler:
    """Manages edict scheduling: immediate, once (delayed), cron (future)."""

    def __init__(
        self,
        event_bus: EventBus,
        storage: Storage,
        *,
        scheduled_run_preparer: Any | None = None,
        run_reconciler: Any | None = None,
        run_cancellation: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._bus = event_bus
        self._storage = storage
        self._outbox_repository = OutboxRepository(storage.unit_of_work)
        self._jobs: dict[str, _Job] = {}
        self._running = False
        self._restored = False  # 作业恢复+常驻任务装配完成后才对外 ready
        self._cron_task: asyncio.Task | None = None
        self._system_jobs: list[dict] = []
        self._system_cron_tasks: list[asyncio.Task] = []
        self._scheduled_run_preparer = scheduled_run_preparer
        if run_cancellation is None:
            from tianshu.application.fenced_run_completion import FencedRunCompletion

            run_cancellation = FencedRunCompletion(
                storage.unit_of_work,
                storage.attempt_repo,
            )
        self._run_cancellation = run_cancellation
        self._run_reconciler = run_reconciler
        self._clock = clock or (lambda: datetime.now(UTC))

    def register_system_jobs(
        self,
        profile_trigger: Any,
        skill_curator: Any = None,
        universe_evolver: Any = None,
    ) -> None:
        """Register built-in system cron jobs (daily profile synthesis, weekly skill curation)."""

        async def _fire() -> None:
            await profile_trigger.run_for_all_personas(trigger_source="cron")

        self._system_jobs.append(
            {"cron": "0 3 * * *", "name": "profile.daily_synthesis", "fn": _fire}
        )
        logger.info("Registered system job: profile.daily_synthesis (0 3 * * *)")

        if skill_curator is not None:

            async def _fire_curate() -> None:
                await skill_curator.run(trigger_source="cron")

            self._system_jobs.append(
                {"cron": "0 4 * * 0", "name": "skill.weekly_curate", "fn": _fire_curate}
            )
            logger.info("Registered system job: skill.weekly_curate (0 4 * * 0)")

        if universe_evolver is not None:

            async def _fire_evolve() -> None:
                await universe_evolver.run(trigger_source="cron")

            self._system_jobs.append(
                {"cron": "0 5 * * *", "name": "universe.daily_evolve", "fn": _fire_evolve}
            )
            logger.info("Registered system job: universe.daily_evolve (0 5 * * *)")

            async def _fire_code_propose() -> None:
                await universe_evolver.auto_propose_codes(trigger_source="cron")

            self._system_jobs.append(
                {
                    "cron": "30 5 * * *",
                    "name": "universe.daily_code_propose",
                    "fn": _fire_code_propose,
                }
            )
            logger.info("Registered system job: universe.daily_code_propose (30 5 * * *)")

    async def start(self) -> None:
        # _running 必须先置位：_restore_jobs 恢复出来的 cron/one-shot 循环体以
        # `while self._running` 为条件，晚置位会让它们一启动就退出。
        # 对外 ready 由独立的 _restored 标志把关（恢复+常驻任务装配完成才为 True）。
        self._running = True
        await self._restore_jobs()
        self._review_timeout_task = asyncio.create_task(self._review_timeout_loop())
        self._orphan_sweep_task = asyncio.create_task(self._orphan_sweep_loop())
        for job in self._system_jobs:
            task = asyncio.create_task(self._system_cron_loop(job["cron"], job["name"], job["fn"]))
            self._system_cron_tasks.append(task)
        self._restored = True
        logger.info("Scheduler started")

    @property
    def is_ready(self) -> bool:
        """运行中、作业已恢复、且全部常驻后台任务存活（任一死亡即不再 ready）。"""
        if not self._running or not self._restored:
            return False
        mandatory: list[asyncio.Task] = []
        review = getattr(self, "_review_timeout_task", None)
        orphan = getattr(self, "_orphan_sweep_task", None)
        if review is not None:
            mandatory.append(review)
        if orphan is not None:
            mandatory.append(orphan)
        mandatory.extend(self._system_cron_tasks)
        return all(not task.done() for task in mandatory)

    async def _restore_jobs(self) -> None:
        """Restore persisted jobs from DB on startup."""
        if self._scheduled_run_preparer is not None:
            await self._restore_managed_jobs()
            return
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
            initial_memorial_id = self._restore_initial_memorial_id(edict_id, job_id)
            if schedule_type == "cron" and row.get("cron_expr"):
                tz_name = edict.schedule.timezone if edict.schedule else "UTC"
                task = asyncio.create_task(
                    self._cron_loop(
                        edict,
                        row["cron_expr"],
                        job_id,
                        tz_name,
                        initial_memorial_id=initial_memorial_id,
                    )
                )
                next_run = _next_cron_utc(row["cron_expr"], tz_name)
                job = _Job(
                    job_id,
                    edict_id,
                    "cron",
                    task=task,
                    next_run=next_run,
                    initial_memorial_id=initial_memorial_id,
                )
                self._jobs[job_id] = job
                restored += 1
            elif schedule_type == "once" and row.get("next_run"):
                target = datetime.fromisoformat(row["next_run"])
                delay = (target - datetime.now(UTC)).total_seconds()
                if delay <= 0:
                    await self._emit_scheduled(edict, memorial_id=initial_memorial_id)
                    self._storage.delete_scheduler_job(job_id)
                else:
                    task = asyncio.create_task(
                        self._delayed_emit(
                            edict,
                            delay,
                            memorial_id=initial_memorial_id,
                            job_id=job_id,
                        )
                    )
                    job = _Job(
                        job_id,
                        edict_id,
                        "once",
                        task=task,
                        next_run=target,
                        initial_memorial_id=initial_memorial_id,
                    )
                    self._jobs[job_id] = job
                    restored += 1
            elif schedule_type == "interval" and row.get("interval_seconds"):
                interval = int(row["interval_seconds"])
                next_run = datetime.now(UTC) + timedelta(seconds=interval)
                task = asyncio.create_task(
                    self._interval_loop(
                        edict,
                        interval,
                        job_id,
                        initial_memorial_id=initial_memorial_id,
                    )
                )
                job = _Job(
                    job_id,
                    edict_id,
                    "interval",
                    task=task,
                    next_run=next_run,
                    initial_memorial_id=initial_memorial_id,
                )
                self._jobs[job_id] = job
                restored += 1
        if restored:
            logger.info("Restored %d scheduler jobs from DB", restored)

    def _restore_initial_memorial_id(self, edict_id: str, job_id: str) -> str | None:
        for event_id, memorial_id in self._outbox_repository.submission_identities_for_edict(
            edict_id
        ):
            if submission_job_id(event_id) != job_id:
                continue
            memorial = self._storage.get_memorial(memorial_id)
            if memorial and memorial.status == TaskStatus.SUBMITTED:
                return memorial_id
        return None

    async def _review_timeout_loop(self) -> None:
        """Periodically check for NEEDS_REVIEW memorials that have timed out."""
        try:
            while self._running:
                await asyncio.sleep(300)  # Check every 5 minutes
                try:
                    memorials, _ = self._storage.list_memorials(
                        status="needs_review",
                        limit=100,
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

    async def _orphan_sweep_loop(self) -> None:
        """周期回收孤儿任务：活跃态但长时间无心跳的 memorial（Multica 借鉴 #1）。

        触发场景：进程崩溃/重启后遗留在 running/planning/auditing 的 memorial，
        或执行体挂起（如外部调用无限等待）。心跳由 storage.append_event 刷新，
        故正常产生事件的任务不会被误判为孤儿。
        """
        try:
            while self._running:
                await asyncio.sleep(ORPHAN_SWEEP_INTERVAL_SECONDS)
                try:
                    stale = self._storage.list_stale_memorials(
                        idle_seconds=ORPHAN_IDLE_THRESHOLD_SECONDS,
                    )
                    for memorial in stale:
                        await self._recover_orphan(memorial)
                except Exception:
                    logger.exception("Orphan sweep failed")
        except asyncio.CancelledError:
            pass

    async def _recover_orphan(self, memorial) -> None:
        """回收单个孤儿 memorial：可续跑的长任务发 edict.resume，否则判失败。"""
        if self._scheduled_run_preparer is not None:
            logger.warning(
                "Observed stale memorial %s; durable attempt reconciler owns recovery",
                memorial.id,
            )
            return
        last = memorial.last_heartbeat_at or memorial.started_at or memorial.created_at
        idle = (datetime.now(UTC) - last).total_seconds() if last else 0.0
        edict = self._storage.get_edict(memorial.edict_id)

        # 安全排除 1：用户主动暂停的长任务（合法无心跳，不回收）
        if edict and edict.runtime.lifecycle_phase == "paused":
            return
        # 安全排除 2：归属活跃 DAG 的 memorial 由 DAG 自身（CascadeCanceller/retry）管理
        if edict:
            dag = self._storage.get_dag_by_edict(edict.id)
            if dag and dag.status in ("pending", "running"):
                return

        # 可续跑：长任务 outer loop（有 acceptance + checkpoint 型 profile）→ 发 resume
        if (
            edict
            and edict.acceptance is not None
            and edict.execution_profile in ("checkpointed", "background")
        ):
            logger.warning(
                "Orphan memorial %s idle %.0fs, resuming outer loop",
                memorial.id,
                idle,
            )
            await self._bus.emit(
                make_event(
                    "edict.resume",
                    edict_id=edict.id,
                    memorial_id=memorial.id,
                    producer="scheduler",
                    payload={"reason": "orphan_recovery", "idle_seconds": idle},
                )
            )
            # 刷新心跳（append_event 对活跃 memorial 顺带打心跳）：给 executor 一个
            # idle 周期接管，避免每次 sweep 都重发 resume（防 resume 风暴），同时留审计痕迹。
            self._storage.append_event(
                edict.id,
                memorial.id,
                "orphan.resume_requested",
                {"idle_seconds": idle},
            )
            return

        # 否则：标记失败，交由现有 auditor/notifier/retry 收尾
        logger.warning(
            "Orphan memorial %s idle %.0fs, marking FAILED",
            memorial.id,
            idle,
        )
        memorial.status = TaskStatus.FAILED
        memorial.error = f"orphaned: no heartbeat for {idle:.0f}s"
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(
            make_event(
                "execution.failed",
                edict_id=memorial.edict_id,
                memorial_id=memorial.id,
                producer="scheduler",
                payload={"status": "failed", "error": memorial.error},
            )
        )

    async def _system_cron_loop(self, cron_expr: str, name: str, fn: _AsyncFn) -> None:
        """Run a system cron job on the given expression until stopped (UTC)."""
        try:
            while self._running:
                next_run = _next_cron_utc(cron_expr, "UTC")
                delay = (next_run - datetime.now(UTC)).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self._running:
                    break
                # 去重（Multica 借鉴 #2-B）：上次同名 job 仍 running 则跳过本次
                if self._storage.has_running_system_job(name):
                    logger.info("System job %s: skip, previous run still running", name)
                    self._storage.create_schedule_run(
                        source=name,
                        kind="system",
                        status="skipped",
                    )
                    continue
                logger.info("Firing system job: %s", name)
                run_id = self._storage.create_schedule_run(
                    source=name,
                    kind="system",
                    status="running",
                )
                try:
                    await fn()
                    self._storage.finish_schedule_run(run_id, "completed")
                except Exception as e:
                    self._storage.finish_schedule_run(run_id, "failed", str(e))
                    logger.exception("System cron job %s failed", name)
        except asyncio.CancelledError:
            logger.info("System cron loop cancelled: %s", name)

    async def stop(self) -> None:
        self._running = False
        self._restored = False
        for job in list(self._jobs.values()):
            if job.task and not job.task.done():
                job.task.cancel()
        if self._cron_task and not self._cron_task.done():
            self._cron_task.cancel()
        for task in self._system_cron_tasks:
            if not task.done():
                task.cancel()
        self._system_cron_tasks.clear()
        if hasattr(self, "_review_timeout_task") and not self._review_timeout_task.done():
            self._review_timeout_task.cancel()
        if hasattr(self, "_orphan_sweep_task") and not self._orphan_sweep_task.done():
            self._orphan_sweep_task.cancel()
        self._jobs.clear()
        logger.info("Scheduler stopped")

    async def schedule(
        self,
        edict: Edict,
        memorial_id: str | None = None,
        *,
        job_id: str | None = None,
        scheduled_at: datetime | None = None,
    ) -> str:
        """Schedule an edict based on its schedule config. Returns job_id."""
        if self._scheduled_run_preparer is not None:
            return await self._schedule_managed(
                edict,
                memorial_id=memorial_id,
                job_id=job_id,
                scheduled_at=scheduled_at,
            )
        replay_safe = job_id is not None
        job_id = job_id or str(ULID())
        if replay_safe:
            existing = self._jobs.get(job_id)
            if existing is not None:
                if existing.edict_id != edict.id:
                    raise RuntimeError("scheduler replay job ID belongs to another edict")
                if memorial_id:
                    await self._reattach_initial_memorial(existing, edict, memorial_id)
                return job_id
            persisted = self._storage.get_scheduler_job(job_id)
            if persisted is not None:
                if persisted["edict_id"] != edict.id:
                    raise RuntimeError("persisted scheduler replay belongs to another edict")
                return job_id
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
                    if replay_safe and not self._storage.save_scheduler_job_if_absent(
                        job_id,
                        edict.id,
                        "once",
                        next_run=schedule.at,
                    ):
                        return job_id
                    task = asyncio.create_task(
                        self._delayed_emit(
                            edict,
                            delay,
                            memorial_id=memorial_id,
                            job_id=job_id,
                        )
                    )
                    job = _Job(
                        job_id,
                        edict.id,
                        "once",
                        task=task,
                        next_run=schedule.at,
                        initial_memorial_id=memorial_id,
                    )
                    self._jobs[job_id] = job
                    # Persist for restart recovery
                    if not replay_safe:
                        self._storage.save_scheduler_job(
                            job_id,
                            edict.id,
                            "once",
                            next_run=schedule.at,
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
                tz_name = schedule.timezone or "UTC"
                next_run = _next_cron_utc(schedule.cron, tz_name)
                if replay_safe and not self._storage.save_scheduler_job_if_absent(
                    job_id,
                    edict.id,
                    "cron",
                    cron_expr=schedule.cron,
                    next_run=next_run,
                ):
                    return job_id
                task = asyncio.create_task(
                    self._cron_loop(
                        edict,
                        schedule.cron,
                        job_id,
                        tz_name,
                        initial_memorial_id=memorial_id,
                    )
                )
                job = _Job(
                    job_id,
                    edict.id,
                    "cron",
                    task=task,
                    next_run=next_run,
                    initial_memorial_id=memorial_id,
                )
                self._jobs[job_id] = job
                # Persist cron jobs for restart recovery
                if not replay_safe:
                    self._storage.save_scheduler_job(
                        job_id,
                        edict.id,
                        "cron",
                        cron_expr=schedule.cron,
                        next_run=next_run,
                    )
        elif schedule.type == "interval":
            if not schedule.interval_seconds or schedule.interval_seconds < 1:
                logger.warning(
                    "Interval edict %s has no interval_seconds, falling back to immediate",
                    edict.id,
                )
                job = _Job(job_id, edict.id, "interval")
                self._jobs[job_id] = job
                await self._emit_scheduled(edict, memorial_id=memorial_id)
            else:
                next_run = datetime.now(UTC) + timedelta(seconds=schedule.interval_seconds)
                if replay_safe and not self._storage.save_scheduler_job_if_absent(
                    job_id,
                    edict.id,
                    "interval",
                    interval_seconds=schedule.interval_seconds,
                    next_run=next_run,
                ):
                    return job_id
                task = asyncio.create_task(
                    self._interval_loop(
                        edict,
                        schedule.interval_seconds,
                        job_id,
                        initial_memorial_id=memorial_id,
                    )
                )
                job = _Job(
                    job_id,
                    edict.id,
                    "interval",
                    task=task,
                    next_run=next_run,
                    initial_memorial_id=memorial_id,
                )
                self._jobs[job_id] = job
                if not replay_safe:
                    self._storage.save_scheduler_job(
                        job_id,
                        edict.id,
                        "interval",
                        interval_seconds=schedule.interval_seconds,
                        next_run=next_run,
                    )

        return job_id

    async def _schedule_managed(
        self,
        edict: Edict,
        *,
        memorial_id: str | None,
        job_id: str | None,
        scheduled_at: datetime | None,
    ) -> str:
        """Persist a timer cursor, then let the preparer create executable truth."""
        job_id = job_id or str(ULID())
        schedule = edict.schedule
        now = (scheduled_at or self._clock()).astimezone(UTC)
        schedule_type = schedule.type
        cron_expr = schedule.cron
        interval_seconds = schedule.interval_seconds
        if schedule_type == "once":
            cursor = schedule.at.astimezone(UTC) if schedule.at is not None else now
        elif schedule_type == "cron" and cron_expr:
            cursor = _next_cron_utc(cron_expr, schedule.timezone)
        elif schedule_type == "interval" and interval_seconds and interval_seconds > 0:
            cursor = now + timedelta(seconds=interval_seconds)
        else:
            schedule_type = "immediate"
            cursor = now
            cron_expr = None
            interval_seconds = None
        inserted = self._storage.save_scheduler_job_if_absent(
            job_id,
            edict.id,
            schedule_type,
            cron_expr=cron_expr,
            next_run=cursor,
            interval_seconds=interval_seconds,
        )
        persisted = self._storage.get_scheduler_job(job_id)
        if persisted is None or persisted["edict_id"] != edict.id:
            raise RuntimeError("durable scheduler replay conflicts with job identity")
        if not inserted:
            if persisted["schedule_type"] != schedule_type or persisted["next_run"] is None:
                raise RuntimeError("durable scheduler replay conflicts with timer envelope")
            cursor = datetime.fromisoformat(str(persisted["next_run"])).astimezone(UTC)
        job = _Job(
            job_id,
            edict.id,
            schedule_type,
            next_run=cursor,
            initial_memorial_id=memorial_id,
        )
        self._jobs[job_id] = job
        if schedule_type == "immediate":
            await self._prepare_managed_fire(job_id, cursor, memorial_id)
            return job_id
        if inserted or job.task is None:
            job.task = asyncio.create_task(
                self._managed_job_loop(job_id, initial_memorial_id=memorial_id),
                name=f"scheduled-fire-{job_id}",
            )
        return job_id

    async def _prepare_managed_fire(
        self,
        job_id: str,
        scheduled_at: datetime,
        initial_memorial_id: str | None,
    ) -> None:
        preparer = self._scheduled_run_preparer
        if preparer is None:
            raise RuntimeError("managed scheduled-run preparer is not configured")
        prepared = preparer.prepare(
            job_id=job_id,
            scheduled_at=scheduled_at,
            initial_memorial_id=initial_memorial_id,
        )
        job = self._jobs.get(job_id)
        if job is not None:
            job.next_run = prepared.next_run
            job.initial_memorial_id = None
        if prepared.attempt_id is not None and self._run_reconciler is not None:
            await self._run_reconciler.reconcile_once()

    async def _managed_job_loop(
        self,
        job_id: str,
        *,
        initial_memorial_id: str | None,
    ) -> None:
        try:
            while self._running:
                row = self._storage.get_scheduler_job(job_id)
                if row is None or row["status"] != "active" or row["next_run"] is None:
                    return
                scheduled_at = datetime.fromisoformat(str(row["next_run"])).astimezone(UTC)
                delay = (scheduled_at - self._clock()).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._prepare_managed_fire(
                    job_id,
                    scheduled_at,
                    initial_memorial_id,
                )
                initial_memorial_id = None
        except asyncio.CancelledError:
            logger.info("Managed schedule loop cancelled for job %s", job_id)

    async def _restore_managed_jobs(self) -> None:
        for row in self._storage.list_active_scheduler_jobs():
            if row.get("next_run") is None:
                continue
            initial_memorial_id = self._restore_initial_memorial_id(
                row["edict_id"],
                row["job_id"],
            )
            job = _Job(
                row["job_id"],
                row["edict_id"],
                row["schedule_type"],
                next_run=datetime.fromisoformat(row["next_run"]),
                initial_memorial_id=initial_memorial_id,
            )
            job.task = asyncio.create_task(
                self._managed_job_loop(
                    job.job_id,
                    initial_memorial_id=initial_memorial_id,
                ),
                name=f"scheduled-fire-{job.job_id}",
            )
            self._jobs[job.job_id] = job

    async def _reattach_initial_memorial(
        self,
        job: _Job,
        edict: Edict,
        memorial_id: str,
    ) -> None:
        """Re-arm a restored durable timer with its replayed submission identity."""
        memorial = self._storage.get_memorial(memorial_id)
        if not memorial or memorial.status != TaskStatus.SUBMITTED:
            return
        if job.initial_memorial_id == memorial_id:
            return
        if job.initial_memorial_id is not None:
            raise RuntimeError("scheduler replay job belongs to another memorial")
        if job.task and not job.task.done():
            job.task.cancel()

        schedule = edict.schedule
        if schedule.type == "once" and schedule.at:
            delay = (schedule.at - datetime.now(UTC)).total_seconds()
            if delay <= 0:
                await self._emit_scheduled(edict, memorial_id=memorial_id)
                return
            job.task = asyncio.create_task(
                self._delayed_emit(
                    edict,
                    delay,
                    memorial_id=memorial_id,
                    job_id=job.job_id,
                )
            )
            job.next_run = schedule.at
        elif schedule.type == "cron" and schedule.cron:
            tz_name = schedule.timezone or "UTC"
            job.task = asyncio.create_task(
                self._cron_loop(
                    edict,
                    schedule.cron,
                    job.job_id,
                    tz_name,
                    initial_memorial_id=memorial_id,
                )
            )
            job.next_run = _next_cron_utc(schedule.cron, tz_name)
        elif schedule.type == "interval" and schedule.interval_seconds:
            job.task = asyncio.create_task(
                self._interval_loop(
                    edict,
                    schedule.interval_seconds,
                    job.job_id,
                    initial_memorial_id=memorial_id,
                )
            )
            job.next_run = datetime.now(UTC) + timedelta(seconds=schedule.interval_seconds)
        else:
            return
        job.initial_memorial_id = memorial_id

    async def cancel(self, job_id: str) -> None:
        row = self._storage.get_scheduler_job(job_id)
        job = self._jobs.pop(job_id, None)
        initial_memorial_id = job.initial_memorial_id if job else None
        if initial_memorial_id is None and row is not None:
            initial_memorial_id = self._restore_initial_memorial_id(
                row["edict_id"],
                job_id,
            )
        if job and job.task and not job.task.done():
            job.task.cancel()
        self._storage.delete_scheduler_job(job_id)
        if initial_memorial_id is not None:
            memorial = self._storage.get_memorial(initial_memorial_id)
            if memorial and memorial.status == TaskStatus.SUBMITTED:
                self._run_cancellation.cancel_root(
                    memorial.id,
                    reason=f"scheduler job {job_id} cancellation",
                )

    async def pause(self, job_id: str) -> bool:
        """Pause an active job: cancel its timer but keep it for resume."""
        row = self._storage.get_scheduler_job(job_id)
        if not row or row["status"] != "active":
            return False
        job = self._jobs.pop(job_id, None)
        if job and job.task and not job.task.done():
            job.task.cancel()
        self._storage.set_scheduler_job_status(job_id, "paused")
        return True

    async def resume(self, job_id: str) -> bool:
        """Resume a paused job: rebuild its timer from the persisted edict schedule."""
        row = self._storage.get_scheduler_job(job_id)
        if not row or row["status"] != "paused":
            return False
        edict = self._storage.get_edict(row["edict_id"])
        if not edict or edict.status.value != "open":
            self._storage.delete_scheduler_job(job_id)
            return False
        sched = edict.schedule
        initial_memorial_id = self._restore_initial_memorial_id(edict.id, job_id)
        if self._scheduled_run_preparer is not None:
            persisted_cursor = row.get("next_run")
            if persisted_cursor is None:
                return False
            managed_next_run = datetime.fromisoformat(str(persisted_cursor)).astimezone(UTC)
            self._storage.set_scheduler_job_status(job_id, "active")
            task = asyncio.create_task(
                self._managed_job_loop(
                    job_id,
                    initial_memorial_id=initial_memorial_id,
                ),
                name=f"scheduled-fire-{job_id}",
            )
            self._jobs[job_id] = _Job(
                job_id,
                edict.id,
                str(row["schedule_type"]),
                task=task,
                next_run=managed_next_run,
                initial_memorial_id=initial_memorial_id,
            )
            return True
        next_run: datetime | None = None
        if sched.type == "cron" and sched.cron:
            tz_name = sched.timezone or "UTC"
            next_run = _next_cron_utc(sched.cron, tz_name)
            task = asyncio.create_task(
                self._cron_loop(
                    edict,
                    sched.cron,
                    job_id,
                    tz_name,
                    initial_memorial_id=initial_memorial_id,
                )
            )
            self._jobs[job_id] = _Job(
                job_id,
                edict.id,
                "cron",
                task=task,
                next_run=next_run,
                initial_memorial_id=initial_memorial_id,
            )
        elif sched.type == "interval" and sched.interval_seconds:
            next_run = datetime.now(UTC) + timedelta(seconds=sched.interval_seconds)
            task = asyncio.create_task(
                self._interval_loop(
                    edict,
                    sched.interval_seconds,
                    job_id,
                    initial_memorial_id=initial_memorial_id,
                )
            )
            self._jobs[job_id] = _Job(
                job_id,
                edict.id,
                "interval",
                task=task,
                next_run=next_run,
                initial_memorial_id=initial_memorial_id,
            )
        elif sched.type == "once" and sched.at:
            delay = (sched.at - datetime.now(UTC)).total_seconds()
            if delay <= 0:
                await self._emit_scheduled(edict, memorial_id=initial_memorial_id)
                self._storage.delete_scheduler_job(job_id)
                return True
            next_run = sched.at
            task = asyncio.create_task(
                self._delayed_emit(
                    edict,
                    delay,
                    memorial_id=initial_memorial_id,
                    job_id=job_id,
                )
            )
            self._jobs[job_id] = _Job(
                job_id,
                edict.id,
                "once",
                task=task,
                next_run=next_run,
                initial_memorial_id=initial_memorial_id,
            )
        else:
            return False
        self._storage.set_scheduler_job_status(job_id, "active")
        self._storage.update_scheduler_job_next_run(job_id, next_run)
        return True

    async def run_now(
        self,
        job_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> bool:
        """Immediately fire a job's edict once, without altering its schedule."""
        row = self._storage.get_scheduler_job(job_id)
        edict_id = (
            row["edict_id"]
            if row
            else (self._jobs[job_id].edict_id if job_id in self._jobs else None)
        )
        if not edict_id:
            return False
        edict = self._storage.get_edict(edict_id)
        if not edict or edict.status.value != "open":
            return False
        if self._scheduled_run_preparer is not None:
            if idempotency_key is None or not idempotency_key.strip():
                raise ValueError("run_now idempotency_key is required in managed mode")
            prepared = self._scheduled_run_preparer.prepare_manual(
                job_id=job_id,
                idempotency_key=idempotency_key,
                scheduled_at=self._clock(),
            )
            if prepared.attempt_id is not None and self._run_reconciler is not None:
                await self._run_reconciler.reconcile_once()
            return True
        await self._emit_scheduled(edict)
        return True

    async def list_jobs(self) -> list[dict]:
        """List persisted jobs (active + paused), merging live in-memory next_run."""
        rows = self._storage.list_scheduler_jobs(statuses=("active", "paused"))
        out: list[dict] = []
        for row in rows:
            jid = row["job_id"]
            mem = self._jobs.get(jid)
            next_run = mem.next_run.isoformat() if (mem and mem.next_run) else row.get("next_run")
            out.append(
                {
                    "job_id": jid,
                    "edict_id": row["edict_id"],
                    "schedule_type": row["schedule_type"],
                    "status": row["status"],
                    "next_run": next_run,
                    "cron_expr": row.get("cron_expr"),
                    "interval_seconds": row.get("interval_seconds"),
                }
            )
        return out

    async def handle_submitted(self, event: EventEnvelope) -> None:
        """EventBus handler for edict.submitted."""
        edict_id = event.edict_id
        if not edict_id:
            return
        edict = self._storage.get_edict(edict_id)
        if not edict:
            logger.error("Scheduler: edict %s not found", edict_id)
            return
        await self.schedule(
            edict,
            memorial_id=event.memorial_id,
            job_id=submission_job_id(event.event_id),
            scheduled_at=event.timestamp,
        )

    def _skip_for_concurrency(self, edict: Edict) -> bool:
        """周期任务并发去重（Multica 借鉴 #2-A）：policy=skip 且上次未结束 → 跳过本次。"""
        policy = edict.schedule.concurrency_policy if edict.schedule else "skip"
        if policy == "allow":
            return False
        return self._storage.has_unfinished_memorials(edict.id)

    async def _fire_scheduled(self, edict: Edict, kind: str) -> None:
        """周期任务单次触发：并发去重 + 记录 schedule_run 台账（Multica 借鉴 #2-A/C）。"""
        if self._skip_for_concurrency(edict):
            logger.info(
                "%s job: skip fire, edict %s still has unfinished run (policy=skip)",
                kind,
                edict.id,
            )
            self._storage.create_schedule_run(
                source=edict.id,
                kind=kind,
                status="skipped",
                edict_id=edict.id,
            )
            return
        await self._emit_scheduled(edict)
        self._storage.create_schedule_run(
            source=edict.id,
            kind=kind,
            status="fired",
            edict_id=edict.id,
        )

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

    async def _cron_loop(
        self,
        edict: Edict,
        cron_expr: str,
        job_id: str,
        tz_name: str = "UTC",
        *,
        initial_memorial_id: str | None = None,
    ) -> None:
        """Repeatedly emit edict.scheduled at cron intervals (in given timezone)."""
        try:
            while self._running:
                next_run = _next_cron_utc(cron_expr, tz_name)
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
                if initial_memorial_id is not None:
                    await self._emit_scheduled(
                        fresh_edict,
                        memorial_id=initial_memorial_id,
                    )
                    initial_memorial_id = None
                else:
                    await self._fire_scheduled(fresh_edict, "cron")
                if job_id in self._jobs:
                    next_dt = _next_cron_utc(cron_expr, tz_name)
                    self._jobs[job_id].next_run = next_dt
                    self._storage.update_scheduler_job_next_run(job_id, next_dt)
        except asyncio.CancelledError:
            logger.info("Cron loop cancelled for edict %s", edict.id)

    async def _interval_loop(
        self,
        edict: Edict,
        interval_seconds: int,
        job_id: str,
        *,
        initial_memorial_id: str | None = None,
    ) -> None:
        """Repeatedly emit edict.scheduled every interval_seconds (each fire = fresh memorial)."""
        try:
            while self._running:
                await asyncio.sleep(interval_seconds)
                fresh_edict = self._storage.get_edict(edict.id)
                if not fresh_edict or fresh_edict.status.value != "open":
                    logger.info(
                        "Interval job %s stopped: edict %s is no longer open",
                        job_id,
                        edict.id,
                    )
                    break
                if initial_memorial_id is not None:
                    await self._emit_scheduled(
                        fresh_edict,
                        memorial_id=initial_memorial_id,
                    )
                    initial_memorial_id = None
                else:
                    await self._fire_scheduled(fresh_edict, "interval")
                if job_id in self._jobs:
                    next_dt = datetime.now(UTC) + timedelta(seconds=interval_seconds)
                    self._jobs[job_id].next_run = next_dt
                    self._storage.update_scheduler_job_next_run(job_id, next_dt)
        except asyncio.CancelledError:
            logger.info("Interval loop cancelled for edict %s", edict.id)

    async def _delayed_emit(
        self,
        edict: Edict,
        delay: float,
        *,
        memorial_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        try:
            await asyncio.sleep(delay)
            await self._emit_scheduled(edict, memorial_id=memorial_id)
            if job_id is not None:
                self._storage.delete_scheduler_job(job_id)
                self._jobs.pop(job_id, None)
        except asyncio.CancelledError:
            logger.info("Delayed schedule cancelled for edict %s", edict.id)
