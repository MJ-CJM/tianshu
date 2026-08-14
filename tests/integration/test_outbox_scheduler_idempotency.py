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
from tianshu.models.common import TaskStatus
from tianshu.models.edict import EdictSchedule
from tianshu.models.events import EventEnvelope
from tianshu.models.memorial import Memorial
from tianshu.scheduler.scheduler import Scheduler, submission_job_id
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_CONSUMER_NAME = "scheduler.edict_submitted.v1"
#: once 用例的提前量。窗口只需覆盖 save_edict + schedule() 这两步（建库与
#: init_db 已移到起算之前），1 秒对 CI 慢盘也绰绰有余，又不至于拖慢用例。
_ONCE_LEAD_SECONDS = 1.0


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


#: once 作业的调度窗。断言要在 job 触发「之前」跑完，窗口太窄会让测试在慢机器上
#: 假失败（issue #65）。给足余量，代价只是每例多等几秒。
SCHEDULING_WINDOW_SECONDS = 3


def _only_job(scheduler: Scheduler, context: str):
    """取唯一在册作业；空集合时给出可读原因而不是晦涩的 StopIteration。"""
    jobs = list(scheduler._jobs.values())  # noqa: SLF001
    assert jobs, (
        f"{context}: scheduler has no registered job — it most likely fired before "
        f"the assertion ran. Widen SCHEDULING_WINDOW_SECONDS."
    )
    return jobs[0]


def _register_scheduler(bus: EventBus, storage: Storage) -> Scheduler:
    scheduler = Scheduler(event_bus=bus, storage=storage)
    bus.on(
        "edict.submitted",
        scheduler.handle_submitted,
        consumer_name=_CONSUMER_NAME,
    )
    return scheduler


def _save_submission(
    storage: Storage,
    edict: Edict,
    memorial: Memorial,
    event_id: str,
) -> EventEnvelope:
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_id=event_id,
        event_type="edict.submitted",
        edict_id=edict.id,
        memorial_id=memorial.id,
        timestamp=datetime.now(UTC),
        producer="tests",
    )
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(unit_of_work.connection, event)
        unit_of_work.commit()
    return event


@pytest.mark.asyncio
async def test_future_once_first_fire_is_not_restored_after_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "scheduler-once-terminal.sqlite3"
    job_id = "future-once-terminal-job"

    first_storage = Storage(str(database_path))
    first_storage.init_db()
    first_bus = EventBus()
    first_scheduler = _register_scheduler(first_bus, first_storage)
    first_delivery = asyncio.Event()

    async def capture_first(_event: EventEnvelope) -> None:
        first_delivery.set()

    first_bus.on(
        "edict.scheduled",
        capture_first,
        consumer_name="tests.scheduler_once_terminal.first.v1",
    )
    await first_scheduler.start()

    # 时间窗必须在建库/init_db（跑 migration）之后才起算：Scheduler.schedule
    # 对 once 的分派看的是 `schedule.at - now() <= 0`，一旦到点即走"立即触发"
    # 分支——那条路只建内存 _Job **不落库**，后面 get_scheduler_job 自然为 None。
    # 原先在 init_db 之前就取 now()+100ms，CI 慢盘上 migration 足以吃光窗口，
    # 于是间歇性走成立即触发（本地飞快则始终走延迟路径，故只在 CI 复现）。
    target = datetime.now(UTC) + timedelta(seconds=_ONCE_LEAD_SECONDS)
    edict = Edict(
        goal="fire a durable once schedule only once",
        schedule=EdictSchedule(type="once", at=target),
    )
    first_storage.save_edict(edict)

    try:
        await first_scheduler.schedule(edict, job_id=job_id)
        # 落库即证明走的是延迟路径；随后才是本用例真正要验的"触发一次即终结"。
        assert first_storage.get_scheduler_job(job_id) is not None, (
            "once job 未落库，说明 schedule() 走了立即触发分支，时间窗被吃光"
        )
        await asyncio.wait_for(first_delivery.wait(), timeout=_ONCE_LEAD_SECONDS + 5)
        row = first_storage.get_scheduler_job(job_id)
        assert row is not None
        assert row["status"] != "active"
    finally:
        await first_scheduler.stop()
        await asyncio.sleep(0)
        first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    second_bus = EventBus()
    second_scheduler = _register_scheduler(second_bus, second_storage)
    second_deliveries: list[EventEnvelope] = []

    async def capture_second(event: EventEnvelope) -> None:
        second_deliveries.append(event)

    second_bus.on(
        "edict.scheduled",
        capture_second,
        consumer_name="tests.scheduler_once_terminal.second.v1",
    )
    await second_scheduler.start()

    try:
        await asyncio.sleep(0.1)
        assert second_deliveries == []
        assert second_storage.list_active_scheduler_jobs() == []
    finally:
        await second_scheduler.stop()
        await asyncio.sleep(0)
        second_storage.close()


