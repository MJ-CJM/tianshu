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
        edict = Edict(goal="test")
        storage.save_edict(edict)
        await scheduler.schedule(edict)
        jobs = await scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["edict_id"] == edict.id

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
