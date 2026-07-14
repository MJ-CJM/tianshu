"""Scheduler side-effect replay after outbox consumer-ack loss."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tianshu.application.outbox import OutboxDispatcher
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
from tianshu.models.edict import EdictSchedule
from tianshu.models.events import EventEnvelope
from tianshu.scheduler.scheduler import Scheduler
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_CONSUMER_NAME = "scheduler.edict_submitted.v1"


class _FailFirstConsumptionAck:
    def __init__(self, repository: OutboxRepository) -> None:
        self._repository = repository
        self._failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def record_consumption(self, **kwargs: Any) -> bool:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated crash after scheduler effect")
        return self._repository.record_consumption(**kwargs)


def _register_scheduler(bus: EventBus, storage: Storage) -> Scheduler:
    scheduler = Scheduler(event_bus=bus, storage=storage)
    bus.on(
        "edict.submitted",
        scheduler.handle_submitted,
        consumer_name=_CONSUMER_NAME,
    )
    return scheduler


@pytest.mark.parametrize("schedule_type", ["once", "cron"])
async def test_future_schedule_replay_reuses_one_durable_job_and_timer_after_ack_crash(
    tmp_path: Path,
    schedule_type: str,
) -> None:
    database_path = tmp_path / f"scheduler-replay-{schedule_type}.sqlite3"
    now = datetime.now(UTC)
    schedule = (
        EdictSchedule(type="once", at=now + timedelta(days=7))
        if schedule_type == "once"
        else EdictSchedule(type="cron", cron="0 0 1 1 *", timezone="UTC")
    )
    event_id = f"scheduler-replay-{schedule_type}-event"

    first_storage = Storage(str(database_path))
    first_storage.init_db()
    edict = Edict(goal=f"future {schedule_type}", schedule=schedule)
    first_storage.save_edict(edict)
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id=edict.id,
                timestamp=now,
                producer="tests",
            ),
        )
        unit_of_work.commit()
    first_repository = OutboxRepository(first_storage.unit_of_work)
    first_bus = EventBus()
    first_scheduler = _register_scheduler(first_bus, first_storage)
    await first_scheduler.start()
    crashing_dispatcher = OutboxDispatcher(
        _FailFirstConsumptionAck(first_repository),
        first_bus,
        owner_id="first-worker",
        clock=lambda: now,
        lease_seconds=5,
    )

    try:
        with pytest.raises(RuntimeError, match="crash after scheduler effect"):
            await crashing_dispatcher.drain_once()
        first_jobs = first_storage.list_active_scheduler_jobs()
        assert len(first_jobs) == 1
        assert first_jobs[0]["job_id"].startswith("submitted-")
        assert len(first_jobs[0]["job_id"]) == len("submitted-") + 64
        assert len(first_scheduler._jobs) == 1  # noqa: SLF001 - one live timer proof
        first_job = next(iter(first_scheduler._jobs.values()))  # noqa: SLF001
        assert first_job.task is not None and not first_job.task.done()
    finally:
        await first_scheduler.stop()
        await asyncio.sleep(0)
        first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    second_repository = OutboxRepository(second_storage.unit_of_work)
    second_bus = EventBus()
    second_scheduler = _register_scheduler(second_bus, second_storage)
    await second_scheduler.start()
    restarted_dispatcher = OutboxDispatcher(
        second_repository,
        second_bus,
        owner_id="second-worker",
        clock=lambda: now + timedelta(seconds=6),
        lease_seconds=5,
    )

    try:
        assert len(second_scheduler._jobs) == 1  # noqa: SLF001 - restored timer proof
        restored_task = next(iter(second_scheduler._jobs.values())).task  # noqa: SLF001
        assert restored_task is not None and not restored_task.done()
        assert await restarted_dispatcher.drain_once() == 1

        jobs = second_storage.list_active_scheduler_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == first_jobs[0]["job_id"]
        assert len(second_scheduler._jobs) == 1  # noqa: SLF001
        live_job = next(iter(second_scheduler._jobs.values()))  # noqa: SLF001
        assert live_job.task is not None and not live_job.task.done()
        assert live_job.task is restored_task

        record = second_repository.get(second_storage._conn, event_id)  # noqa: SLF001
        assert record is not None
        assert (record.status, record.attempt_count) == ("published", 2)
        assert second_repository.consumed_consumers(event_id) == frozenset({_CONSUMER_NAME})
    finally:
        await second_scheduler.stop()
        await asyncio.sleep(0)
        second_storage.close()