@pytest.mark.parametrize("schedule_type", ["once", "cron", "interval"])
@pytest.mark.parametrize("storage_mode", ["file", "memory"])
@pytest.mark.asyncio
async def test_pause_resume_preserves_authoritative_initial_memorial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schedule_type: str,
    storage_mode: str,
) -> None:
    if schedule_type == "cron":
        monkeypatch.setattr(
            "tianshu.scheduler.scheduler._next_cron_utc",
            lambda *_args: datetime.now(UTC) + timedelta(milliseconds=50),
        )
    schedule = {
        "once": EdictSchedule(
            type="once",
            at=datetime.now(UTC) + timedelta(milliseconds=300),
        ),
        "cron": EdictSchedule(type="cron", cron="* * * * *", timezone="UTC"),
        "interval": EdictSchedule(type="interval", interval_seconds=1),
    }[schedule_type]
    event_id = f"pause-resume-{schedule_type}-event"
    database = (
        str(tmp_path / f"pause-resume-{schedule_type}.sqlite3")
        if storage_mode == "file"
        else ":memory:"
    )
    storage = Storage(database)
    storage.init_db()
    edict = Edict(goal=f"resume {schedule_type}", schedule=schedule)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    event = _save_submission(storage, edict, memorial, event_id)
    bus = EventBus()
    scheduler = _register_scheduler(bus, storage)
    delivered = asyncio.Event()
    received: list[EventEnvelope] = []

    async def capture(scheduled: EventEnvelope) -> None:
        received.append(scheduled)
        delivered.set()

    bus.on(
        "edict.scheduled",
        capture,
        consumer_name=f"tests.pause_resume_{schedule_type}.v1",
    )
    await scheduler.start()
    job_id = submission_job_id(event_id)

    try:
        await scheduler.handle_submitted(event)
        assert await scheduler.pause(job_id) is True
        assert storage.get_scheduler_job(job_id)["status"] == "paused"
        assert storage.get_memorial(memorial.id).status == TaskStatus.SUBMITTED

        assert await scheduler.resume(job_id) is True
        await asyncio.wait_for(delivered.wait(), timeout=SCHEDULING_WINDOW_SECONDS + 3)

        assert received[0].memorial_id == memorial.id
        assert storage.get_memorial(memorial.id).status == TaskStatus.SCHEDULED
    finally:
        await scheduler.cancel(job_id)
        await scheduler.stop()
        await asyncio.sleep(0)
        storage.close()


@pytest.mark.parametrize("storage_mode", ["file", "memory"])
@pytest.mark.asyncio
async def test_cancel_terminalizes_unfired_initial_memorial(
    tmp_path: Path,
    storage_mode: str,
) -> None:
    database = (
        str(tmp_path / "cancel-initial-memorial.sqlite3") if storage_mode == "file" else ":memory:"
    )
    storage = Storage(database)
    storage.init_db()
    edict = Edict(
        goal="cancel durable submission before fire",
        schedule=EdictSchedule(
            type="once",
            at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    event_id = "cancel-initial-memorial-event"
    event = _save_submission(storage, edict, memorial, event_id)
    scheduler = _register_scheduler(EventBus(), storage)
    await scheduler.start()
    job_id = submission_job_id(event_id)

    try:
        await scheduler.handle_submitted(event)
        await scheduler.cancel(job_id)

        cancelled = storage.get_memorial(memorial.id)
        assert cancelled.status == TaskStatus.CANCELLED
        assert cancelled.completed_at is not None
        assert storage.has_unfinished_memorials(edict.id) is False
        assert storage.get_scheduler_job(job_id)["status"] == "cancelled"
    finally:
        await scheduler.stop()
        await asyncio.sleep(0)
        storage.close()


@pytest.mark.parametrize("schedule_type", ["once", "cron"])
@pytest.mark.asyncio
async def test_published_submission_restart_preserves_initial_memorial_until_first_fire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schedule_type: str,
) -> None:
    database_path = tmp_path / f"scheduler-published-restart-{schedule_type}.sqlite3"
    now = datetime.now(UTC)
    if schedule_type == "cron":
        monkeypatch.setattr(
            "tianshu.scheduler.scheduler._next_cron_utc",
            lambda *_args: datetime.now(UTC) + timedelta(seconds=SCHEDULING_WINDOW_SECONDS),
        )
    schedule = (
        EdictSchedule(type="once", at=now + timedelta(seconds=SCHEDULING_WINDOW_SECONDS))
        if schedule_type == "once"
        else EdictSchedule(type="cron", cron="* * * * *", timezone="UTC")
    )
    event_id = f"scheduler-published-restart-{schedule_type}-event"

    first_storage = Storage(str(database_path))
    first_storage.init_db()
    edict = Edict(goal=f"published {schedule_type}", schedule=schedule)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    first_storage.save_edict(edict)
    first_storage.save_memorial(memorial)
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id=edict.id,
                memorial_id=memorial.id,
                timestamp=now,
                producer="tests",
            ),
        )
        unit_of_work.commit()
    first_repository = OutboxRepository(first_storage.unit_of_work)
    first_bus = EventBus()
    first_scheduler = _register_scheduler(first_bus, first_storage)
    await first_scheduler.start()
    dispatcher = OutboxDispatcher(
        first_repository,
        first_bus,
        owner_id="first-worker",
        clock=lambda: now,
    )

    try:
        assert await dispatcher.drain_once() == 1
        record = first_repository.get(first_storage._conn, event_id)  # noqa: SLF001
        assert record is not None and record.status == "published"
        assert len(first_storage.list_active_scheduler_jobs()) == 1
    finally:
        await first_scheduler.stop()
        await asyncio.sleep(0)
        first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    second_bus = EventBus()
    second_scheduler = _register_scheduler(second_bus, second_storage)
    delivered = asyncio.Event()
    received: list[EventEnvelope] = []

    async def capture(event: EventEnvelope) -> None:
        received.append(event)
        delivered.set()

    second_bus.on(
        "edict.scheduled",
        capture,
        consumer_name=f"tests.scheduler_published_restart_{schedule_type}.v1",
    )
    await second_scheduler.start()

    try:
        await asyncio.wait_for(delivered.wait(), timeout=SCHEDULING_WINDOW_SECONDS + 3)
        assert received[0].memorial_id == memorial.id
        assert second_storage.get_memorial(memorial.id).status == TaskStatus.SCHEDULED
        expected_active_jobs = 0 if schedule_type == "once" else 1
        assert len(second_storage.list_active_scheduler_jobs()) == expected_active_jobs
    finally:
        await second_scheduler.stop()
        await asyncio.sleep(0)
        second_storage.close()


