"""Tests for Scheduler."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.edict import EdictSchedule
from tianshu.models.events import make_event
from tianshu.scheduler.scheduler import Scheduler


class TestScheduler:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def scheduler(self, event_bus, storage):
        return Scheduler(event_bus=event_bus, storage=storage)

    async def test_immediate_schedule(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")

        edict = Edict(goal="do now")
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)

        assert job_id
        handler.assert_called_once()

    async def test_once_past_time(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")

        past = datetime.now(UTC) - timedelta(hours=1)
        edict = Edict(
            goal="past task",
            schedule=EdictSchedule(type="once", at=past),
        )
        storage.save_edict(edict)
        await scheduler.schedule(edict)

        handler.assert_called_once()

    async def test_once_no_at(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")

        edict = Edict(
            goal="once no at",
            schedule=EdictSchedule(type="once"),
        )
        storage.save_edict(edict)
        await scheduler.schedule(edict)

        handler.assert_called_once()

    async def test_future_once_preserves_initial_memorial_identity(
        self,
        scheduler,
        event_bus,
        storage,
    ):
        delivered = asyncio.Event()
        received = []

        async def capture(event):
            received.append(event)
            delivered.set()

        event_bus.on("edict.scheduled", capture, consumer_name="test.initial_once.v1")
        edict = Edict(
            goal="future durable submission",
            schedule=EdictSchedule(
                type="once",
                at=datetime.now(UTC) + timedelta(milliseconds=20),
            ),
        )
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            instruction=edict.goal,
            status=TaskStatus.SUBMITTED,
        )
        storage.save_memorial(memorial)

        job_id = await scheduler.schedule(edict, memorial_id=memorial.id)
        await asyncio.wait_for(delivered.wait(), timeout=1)

        assert received[0].memorial_id == memorial.id
        assert storage.get_memorial(memorial.id).status == TaskStatus.SCHEDULED
        await scheduler.cancel(job_id)

    async def test_cron_first_fire_uses_initial_memorial_before_concurrency_guard(
        self,
        scheduler,
        event_bus,
        storage,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "tianshu.scheduler.scheduler._next_cron_utc",
            lambda *_args: datetime.now(UTC) + timedelta(milliseconds=20),
        )
        delivered = asyncio.Event()
        received = []

        async def capture(event):
            received.append(event)
            scheduler._running = False
            delivered.set()

        event_bus.on("edict.scheduled", capture, consumer_name="test.initial_cron.v1")
        edict = Edict(
            goal="cron durable submission",
            schedule=EdictSchedule(type="cron", cron="* * * * *"),
        )
        storage.save_edict(edict)
        memorial = Memorial(
            edict_id=edict.id,
            instruction=edict.goal,
            status=TaskStatus.SUBMITTED,
        )
        storage.save_memorial(memorial)
        scheduler._running = True

        job_id = await scheduler.schedule(edict, memorial_id=memorial.id)
        try:
            await asyncio.wait_for(delivered.wait(), timeout=1)
        finally:
            scheduler._running = False
            await scheduler.cancel(job_id)

        assert received[0].memorial_id == memorial.id
        assert storage.get_memorial(memorial.id).status == TaskStatus.SCHEDULED

    async def test_cancel_job(self, scheduler, storage):
        edict = Edict(
            goal="future task",
            schedule=EdictSchedule(
                type="once",
                at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)
        await scheduler.cancel(job_id)
        jobs = await scheduler.list_jobs()
        assert len(jobs) == 0

    async def test_list_jobs(self, scheduler, storage):
        # list_jobs 现在只列持久化任务（once/cron/interval）；immediate 不持久化。
        edict = Edict(
            goal="test",
            schedule=EdictSchedule(
                type="once",
                at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )
        storage.save_edict(edict)
        await scheduler.schedule(edict)
        jobs = await scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["edict_id"] == edict.id
        assert jobs[0]["status"] == "active"

    async def test_handle_submitted(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")

        edict = Edict(goal="via event")
        storage.save_edict(edict)

        event = make_event("edict.submitted", edict_id=edict.id)
        await scheduler.handle_submitted(event)

        handler.assert_called_once()

    async def test_start_stop(self, scheduler):
        await scheduler.start()
        await scheduler.stop()

    async def test_cron_fallback_to_immediate(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")

        edict = Edict(
            goal="cron task",
            schedule=EdictSchedule(type="cron"),
        )
        storage.save_edict(edict)
        await scheduler.schedule(edict)
        handler.assert_called_once()


class TestNextCronUtc:
    """_next_cron_utc 时区计算辅助函数。"""

    def test_shanghai_offset_8h_from_utc(self):
        """Asia/Shanghai 16:20 → UTC 08:20（同一天或次日，差 8 小时）。"""
        from tianshu.scheduler.scheduler import _next_cron_utc

        sh = _next_cron_utc("20 16 * * *", "Asia/Shanghai")
        utc = _next_cron_utc("20 16 * * *", "UTC")
        # 同一表达式，两种时区下解释，UTC 时刻应差 8 小时（取绝对值，跨日时为 16h）
        delta_hours = abs((sh - utc).total_seconds() / 3600)
        assert delta_hours in (8, 16), (
            f"Asia/Shanghai 16:20 应比 UTC 16:20 早 8h；实得 {delta_hours}h"
        )
        assert sh.tzinfo is UTC
        assert sh.minute == 20

    def test_shanghai_cron_hour_in_utc(self):
        """16:20 北京时间 = 08:20 UTC，结果的 UTC 小时应为 8。"""
        from tianshu.scheduler.scheduler import _next_cron_utc

        result = _next_cron_utc("20 16 * * *", "Asia/Shanghai")
        assert result.hour == 8
        assert result.minute == 20

    def test_utc_explicit(self):
        from tianshu.scheduler.scheduler import _next_cron_utc

        result = _next_cron_utc("20 16 * * *", "UTC")
        assert result.hour == 16
        assert result.minute == 20

    def test_unknown_timezone_is_rejected(self):
        """非法时区必须显式失败，不能静默改变用户计划。"""
        from tianshu.scheduler.scheduler import _next_cron_utc

        with pytest.raises(ValueError, match="Bogus/Zone"):
            _next_cron_utc("20 16 * * *", "Bogus/Zone")

    def test_none_timezone_treated_as_utc(self):
        from tianshu.scheduler.scheduler import _next_cron_utc

        result = _next_cron_utc("20 16 * * *", None)
        assert result.hour == 16

    def test_returns_aware_utc_datetime(self):
        from tianshu.scheduler.scheduler import _next_cron_utc

        result = _next_cron_utc("0 0 * * *", "Asia/Shanghai")
        assert result.tzinfo is UTC


class TestSchedulerCronTimezone:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def scheduler(self, event_bus, storage):
        return Scheduler(event_bus=event_bus, storage=storage)

    async def test_cron_uses_schedule_timezone(self, scheduler, storage):
        """cron 敕令的 next_run 应按 schedule.timezone 计算（不是 UTC）。"""
        edict = Edict(
            goal="每天 16:20 北京时间推送",
            schedule=EdictSchedule(
                type="cron",
                cron="20 16 * * *",
                timezone="Asia/Shanghai",
            ),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)

        jobs = await scheduler.list_jobs()
        target = next(j for j in jobs if j["job_id"] == job_id)
        next_run = datetime.fromisoformat(target["next_run"])
        # 北京 16:20 = UTC 08:20
        assert next_run.hour == 8
        assert next_run.minute == 20

        await scheduler.cancel(job_id)

    async def test_cron_default_timezone_is_utc(self, scheduler, storage):
        """schedule.timezone 默认 UTC 时按 UTC 解释。"""
        edict = Edict(
            goal="每天 UTC 16:20",
            schedule=EdictSchedule(type="cron", cron="20 16 * * *"),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)

        jobs = await scheduler.list_jobs()
        target = next(j for j in jobs if j["job_id"] == job_id)
        next_run = datetime.fromisoformat(target["next_run"])
        assert next_run.hour == 16
        assert next_run.minute == 20

        await scheduler.cancel(job_id)


class TestSchedulerJobControl:
    """interval / pause / resume / run_now —— 调度工具依赖的能力。"""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def scheduler(self, event_bus, storage):
        return Scheduler(event_bus=event_bus, storage=storage)

    async def test_interval_persisted_and_listed(self, scheduler, storage):
        edict = Edict(
            goal="周期巡检",
            schedule=EdictSchedule(type="interval", interval_seconds=3600),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)
        row = storage.get_scheduler_job(job_id)
        assert row["schedule_type"] == "interval"
        assert row["interval_seconds"] == 3600
        jobs = await scheduler.list_jobs()
        assert any(j["job_id"] == job_id and j["schedule_type"] == "interval" for j in jobs)
        await scheduler.cancel(job_id)

    async def test_pause_and_resume(self, scheduler, storage):
        edict = Edict(
            goal="可暂停",
            schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)

        assert await scheduler.pause(job_id) is True
        assert storage.get_scheduler_job(job_id)["status"] == "paused"
        # paused 任务仍在 list_jobs 可见
        jobs = await scheduler.list_jobs()
        target = next(j for j in jobs if j["job_id"] == job_id)
        assert target["status"] == "paused"
        # 重复 pause 失败
        assert await scheduler.pause(job_id) is False
        # resume 恢复为 active 并重建定时器
        assert await scheduler.resume(job_id) is True
        assert storage.get_scheduler_job(job_id)["status"] == "active"

        await scheduler.cancel(job_id)

    async def test_failed_job_can_be_resumed_from_persisted_schedule(self, scheduler, storage):
        edict = Edict(
            goal="故障后恢复",
            schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)
        storage.set_scheduler_job_status(job_id, "failed")

        assert await scheduler.resume(job_id) is True
        assert storage.get_scheduler_job(job_id)["status"] == "active"

        await scheduler.cancel(job_id)

    async def test_run_now_emits_without_changing_schedule(
        self,
        scheduler,
        event_bus,
        storage,
    ):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler, consumer_name="test.edict_scheduled.v1")
        edict = Edict(
            goal="立即触发一次",
            schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
        )
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)
        handler.reset_mock()  # cron schedule 本身不 emit

        assert await scheduler.run_now(job_id) is True
        handler.assert_called_once()
        # run_now 不改调度，任务仍 active
        assert storage.get_scheduler_job(job_id)["status"] == "active"

        await scheduler.cancel(job_id)

    async def test_control_unknown_job_returns_false(self, scheduler):
        assert await scheduler.pause("nope") is False
        assert await scheduler.resume("nope") is False
        assert await scheduler.run_now("nope") is False


class TestSchedulerReadiness:
    """G1.5 readiness 契约:运行中且常驻后台任务全部存活。"""

    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def scheduler(self, event_bus, storage):
        return Scheduler(event_bus=event_bus, storage=storage)

    async def test_ready_lifecycle_start_dead_task_stop(self, scheduler):
        assert scheduler.is_ready is False  # 未 start
        await scheduler.start()
        assert scheduler.is_ready is True
        scheduler._orphan_sweep_task.cancel()
        import asyncio

        await asyncio.sleep(0)
        assert scheduler.is_ready is False  # 常驻任务死亡即不 ready
        await scheduler.stop()
        assert scheduler.is_ready is False


class TestSchedulerReadinessWindow:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.mark.asyncio
    async def test_not_ready_until_job_restore_completes(self, event_bus, storage):
        """start() 期间（_restore_jobs 未完成）不得报告 ready。"""
        scheduler = Scheduler(event_bus=event_bus, storage=storage)
        observed: list[bool] = []
        original = scheduler._restore_jobs

        async def _slow_restore():
            observed.append(scheduler.is_ready)  # 恢复进行中的 readiness 快照
            await original()

        scheduler._restore_jobs = _slow_restore  # type: ignore[method-assign]
        await scheduler.start()
        try:
            assert observed == [False], "任务恢复完成前不得 ready"
            assert scheduler.is_ready is True
        finally:
            await scheduler.stop()


class TestManagedSchedulerRecovery:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    async def test_overdue_interval_coalesces_to_latest_slot(self, event_bus, storage):
        now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
        edict = Edict(
            goal="coalesce missed runs",
            schedule=EdictSchedule(
                type="interval",
                interval_seconds=60,
                misfire_policy="coalesce",
            ),
        )
        storage.save_edict(edict)
        scheduler = Scheduler(event_bus, storage, clock=lambda: now)
        scheduled_at = now - timedelta(minutes=5, seconds=30)

        coalesced = scheduler._coalesce_due_cursor(
            {
                "edict_id": edict.id,
                "schedule_type": "interval",
                "interval_seconds": 60,
            },
            scheduled_at,
            now,
        )

        assert coalesced == now - timedelta(seconds=30)

    async def test_managed_timer_failure_becomes_visible_failed_state(
        self,
        event_bus,
        storage,
    ):
        now = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)

        class BrokenPreparer:
            def prepare(self, **_kwargs):
                raise RuntimeError("timer exploded")

        edict = Edict(
            goal="managed timer",
            schedule=EdictSchedule(type="interval", interval_seconds=60),
        )
        storage.save_edict(edict)
        storage.save_scheduler_job(
            "broken-job",
            edict.id,
            "interval",
            interval_seconds=60,
            next_run=now,
        )
        scheduler = Scheduler(
            event_bus,
            storage,
            scheduled_run_preparer=BrokenPreparer(),
            clock=lambda: now,
        )
        scheduler._running = True
        try:
            await scheduler._restore_managed_jobs()
            await asyncio.sleep(0.01)

            assert storage.get_scheduler_job("broken-job")["status"] == "failed"
            listed = await scheduler.list_jobs()
            assert (
                next(job for job in listed if job["job_id"] == "broken-job")["status"] == "failed"
            )
            assert storage.list_schedule_runs(source="broken-job")[0]["status"] == "failed"
        finally:
            await scheduler.stop()

    async def test_start_recovers_all_prior_running_system_ledger(self, event_bus, storage):
        run_id = storage.create_schedule_run(
            source="system.prior",
            kind="system",
            status="running",
        )
        scheduler = Scheduler(event_bus, storage)

        await scheduler.start()
        try:
            run = storage.list_schedule_runs(source="system.prior")[0]
            assert run["id"] == run_id
            assert run["status"] == "failed"
            assert run["finished_at"] is not None
        finally:
            await scheduler.stop()
