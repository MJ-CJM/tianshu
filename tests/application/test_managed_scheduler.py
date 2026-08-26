"""Production scheduler commits a durable fire before dispatching it."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from tianshu.application.scheduled_runs import (
    PreparedFire,
    ScheduledFireBindingUnavailable,
    ScheduledRunPreparer,
)
from tianshu.bus.event_bus import EventBus
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models import Edict, EdictSchedule, Memorial
from tianshu.models.events import EventEnvelope
from tianshu.models.frozen_content import (
    FrozenContentViewsV1,
    FrozenSkillsViewV1,
    frozen_skills_view_digest,
)
from tianshu.scheduler.scheduler import Scheduler, submission_job_id
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.universe.router import ChallengerRouter, GenerationBindingUnavailable

_NOW = datetime(2026, 7, 16, 11, tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _frozen_views(source_digest: str) -> FrozenContentViewsV1:
    return FrozenContentViewsV1(
        skills=FrozenSkillsViewV1(
            source_digest=source_digest,
            effective_digest=frozen_skills_view_digest(
                skills={},
                load_all_entries=(),
            ),
            skills={},
        )
    )


class _ObservedViewFactory:
    def __init__(self, source_digest: str, *, failing: bool = True) -> None:
        self.source_digest = source_digest
        self.failing = failing
        self.calls = 0
        self.called = asyncio.Event()

    def __call__(self) -> FrozenContentViewsV1:
        self.calls += 1
        self.called.set()
        if self.failing:
            raise RuntimeError("private frozen view failure")
        return _frozen_views(self.source_digest)


class _ObservedReconciler:
    def __init__(self) -> None:
        self.calls = 0
        self.called = asyncio.Event()

    async def reconcile_once(self) -> int:
        self.calls += 1
        self.called.set()
        return 0


def _enforced_preparer(storage, factory: _ObservedViewFactory) -> ScheduledRunPreparer:
    resolver = SystemSnapshotResolver(
        kernel_facts=lambda: {
            "dependency_lock_hash": _digest("lock"),
            "tianshu_version": "test",
        },
        executor_digests=lambda: {},
        skills_digest=lambda: factory.source_digest,
        personas_digest=lambda: _digest("personas"),
        policy_rules_digest=lambda: _digest("policy-rules"),
        provider_profiles_digest=lambda: _digest("provider-profiles"),
    )
    return ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        ChallengerRouter(
            storage,
            snapshot_resolver=lambda: resolver,
            system_snapshot_strict=True,
            view_factory=factory,
            frozen_content_views=True,
            frozen_content_views_enforced=True,
        ),
        require_runtime_binding=True,
    )


def _bind_submission(
    storage,
    *,
    edict: Edict,
    memorial: Memorial,
    event_id: str,
) -> str:
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id=edict.id,
                memorial_id=memorial.id,
                timestamp=_NOW,
                producer="test",
                payload={"goal": edict.goal},
            ),
        )
        unit_of_work.commit()
    return submission_job_id(event_id)


class _Preparer:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[dict[str, object]] = []

    def prepare(self, **kwargs: object) -> PreparedFire:
        self.order.append("prepare")
        self.calls.append(kwargs)
        return PreparedFire(
            fire_id="fire-1",
            job_id=str(kwargs["job_id"]),
            edict_id="edict-1",
            scheduled_at=kwargs["scheduled_at"],
            next_run=None,
            status="prepared",
            memorial_id="root-1",
            attempt_id="attempt-1",
            schedule_run_id="schedule-run-1",
            deduplicated=False,
        )

    def prepare_manual(self, **kwargs: object) -> PreparedFire:
        self.order.append("prepare_manual")
        self.calls.append(kwargs)
        return PreparedFire(
            fire_id="manual-fire-1",
            job_id=str(kwargs["job_id"]),
            edict_id="edict-1",
            scheduled_at=kwargs["scheduled_at"],
            next_run=_NOW,
            status="prepared",
            memorial_id="manual-root-1",
            attempt_id="manual-attempt-1",
            schedule_run_id="manual-run-1",
            deduplicated=False,
        )


class _Reconciler:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def reconcile_once(self) -> int:
        self.order.append("dispatch")
        return 1


class _ReadinessGate:
    def __init__(self) -> None:
        self.available = False
        self.admission_calls = 0
        self.dispatch_calls = 0
        self.admission_observed = asyncio.Event()

    async def admit_durable_prepare(self) -> bool:
        self.admission_calls += 1
        self.admission_observed.set()
        return self.available

    async def reconcile_once(self) -> int:
        self.dispatch_calls += 1
        return 1


class _FlakyGenerationRouter:
    def __init__(self, storage) -> None:  # type: ignore[no-untyped-def]
        self._delegate = ChallengerRouter(storage)
        self.failure_observed = asyncio.Event()

    def assign_current(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self._delegate.assign_current(*args, **kwargs)

    def prebind_runtime_current(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not self.failure_observed.is_set():
            self.failure_observed.set()
            raise GenerationBindingUnavailable("generation_binding_unavailable")
        return self._delegate.prebind_runtime_current(*args, **kwargs)


async def test_immediate_fire_is_prepared_before_dispatch(storage) -> None:
    order: list[str] = []
    preparer = _Preparer(order)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=_Reconciler(order),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)

    await scheduler.schedule(
        edict,
        memorial_id=root.id,
        job_id="job-1",
        scheduled_at=_NOW,
    )

    assert order == ["prepare", "dispatch"]
    assert preparer.calls[0]["initial_memorial_id"] == root.id
    assert storage.get_scheduler_job("job-1")["next_run"] == _NOW.isoformat()


async def test_readiness_gate_preserves_immediate_job_then_recovers(storage) -> None:
    gate = _ReadinessGate()
    router = _FlakyGenerationRouter(storage)
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,  # type: ignore[arg-type]
    )
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=gate,
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    event_id = "readiness-gated-submission"
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id=edict.id,
                memorial_id=root.id,
                timestamp=_NOW,
                producer="test",
                payload={"goal": edict.goal},
            ),
        )
        unit_of_work.commit()
    job_id = submission_job_id(event_id)
    scheduler._running = True  # noqa: SLF001

    await scheduler.schedule(
        edict,
        memorial_id=root.id,
        job_id=job_id,
        scheduled_at=_NOW,
    )
    job = scheduler._jobs[job_id]  # noqa: SLF001
    try:
        await asyncio.sleep(0)
        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "active"
        assert durable["next_run"] == _NOW.isoformat()
        assert job.task is not None and not job.task.done()
        assert gate.admission_calls >= 1
        assert gate.dispatch_calls == 0
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 0
        )

        gate.available = True
        await asyncio.wait_for(router.failure_observed.wait(), timeout=2)
        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "active"
        assert durable["next_run"] == _NOW.isoformat()
        assert job.task is not None and not job.task.done()
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 0
        )

        await asyncio.wait_for(job.task, timeout=2)

        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "completed"
        assert durable["next_run"] is None
        assert gate.dispatch_calls == 1
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 1
        )
    finally:
        scheduler._running = False  # noqa: SLF001
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)


async def test_readiness_gate_precedes_overdue_cursor_coalesce(storage) -> None:
    gate = _ReadinessGate()
    preparer = _Preparer([])
    edict = Edict(
        id="edict-1",
        goal="periodic work",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            misfire_policy="coalesce",
        ),
    )
    storage.save_edict(edict)
    overdue = _NOW - timedelta(minutes=5)
    storage.save_scheduler_job(
        "job-1",
        edict.id,
        "interval",
        interval_seconds=60,
        next_run=overdue,
    )
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=gate,
        clock=lambda: _NOW,
    )
    scheduler._running = True  # noqa: SLF001
    task = asyncio.create_task(  # noqa: SLF001
        scheduler._managed_job_loop("job-1", initial_memorial_id=None)
    )
    try:
        await asyncio.sleep(0)
        assert gate.admission_calls == 1
        assert preparer.calls == []
        assert storage.get_scheduler_job("job-1")["next_run"] == overdue.isoformat()
    finally:
        scheduler._running = False  # noqa: SLF001
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_future_fire_rechecks_readiness_after_timer_sleep(storage) -> None:
    gate = _ReadinessGate()
    gate.available = True
    preparer = _Preparer([])
    future = _NOW + timedelta(seconds=0.05)
    storage.save_edict(Edict(id="edict-1", goal="future work"))
    storage.save_scheduler_job("job-1", "edict-1", "once", next_run=future)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=gate,
        clock=lambda: _NOW,
    )
    scheduler._running = True  # noqa: SLF001
    task = asyncio.create_task(  # noqa: SLF001
        scheduler._managed_job_loop("job-1", initial_memorial_id=None)
    )
    try:
        await gate.admission_observed.wait()
        gate.available = False
        await asyncio.sleep(0.1)

        assert gate.admission_calls >= 2
        assert preparer.calls == []
        durable = storage.get_scheduler_job("job-1")
        assert durable is not None
        assert durable["status"] == "active"
        assert durable["next_run"] == future.isoformat()
    finally:
        scheduler._running = False  # noqa: SLF001
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_enforced_interval_post_commit_failure_keeps_loop_alive_and_reconciles(
    storage,
) -> None:
    factory = _ObservedViewFactory(_digest("stable-skills"))
    reconciler = _ObservedReconciler()
    edict = Edict(
        id="interval-frozen-edict",
        goal="periodic frozen work",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "interval-frozen-job",
        edict.id,
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_enforced_preparer(storage, factory),
        run_reconciler=reconciler,
        clock=lambda: _NOW,
    )
    scheduler._running = True  # noqa: SLF001

    await scheduler._restore_managed_jobs()  # noqa: SLF001
    job = scheduler._jobs["interval-frozen-job"]  # noqa: SLF001
    try:
        await asyncio.wait_for(reconciler.called.wait(), timeout=2)
        await asyncio.sleep(0)

        durable = storage.get_scheduler_job("interval-frozen-job")
        assert durable is not None
        assert durable["status"] == "active"
        assert durable["next_run"] == (_NOW + timedelta(seconds=60)).isoformat()
        assert job.task is not None and not job.task.done()
        assert job.next_run == _NOW + timedelta(seconds=60)
        assert "interval-frozen-job" in scheduler._jobs  # noqa: SLF001
        assert reconciler.calls == 1

        runs = storage.list_schedule_runs(source="interval-frozen-job")
        assert len(runs) == 1
        assert runs[0]["status"] == "prepared"
        attempt = storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts"
        ).fetchone()
        assert attempt is not None and attempt["status"] == "claimable"
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='skills_view_binding_failed'"
            ).fetchone()[0]
            == 1
        )
    finally:
        scheduler._running = False  # noqa: SLF001
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)


async def test_enforced_once_post_commit_failure_consumes_initial_root_and_reconciles(
    storage,
) -> None:
    factory = _ObservedViewFactory(_digest("stable-skills"))
    reconciler = _ObservedReconciler()
    edict = Edict(
        id="once-frozen-edict",
        goal="one frozen run",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    root = Memorial(id="once-submitted-root", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    job_id = _bind_submission(
        storage,
        edict=edict,
        memorial=root,
        event_id="once-frozen-submission",
    )
    storage.save_scheduler_job(job_id, edict.id, "once", next_run=_NOW)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_enforced_preparer(storage, factory),
        run_reconciler=reconciler,
        clock=lambda: _NOW,
    )
    scheduler._running = True  # noqa: SLF001

    await scheduler._restore_managed_jobs()  # noqa: SLF001
    job = scheduler._jobs[job_id]  # noqa: SLF001
    try:
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=2)

        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "completed"
        assert durable["next_run"] is None
        assert job.initial_memorial_id is None
        assert reconciler.calls == 1
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id=?", (edict.id,)
            ).fetchone()[0]
            == 1
        )
        attempt = storage._conn.execute(  # noqa: SLF001
            "SELECT memorial_id, status FROM execution_attempts"
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == (root.id, "claimable")
        runs = storage.list_schedule_runs(source=job_id)
        assert len(runs) == 1
        assert runs[0]["status"] == "prepared"
    finally:
        scheduler._running = False  # noqa: SLF001
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)


async def test_enforced_once_audit_failure_rolls_back_then_recovers_same_initial_root(
    storage,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tianshu.scheduler.scheduler.MANAGED_READINESS_BACKOFF_SECONDS",
        0.01,
    )
    factory = _ObservedViewFactory(_digest("stable-skills"))
    reconciler = _ObservedReconciler()
    edict = Edict(
        id="once-audit-edict",
        goal="recover one frozen run",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    root = Memorial(id="once-audit-root", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    job_id = _bind_submission(
        storage,
        edict=edict,
        memorial=root,
        event_id="once-audit-submission",
    )
    storage.save_scheduler_job(job_id, edict.id, "once", next_run=_NOW)
    storage._conn.executescript(  # noqa: SLF001
        """
        CREATE TRIGGER reject_scheduler_skills_view_failure_outbox
        BEFORE INSERT ON outbox_events
        WHEN NEW.event_type='skills_view_binding_failed' BEGIN
            SELECT RAISE(ABORT, 'reject scheduler skills view failure outbox');
        END;
        """
    )
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_enforced_preparer(storage, factory),
        run_reconciler=reconciler,
        clock=lambda: _NOW,
    )
    scheduler._running = True  # noqa: SLF001

    await scheduler._restore_managed_jobs()  # noqa: SLF001
    job = scheduler._jobs[job_id]  # noqa: SLF001
    try:
        await asyncio.wait_for(factory.called.wait(), timeout=2)

        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "active"
        assert durable["next_run"] == _NOW.isoformat()
        assert job.initial_memorial_id == root.id
        assert job.task is not None and not job.task.done()
        assert reconciler.calls == 0
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
            ).fetchone()[0]
            == 0
        )

        factory.failing = False
        storage._conn.execute(  # noqa: SLF001
            "DROP TRIGGER reject_scheduler_skills_view_failure_outbox"
        )
        storage._conn.commit()  # noqa: SLF001

        await asyncio.wait_for(reconciler.called.wait(), timeout=2)
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=2)

        durable = storage.get_scheduler_job(job_id)
        assert durable is not None
        assert durable["status"] == "completed"
        assert durable["next_run"] is None
        assert job.initial_memorial_id is None
        assert factory.calls >= 2
        assert reconciler.calls == 1
        attempt = storage._conn.execute(  # noqa: SLF001
            "SELECT memorial_id, status FROM execution_attempts"
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == (root.id, "claimable")
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 1
        )
    finally:
        scheduler._running = False  # noqa: SLF001
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)


async def test_orphan_sweeper_is_diagnostic_only_in_managed_mode(storage) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    before = storage.get_memorial(root.id)

    await scheduler._recover_orphan(root)  # noqa: SLF001

    assert storage.get_memorial(root.id) == before


async def test_run_now_uses_explicit_stable_key_without_moving_cursor(storage) -> None:
    order: list[str] = []
    preparer = _Preparer(order)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=_Reconciler(order),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    storage.save_edict(edict)
    storage.save_scheduler_job("job-1", edict.id, "immediate", next_run=_NOW)

    assert await scheduler.run_now("job-1", idempotency_key="manual-1")

    assert order == ["prepare_manual", "dispatch"]
    assert preparer.calls[0]["idempotency_key"] == "manual-1"


async def test_run_now_post_commit_binding_failure_still_reconciles_durable_attempt(
    storage,
) -> None:
    factory = _ObservedViewFactory(_digest("stable-skills"))
    reconciler = _ObservedReconciler()
    next_run = _NOW + timedelta(minutes=5)
    edict = Edict(
        id="run-now-frozen-edict",
        goal="manual frozen work",
        schedule=EdictSchedule(type="interval", interval_seconds=300),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "run-now-frozen-job",
        edict.id,
        "interval",
        interval_seconds=300,
        next_run=next_run,
    )
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_enforced_preparer(storage, factory),
        run_reconciler=reconciler,
        clock=lambda: _NOW,
    )

    assert await scheduler.run_now(
        "run-now-frozen-job",
        idempotency_key="manual-frozen-1",
    )

    durable = storage.get_scheduler_job("run-now-frozen-job")
    assert durable is not None
    assert durable["next_run"] == next_run.isoformat()
    assert reconciler.calls == 1
    attempt = storage._conn.execute(  # noqa: SLF001
        "SELECT status FROM execution_attempts"
    ).fetchone()
    assert attempt is not None and attempt["status"] == "claimable"
    runs = storage.list_schedule_runs(source="run-now-frozen-job")
    assert len(runs) == 1
    assert runs[0]["kind"] == "run_now"
    assert runs[0]["status"] == "prepared"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_failed'"
        ).fetchone()[0]
        == 1
    )


async def test_immediate_terminal_event_replay_recovers_committed_binding_without_new_fire(
    storage,
) -> None:
    edict = Edict(id="terminal-replay-edict", goal="recover committed immediate fire")
    root = Memorial(id="terminal-replay-root", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    event_id = "terminal-replay-submission"
    job_id = _bind_submission(
        storage,
        edict=edict,
        memorial=root,
        event_id=event_id,
    )
    storage.save_scheduler_job(job_id, edict.id, "immediate", next_run=_NOW)
    factory = _ObservedViewFactory(_digest("stable-skills"))
    preparer = _enforced_preparer(storage, factory)

    with pytest.raises(ScheduledFireBindingUnavailable) as committed:
        preparer.prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )
    assert committed.value.prepared.deduplicated is False
    assert storage.get_scheduler_job(job_id)["status"] == "completed"

    factory.failing = False
    reconciler = _ObservedReconciler()
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=reconciler,
        clock=lambda: _NOW + timedelta(hours=1),
    )
    event = EventEnvelope(
        event_id=event_id,
        event_type="edict.submitted",
        edict_id=edict.id,
        memorial_id=root.id,
        timestamp=_NOW,
        producer="test",
        payload={"goal": edict.goal},
    )

    await scheduler.handle_submitted(event)

    assert reconciler.calls == 1
    assert scheduler._jobs[job_id].initial_memorial_id is None  # noqa: SLF001
    assert (
        storage._conn.execute("SELECT COUNT(*) FROM schedule_run").fetchone()[0]  # noqa: SLF001
        == 1
    )
    assert (
        storage._conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0]  # noqa: SLF001
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM system_audit_events WHERE action='skills_view_binding_recovered'"
        ).fetchone()[0]
        == 1
    )


async def test_run_now_gate_rejects_before_manual_prepare(storage) -> None:
    order: list[str] = []
    preparer = _Preparer(order)
    gate = _ReadinessGate()
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=gate,
        clock=lambda: _NOW,
    )
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_scheduler_job("job-1", "edict-1", "immediate", next_run=_NOW)

    assert not await scheduler.run_now("job-1", idempotency_key="manual-1")
    assert order == []
    assert preparer.calls == []
    assert gate.dispatch_calls == 0


async def test_managed_run_now_rejects_missing_stable_key(storage) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_scheduler_job("job-1", "edict-1", "immediate", next_run=_NOW)

    with pytest.raises(ValueError, match="idempotency"):
        await scheduler.run_now("job-1")


@pytest.mark.parametrize("schedule_type", ["cron", "interval", "once"])
async def test_managed_resume_restores_cursor_and_only_starts_managed_loop(
    storage,
    monkeypatch,
    schedule_type: str,
) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    if schedule_type == "cron":
        schedule = {"type": "cron", "cron": "0 9 * * *"}
        job_kwargs = {"cron_expr": "0 9 * * *"}
    elif schedule_type == "interval":
        schedule = {"type": "interval", "interval_seconds": 3600}
        job_kwargs = {"interval_seconds": 3600}
    else:
        schedule = {"type": "once", "at": (_NOW + timedelta(hours=2)).isoformat()}
        job_kwargs = {}
    edict = Edict(id="edict-1", goal="work", schedule=schedule)
    storage.save_edict(edict)
    persisted_cursor = _NOW + timedelta(hours=1)
    storage.save_scheduler_job(
        "job-1",
        edict.id,
        schedule_type,
        next_run=persisted_cursor,
        **job_kwargs,
    )
    storage.set_scheduler_job_status("job-1", "paused")
    calls: list[str] = []

    async def managed(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append("managed")

    async def legacy(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append("legacy")

    monkeypatch.setattr(scheduler, "_managed_job_loop", managed)
    monkeypatch.setattr(scheduler, "_cron_loop", legacy)
    monkeypatch.setattr(scheduler, "_interval_loop", legacy)
    monkeypatch.setattr(scheduler, "_delayed_emit", legacy)

    assert await scheduler.resume("job-1") is True
    await scheduler._jobs["job-1"].task  # noqa: SLF001

    assert calls == ["managed"]
    assert scheduler._jobs["job-1"].next_run == persisted_cursor  # noqa: SLF001
    assert storage.get_scheduler_job("job-1")["next_run"] == persisted_cursor.isoformat()