@pytest.mark.asyncio
async def test_restart_replay_reattaches_initial_memorial_to_restored_once_job(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "scheduler-memorial-replay.sqlite3"
    now = datetime.now(UTC)
    # 窗口要覆盖「建库→存敕令→写 outbox→重启调度器」整串耗时：CI 上这串
    # 常超 250ms，job 提前触发后 _jobs 变空，断言拿到的是 StopIteration。
    target = now + timedelta(seconds=SCHEDULING_WINDOW_SECONDS)
    event_id = "scheduler-memorial-replay-event"

    first_storage = Storage(str(database_path))
    first_storage.init_db()
    edict = Edict(
        goal="future once with durable memorial",
        schedule=EdictSchedule(type="once", at=target),
    )
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    first_storage.save_edict(edict)
    first_storage.save_memorial(memorial)
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id=edict.id,
                memorial_id=memorial.id,
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
    finally:
        await first_scheduler.stop()
        await asyncio.sleep(0)
        first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    second_repository = OutboxRepository(second_storage.unit_of_work)
    second_bus = EventBus()
    second_scheduler = _register_scheduler(second_bus, second_storage)
    delivered = asyncio.Event()
    received: list[EventEnvelope] = []

    async def capture(event: EventEnvelope) -> None:
        received.append(event)
        delivered.set()

    second_bus.on(
        "edict.scheduled",
        capture,
        consumer_name="tests.scheduler_memorial_replay.v1",
    )
    await second_scheduler.start()
    restarted_dispatcher = OutboxDispatcher(
        second_repository,
        second_bus,
        owner_id="second-worker",
        clock=lambda: now + timedelta(seconds=6),
        lease_seconds=5,
    )

    try:
        restored_task = _only_job(second_scheduler, "after restart").task
        assert await restarted_dispatcher.drain_once() == 1
        replayed_job = _only_job(second_scheduler, "after outbox replay")
        assert replayed_job.task is restored_task
        assert replayed_job.initial_memorial_id == memorial.id
        await asyncio.wait_for(delivered.wait(), timeout=SCHEDULING_WINDOW_SECONDS + 3)
        assert received[0].memorial_id == memorial.id
        assert second_storage.get_memorial(memorial.id).status == TaskStatus.SCHEDULED
    finally:
        await second_scheduler.stop()
        await asyncio.sleep(0)
        second_storage.close()


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
        first_job = _only_job(first_scheduler, "before restart")
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
        restored_task = _only_job(second_scheduler, "after restart").task
        assert restored_task is not None and not restored_task.done()
        assert await restarted_dispatcher.drain_once() == 1

        jobs = second_storage.list_active_scheduler_jobs()
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == first_jobs[0]["job_id"]
        assert len(second_scheduler._jobs) == 1  # noqa: SLF001
        live_job = _only_job(second_scheduler, "after outbox replay")
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
