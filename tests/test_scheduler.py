"""Tests for Scheduler."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
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
        event_bus.on("edict.scheduled", handler)

        edict = Edict(goal="do now")
        storage.save_edict(edict)
        job_id = await scheduler.schedule(edict)

        assert job_id
        handler.assert_called_once()

    async def test_once_past_time(self, scheduler, event_bus, storage):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler)

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
        event_bus.on("edict.scheduled", handler)

        edict = Edict(
            goal="once no at",
            schedule=EdictSchedule(type="once"),
        )
        storage.save_edict(edict)
        await scheduler.schedule(edict)

        handler.assert_called_once()

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
                type="once", at=datetime.now(UTC) + timedelta(hours=1),
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
        event_bus.on("edict.scheduled", handler)

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
        event_bus.on("edict.scheduled", handler)

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

    def test_unknown_timezone_falls_back_to_utc(self, caplog):
        """非法时区不崩，回退 UTC + warn。"""
        import logging
        from tianshu.scheduler.scheduler import _next_cron_utc
        caplog.set_level(logging.WARNING)
        result = _next_cron_utc("20 16 * * *", "Bogus/Zone")
        assert result.hour == 16  # 退化为 UTC 行为
        assert any("Bogus/Zone" in r.message for r in caplog.records)

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
                type="cron", cron="20 16 * * *", timezone="Asia/Shanghai",
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
        assert any(
            j["job_id"] == job_id and j["schedule_type"] == "interval" for j in jobs
        )
        await scheduler.cancel(job_id)

    async def test_pause_and_resume(self, scheduler, storage):
        edict = Edict(
            goal="可暂停", schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
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

    async def test_run_now_emits_without_changing_schedule(
        self, scheduler, event_bus, storage,
    ):
        handler = AsyncMock()
        event_bus.on("edict.scheduled", handler)
        edict = Edict(
            goal="立即触发一次", schedule=EdictSchedule(type="cron", cron="0 9 * * *"),
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
