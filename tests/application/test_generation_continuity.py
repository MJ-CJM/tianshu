"""Runtime generation continuity at the managed attempt boundary."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from tests.universe.test_challenger_routing import _seed_memorial
from tests.universe.test_snapshot_binding import _snapshot_resolver

from tianshu.application.scheduled_runs import ScheduledRunPreparer
from tianshu.evolution.runtime_context import current_run_binding
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.models import Edict, EdictSchedule, Memorial
from tianshu.models.edict import EdictRuntime
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository
from tianshu.universe.router import (
    ChallengerRouter,
    GenerationBindingUnavailable,
    GenerationRetired,
)

_GENERATION_ONE = "rg-" + "1" * 32
_GENERATION_TWO = "rg-" + "2" * 32
_NOW = datetime(2026, 8, 26, 8, tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class _Selection:
    generation_ids: tuple[str, ...]
    by_scope: dict[str, str]
    executor_manifest_digests: dict[str, str]


@dataclass
class _Controller:
    active_ids: tuple[str, ...] = (_GENERATION_ONE,)
    manifests: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            _GENERATION_ONE: {"keqing:pi": _digest("pi-one")},
            _GENERATION_TWO: {"keqing:pi": _digest("pi-two")},
        }
    )
    fail_pinned: bool = False
    calls: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    releases: list[str] = field(default_factory=list)

    def resolve_for_binding_current(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
        attempt_id: str,
        *,
        pinned_ids: tuple[str, ...] = (),
        inherit_pinned: bool = False,
        allow_ready: bool = False,
    ) -> _Selection:
        del connection, inherit_pinned, allow_ready
        self.calls.append((memorial_id, attempt_id, pinned_ids))
        if pinned_ids and self.fail_pinned:
            raise RuntimeError("generation material is gone")
        selected = pinned_ids or self.active_ids
        if not selected:
            return _Selection((), {}, {"test": _digest("executor")})
        return _Selection(
            selected,
            {"executor:keqing:pi": selected[0]},
            dict(self.manifests[selected[0]]),
        )

    def release_binding(self, attempt_id: str) -> bool:
        self.releases.append(attempt_id)
        return True


def _router(storage, controller: _Controller) -> ChallengerRouter:
    return ChallengerRouter(
        storage,
        snapshot_resolver=lambda: _snapshot_resolver(),
        generation_controller=lambda: controller,
    )


def test_new_attempt_binds_active_generation_and_pinned_manifest(storage) -> None:
    _seed_memorial(storage)
    controller = _Controller()
    router = _router(storage, controller)
    router.assign("memorial-1")

    with router.bind_runtime("memorial-1", attempt_id="attempt-1"):
        binding = current_run_binding()
        assert binding is not None
        assert binding.generation_ids == (_GENERATION_ONE,)
        assert binding.system_snapshot is not None
        assert binding.system_snapshot.components["executor:keqing:pi"] == _digest("pi-one")

    assert controller.calls == [("memorial-1", "attempt-1", ())]


def test_exact_attempt_with_empty_legacy_binding_never_acquires_new_active_generation(
    storage,
) -> None:
    _seed_memorial(storage)
    legacy_router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: _snapshot_resolver(),
    )
    legacy_router.assign("memorial-1")
    with legacy_router.bind_runtime("memorial-1", attempt_id="attempt-legacy"):
        original = current_run_binding()
        assert original is not None
        assert original.generation_ids == ()

    controller = _Controller()
    router = _router(storage, controller)
    with router.bind_runtime("memorial-1", attempt_id="attempt-legacy"):
        replay = current_run_binding()
        assert replay == original

    assert controller.calls == []


def test_retry_attempt_inherits_previous_generation_without_rebucketing(storage) -> None:
    _seed_memorial(storage)
    controller = _Controller()
    router = _router(storage, controller)
    router.assign("memorial-1")
    with router.bind_runtime("memorial-1", attempt_id="attempt-1"):
        pass

    controller.active_ids = (_GENERATION_TWO,)
    with router.bind_runtime("memorial-1", attempt_id="attempt-2"):
        binding = current_run_binding()
        assert binding is not None
        assert binding.generation_ids == (_GENERATION_ONE,)

    assert controller.calls[-1] == (
        "memorial-1",
        "attempt-2",
        (_GENERATION_ONE,),
    )


@pytest.mark.parametrize(
    ("schedule", "job_kwargs"),
    [
        (
            EdictSchedule(
                type="interval",
                interval_seconds=60,
                concurrency_policy="allow",
            ),
            {"interval_seconds": 60},
        ),
        (
            EdictSchedule(
                type="cron",
                cron="0 * * * *",
                timezone="UTC",
                concurrency_policy="allow",
            ),
            {"cron_expr": "0 * * * *"},
        ),
    ],
)
def test_recurring_fire_pins_trigger_generation_before_dispatch(
    storage,
    schedule: EdictSchedule,
    job_kwargs: dict[str, object],
) -> None:
    edict = Edict(
        id="scheduled-edict",
        goal="scheduled Pi work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "scheduled-job",
        edict.id,
        schedule.type,
        next_run=_NOW,
        **job_kwargs,
    )
    controller = _Controller()
    router = _router(storage, controller)
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,
    )

    first = preparer.prepare(job_id="scheduled-job", scheduled_at=_NOW)
    assert first.memorial_id is not None and first.attempt_id is not None
    with storage.unit_of_work() as unit_of_work:
        first_binding = SystemSnapshotRepository().get_binding(
            unit_of_work.connection,
            memorial_id=first.memorial_id,
            attempt_id=first.attempt_id,
        )
        unit_of_work.commit()
    assert first_binding is not None
    assert first_binding.generation_ids == (_GENERATION_ONE,)
    assert controller.releases == [first.attempt_id]

    controller.active_ids = (_GENERATION_TWO,)
    with router.bind_runtime(first.memorial_id, attempt_id=first.attempt_id):
        dispatched = current_run_binding()
        assert dispatched is not None
        assert dispatched.generation_ids == (_GENERATION_ONE,)
    controller.release_binding(first.attempt_id)

    assert first.next_run is not None
    second = preparer.prepare(
        job_id="scheduled-job",
        scheduled_at=first.next_run,
    )
    assert second.memorial_id is not None and second.attempt_id is not None
    with storage.unit_of_work() as unit_of_work:
        second_binding = SystemSnapshotRepository().get_binding(
            unit_of_work.connection,
            memorial_id=second.memorial_id,
            attempt_id=second.attempt_id,
        )
        unit_of_work.commit()
    assert second_binding is not None
    assert second_binding.generation_ids == (_GENERATION_TWO,)
    assert controller.releases[-1] == second.attempt_id


def test_recurring_prebind_failure_rolls_back_entire_fire(storage) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id="scheduled-failure-edict",
        goal="scheduled Pi work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "scheduled-failure-job",
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """
            CREATE TRIGGER reject_scheduled_generation_binding
            BEFORE INSERT ON run_system_bindings BEGIN
                SELECT RAISE(ABORT, 'injected scheduled binding failure');
            END
            """
        )
        unit_of_work.commit()
    controller = _Controller()
    router = _router(storage, controller)
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,
    )

    with pytest.raises(GenerationBindingUnavailable, match="generation_binding_unavailable"):
        preparer.prepare(job_id="scheduled-failure-job", scheduled_at=_NOW)

    job = storage.get_scheduler_job("scheduled-failure-job")
    assert job is not None and job["next_run"] == _NOW.isoformat()
    with storage.unit_of_work() as unit_of_work:
        counts = {
            table: unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed test table names
            ).fetchone()[0]
            for table in (
                "memorials",
                "execution_attempts",
                "schedule_run",
                "run_system_bindings",
                "run_generation_bindings",
                "run_evolution_assignments",
            )
        }
        unit_of_work.commit()
    assert counts == {table: 0 for table in counts}
    assert len(controller.releases) == 1


def test_scheduler_empty_generation_is_durably_fixed_without_snapshot_resolver(storage) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id="scheduled-empty-generation-edict",
        goal="scheduled static work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "scheduled-empty-generation-job",
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    controller = _Controller(active_ids=())
    current_resolver: dict[str, SystemSnapshotResolver | None] = {"value": None}
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: current_resolver["value"],
        generation_controller=lambda: controller,
    )
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,
    )

    prepared = preparer.prepare(job_id="scheduled-empty-generation-job", scheduled_at=_NOW)
    assert prepared.memorial_id is not None and prepared.attempt_id is not None
    job = storage.get_scheduler_job("scheduled-empty-generation-job")
    assert job is not None and job["next_run"] == (_NOW + timedelta(seconds=60)).isoformat()
    with storage.unit_of_work() as unit_of_work:
        generation_binding = SystemSnapshotRepository().get_generation_binding(
            unit_of_work.connection,
            memorial_id=prepared.memorial_id,
            attempt_id=prepared.attempt_id,
        )
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[
                0
            ]
            == 0
        )
        unit_of_work.commit()
    assert generation_binding is not None
    assert generation_binding.generation_ids == ()

    current_resolver["value"] = _snapshot_resolver()
    controller.active_ids = (_GENERATION_ONE,)
    calls_before_dispatch = len(controller.calls)
    with router.bind_runtime(prepared.memorial_id, attempt_id=prepared.attempt_id):
        dispatch_binding = current_run_binding()
        assert dispatch_binding is not None
        assert dispatch_binding.generation_ids == ()
    assert len(controller.calls) == calls_before_dispatch


def test_scheduler_empty_generation_survives_system_snapshot_shadow_write_failure(storage) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id="scheduled-empty-shadow-edict",
        goal="scheduled static work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    storage.save_scheduler_job(
        "scheduled-empty-shadow-job",
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """
            CREATE TRIGGER reject_empty_generation_binding
            BEFORE INSERT ON run_system_bindings BEGIN
                SELECT RAISE(ABORT, 'injected empty binding failure');
            END
            """
        )
        unit_of_work.commit()
    controller = _Controller(active_ids=())
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        _router(storage, controller),
    )

    prepared = preparer.prepare(job_id="scheduled-empty-shadow-job", scheduled_at=_NOW)
    assert prepared.memorial_id is not None and prepared.attempt_id is not None

    job = storage.get_scheduler_job("scheduled-empty-shadow-job")
    assert job is not None and job["next_run"] == (_NOW + timedelta(seconds=60)).isoformat()
    with storage.unit_of_work() as unit_of_work:
        generation_binding = SystemSnapshotRepository().get_generation_binding(
            unit_of_work.connection,
            memorial_id=prepared.memorial_id,
            attempt_id=prepared.attempt_id,
        )
        system_binding_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM run_system_bindings"
        ).fetchone()[0]
        unit_of_work.commit()
    assert generation_binding is not None
    assert generation_binding.generation_ids == ()
    assert system_binding_count == 0


@pytest.mark.parametrize("failure_mode", ["missing_resolver", "shadow_write"])
def test_native_scheduler_pins_empty_generation_when_p1_shadow_is_unavailable(
    storage,
    failure_mode: str,
) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id=f"native-shadow-{failure_mode}-edict",
        goal="scheduled native work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="native"),
    )
    storage.save_edict(edict)
    job_id = f"native-shadow-{failure_mode}-job"
    storage.save_scheduler_job(
        job_id,
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    if failure_mode == "shadow_write":
        with storage.unit_of_work() as unit_of_work:
            unit_of_work.connection.execute(
                """
                CREATE TRIGGER reject_native_shadow_binding
                BEFORE INSERT ON run_system_bindings BEGIN
                    SELECT RAISE(ABORT, 'injected native shadow failure');
                END
                """
            )
            unit_of_work.commit()
    controller = _Controller(active_ids=())
    router = ChallengerRouter(
        storage,
        snapshot_resolver=(
            (lambda: None) if failure_mode == "missing_resolver" else (lambda: _snapshot_resolver())
        ),
        generation_controller=lambda: controller,
    )
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,
        require_runtime_binding=False,
    )

    prepared = preparer.prepare(job_id=job_id, scheduled_at=_NOW)

    assert prepared.attempt_id is not None
    job = storage.get_scheduler_job(job_id)
    assert job is not None and job["next_run"] == (_NOW + timedelta(seconds=60)).isoformat()
    with storage.unit_of_work() as unit_of_work:
        generation_binding = unit_of_work.connection.execute(
            "SELECT state, generation_ids_json FROM run_generation_bindings"
        ).fetchone()
        assert tuple(generation_binding) == ("bound", "[]")
        assert (
            unit_of_work.connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[
                0
            ]
            == 0
        )
        unit_of_work.commit()


@pytest.mark.parametrize("fire_kind", ["scheduled", "manual"])
@pytest.mark.parametrize("failure_mode", ["missing_resolver", "shadow_write"])
def test_required_scheduler_snapshot_shadow_failure_rolls_back_fresh_fire(
    storage,
    fire_kind: str,
    failure_mode: str,
) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id=f"required-shadow-{fire_kind}-{failure_mode}-edict",
        goal="scheduled native work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="native"),
    )
    storage.save_edict(edict)
    job_id = f"required-shadow-{fire_kind}-{failure_mode}-job"
    storage.save_scheduler_job(
        job_id,
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    if failure_mode == "shadow_write":
        with storage.unit_of_work() as unit_of_work:
            unit_of_work.connection.execute(
                """
                CREATE TRIGGER reject_required_shadow_binding
                BEFORE INSERT ON run_system_bindings BEGIN
                    SELECT RAISE(ABORT, 'injected required shadow failure');
                END
                """
            )
            unit_of_work.commit()
    controller = _Controller(active_ids=())
    router = ChallengerRouter(
        storage,
        snapshot_resolver=(
            (lambda: None) if failure_mode == "missing_resolver" else _snapshot_resolver
        ),
        generation_controller=lambda: controller,
    )
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        router,
        require_runtime_binding=True,
    )

    with pytest.raises(GenerationBindingUnavailable, match="generation_binding_unavailable"):
        if fire_kind == "scheduled":
            preparer.prepare(job_id=job_id, scheduled_at=_NOW)
        else:
            preparer.prepare_manual(
                job_id=job_id,
                idempotency_key="required-shadow-run-now",
                scheduled_at=_NOW,
            )

    job = storage.get_scheduler_job(job_id)
    assert job is not None
    assert job["status"] == "active"
    assert job["next_run"] == _NOW.isoformat()
    with storage.unit_of_work() as unit_of_work:
        counts = {
            table: unit_of_work.connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed test table names
            ).fetchone()[0]
            for table in (
                "memorials",
                "execution_attempts",
                "schedule_run",
                "run_system_bindings",
                "run_generation_bindings",
                "run_evolution_assignments",
            )
        }
        unit_of_work.commit()
    assert counts == {table: 0 for table in counts}


@pytest.mark.parametrize("fire_kind", ["scheduled", "manual"])
def test_terminal_scheduler_replay_never_retrofits_current_generation(
    storage,
    fire_kind: str,
) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id=f"terminal-{fire_kind}-edict",
        goal="historical Pi work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    job_id = f"terminal-{fire_kind}-job"
    storage.save_scheduler_job(
        job_id,
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    controller = _Controller()
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        _router(storage, controller),
    )

    if fire_kind == "scheduled":
        first = preparer.prepare(job_id=job_id, scheduled_at=_NOW)
    else:
        first = preparer.prepare_manual(
            job_id=job_id,
            idempotency_key="historical-run",
            scheduled_at=_NOW,
        )
    assert first.memorial_id is not None and first.attempt_id is not None
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "DELETE FROM run_system_bindings WHERE memorial_id=? AND attempt_id=?",
            (first.memorial_id, first.attempt_id),
        )
        unit_of_work.connection.execute(
            "DELETE FROM run_evolution_assignments WHERE memorial_id=?",
            (first.memorial_id,),
        )
        unit_of_work.connection.execute(
            "UPDATE execution_attempts SET status='succeeded' WHERE attempt_id=?",
            (first.attempt_id,),
        )
        unit_of_work.commit()

    controller.active_ids = (_GENERATION_TWO,)
    controller.calls.clear()
    controller.releases.clear()
    if fire_kind == "scheduled":
        replay = preparer.prepare(job_id=job_id, scheduled_at=_NOW)
    else:
        replay = preparer.prepare_manual(
            job_id=job_id,
            idempotency_key="historical-run",
            scheduled_at=_NOW + timedelta(minutes=5),
        )

    assert replay == first.model_copy(update={"deduplicated": True})
    with storage.unit_of_work() as unit_of_work:
        binding_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM run_system_bindings WHERE memorial_id=? AND attempt_id=?",
            (first.memorial_id, first.attempt_id),
        ).fetchone()[0]
        assignment_count = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM run_evolution_assignments WHERE memorial_id=?",
            (first.memorial_id,),
        ).fetchone()[0]
        unit_of_work.commit()
    assert binding_count == 0
    assert assignment_count == 0
    assert controller.calls == []
    assert controller.releases == []


@pytest.mark.parametrize("fire_kind", ["scheduled", "manual"])
def test_claimable_scheduler_replay_repairs_missing_generation_binding(
    storage,
    fire_kind: str,
) -> None:
    schedule = EdictSchedule(
        type="interval",
        interval_seconds=60,
        concurrency_policy="allow",
    )
    edict = Edict(
        id=f"claimable-{fire_kind}-edict",
        goal="unstarted Pi work",
        submitter="principal-1",
        schedule=schedule,
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    job_id = f"claimable-{fire_kind}-job"
    storage.save_scheduler_job(
        job_id,
        edict.id,
        schedule.type,
        interval_seconds=60,
        next_run=_NOW,
    )
    controller = _Controller()
    preparer = ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        _router(storage, controller),
    )

    if fire_kind == "scheduled":
        first = preparer.prepare(job_id=job_id, scheduled_at=_NOW)
    else:
        first = preparer.prepare_manual(
            job_id=job_id,
            idempotency_key="unstarted-run",
            scheduled_at=_NOW,
        )
    assert first.memorial_id is not None and first.attempt_id is not None
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "DELETE FROM run_system_bindings WHERE memorial_id=? AND attempt_id=?",
            (first.memorial_id, first.attempt_id),
        )
        unit_of_work.connection.execute(
            "DELETE FROM run_evolution_assignments WHERE memorial_id=?",
            (first.memorial_id,),
        )
        unit_of_work.commit()

    controller.active_ids = (_GENERATION_TWO,)
    controller.calls.clear()
    controller.releases.clear()
    if fire_kind == "scheduled":
        replay = preparer.prepare(job_id=job_id, scheduled_at=_NOW)
    else:
        replay = preparer.prepare_manual(
            job_id=job_id,
            idempotency_key="unstarted-run",
            scheduled_at=_NOW + timedelta(minutes=5),
        )

    assert replay == first.model_copy(update={"deduplicated": True})
    with storage.unit_of_work() as unit_of_work:
        binding = SystemSnapshotRepository().get_binding(
            unit_of_work.connection,
            memorial_id=first.memorial_id,
            attempt_id=first.attempt_id,
        )
        unit_of_work.commit()
    assert binding is not None
    assert binding.generation_ids == (_GENERATION_ONE,)
    assert controller.calls == [(first.memorial_id, first.attempt_id, (_GENERATION_ONE,))]
    assert controller.releases == [first.attempt_id]


def test_followup_inherits_parent_generation_while_new_root_takes_active(storage) -> None:
    _seed_memorial(storage, "parent")
    storage.save_edict(Edict(id="edict-child", goal="child", submitter="principal-1"))
    storage.save_memorial(
        Memorial(
            id="child",
            edict_id="edict-child",
            parent_memorial_id="parent",
        )
    )
    _seed_memorial(storage, "new-root")
    controller = _Controller()
    router = _router(storage, controller)
    for memorial_id in ("parent", "child", "new-root"):
        router.assign(memorial_id)
    with router.bind_runtime("parent", attempt_id="parent-attempt"):
        pass

    controller.active_ids = (_GENERATION_TWO,)
    with router.bind_runtime("child", attempt_id="child-attempt"):
        child = current_run_binding()
        assert child is not None
        assert child.generation_ids == (_GENERATION_ONE,)
    with router.bind_runtime("new-root", attempt_id="root-attempt"):
        root = current_run_binding()
        assert root is not None
        assert root.generation_ids == (_GENERATION_TWO,)


def test_followup_skips_unbound_parent_and_inherits_nearest_bound_ancestor(storage) -> None:
    _seed_memorial(storage, "ancestor")
    storage.save_edict(Edict(id="edict-parent", goal="parent", submitter="principal-1"))
    storage.save_memorial(
        Memorial(
            id="unbound-parent",
            edict_id="edict-parent",
            parent_memorial_id="ancestor",
        )
    )
    storage.save_edict(Edict(id="edict-child", goal="child", submitter="principal-1"))
    storage.save_memorial(
        Memorial(
            id="child",
            edict_id="edict-child",
            parent_memorial_id="unbound-parent",
        )
    )
    controller = _Controller()
    router = _router(storage, controller)
    for memorial_id in ("ancestor", "unbound-parent", "child"):
        router.assign(memorial_id)
    with router.bind_runtime("ancestor", attempt_id="ancestor-attempt"):
        pass

    controller.active_ids = (_GENERATION_TWO,)
    with router.bind_runtime("child", attempt_id="child-attempt"):
        child = current_run_binding()
        assert child is not None
        assert child.generation_ids == (_GENERATION_ONE,)

    assert controller.calls[-1] == (
        "child",
        "child-attempt",
        (_GENERATION_ONE,),
    )


async def test_followup_executor_override_drops_parent_pi_pin_with_real_controller(
    storage,
) -> None:
    from tests.executor.test_generation_controller import (
        _FIRST,
        _SECOND,
        _THIRD,
        _controller,
        _Materializer,
        _registry,
        _release,
    )

    edict = Edict(
        id="edict-scope-change",
        goal="scope change",
        submitter="principal-1",
        runtime=EdictRuntime(executor="keqing:pi"),
    )
    storage.save_edict(edict)
    storage.save_memorial(Memorial(id="parent-pi", edict_id=edict.id))
    storage.save_memorial(
        Memorial(
            id="child-native",
            edict_id=edict.id,
            parent_memorial_id="parent-pi",
            runtime_override={"executor": "native"},
        )
    )
    storage.save_memorial(
        Memorial(
            id="grandchild-pi",
            edict_id=edict.id,
            parent_memorial_id="child-native",
        )
    )
    controller = _controller(
        storage,
        _registry("native", "keqing:pi"),
        _Materializer(storage),
        ids=(_FIRST, _SECOND, _THIRD),
        outcomes=[(True, None), (True, None), (True, None)],
    )
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)
    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: _snapshot_resolver(),
        generation_controller=lambda: controller,
    )
    for memorial_id in ("parent-pi", "child-native", "grandchild-pi"):
        router.assign(memorial_id)

    with router.bind_runtime("parent-pi", attempt_id="attempt-parent-pi"):
        parent = current_run_binding()
        assert parent is not None and parent.generation_ids == (_FIRST,)
    controller.release_binding("attempt-parent-pi")

    second = controller.stage(_release(binary_digest="c" * 64))
    await controller.warm(second.generation_id)
    controller.activate(second.generation_id)

    with router.bind_runtime("child-native", attempt_id="attempt-child-native"):
        child = current_run_binding()
        assert child is not None and child.generation_ids == ()
    controller.release_binding("attempt-child-native")

    third = controller.stage(_release(binary_digest="d" * 64))
    await controller.warm(third.generation_id)
    controller.activate(third.generation_id)
    with storage.unit_of_work() as unit_of_work:
        retained = GenerationRepository().retained_generation_ids(unit_of_work.connection)
        unit_of_work.commit()
    assert _FIRST in retained

    with router.bind_runtime("grandchild-pi", attempt_id="attempt-grandchild-pi"):
        grandchild = current_run_binding()
        assert grandchild is not None and grandchild.generation_ids == (_FIRST,)
    controller.release_binding("attempt-grandchild-pi")


async def test_multi_scope_binding_failure_leaves_no_partial_lease_or_snapshot(storage) -> None:
    from tests.executor.test_generation_controller import (
        _ALT_SCOPE,
        _FIRST,
        _PI_SCOPE,
        _SECOND,
        _controller,
        _Materializer,
        _provider,
        _registry,
        _release,
    )

    _seed_memorial(storage)
    registry = _registry("keqing:alt", "keqing:pi")
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_ALT_SCOPE, _PI_SCOPE),
    )
    pi = controller.stage(_release("keqing:pi"))
    await controller.warm(pi.generation_id)
    controller.activate(pi.generation_id)
    alt = controller.stage(_release("keqing:alt"))
    await controller.warm(alt.generation_id)
    controller.activate(alt.generation_id)
    with registry._lock:  # noqa: SLF001 - deterministic partial-material fault injection
        registry._generation_bundles.pop(alt.generation_id)  # noqa: SLF001

    router = ChallengerRouter(
        storage,
        snapshot_resolver=lambda: _snapshot_resolver(),
        generation_controller=lambda: controller,
    )
    router.assign("memorial-1")

    with (
        pytest.raises(GenerationBindingUnavailable, match="generation_binding_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-multi-failed"),
    ):
        pytest.fail("one missing scope must fail the entire binding")

    assert registry.attempt_leases() == {}
    with storage.unit_of_work() as unit_of_work:
        binding = SystemSnapshotRepository().get_binding(
            unit_of_work.connection,
            memorial_id="memorial-1",
            attempt_id="attempt-multi-failed",
        )
        unit_of_work.commit()
    assert binding is None


def test_retired_continuity_and_exact_manifest_drift_fail_closed(storage) -> None:
    _seed_memorial(storage)
    controller = _Controller()
    router = _router(storage, controller)
    router.assign("memorial-1")
    with router.bind_runtime("memorial-1", attempt_id="attempt-1"):
        pass

    controller.fail_pinned = True
    with (
        pytest.raises(GenerationRetired, match="generation_retired"),
        router.bind_runtime("memorial-1", attempt_id="attempt-2"),
    ):
        pytest.fail("retired generation must fail before execution")

    controller.fail_pinned = False
    controller.manifests[_GENERATION_ONE] = {"keqing:pi": _digest("drifted")}
    with (
        pytest.raises(GenerationRetired, match="generation_retired"),
        router.bind_runtime("memorial-1", attempt_id="attempt-1"),
    ):
        pytest.fail("manifest drift must fail before execution")


def test_unrelated_static_executor_manifest_drift_does_not_retire_pinned_pi(storage) -> None:
    _seed_memorial(storage)
    controller = _Controller()
    controller.manifests[_GENERATION_ONE] = {
        "keqing:pi": _digest("pi-one"),
        "native": _digest("native-one"),
    }
    router = _router(storage, controller)
    router.assign("memorial-1")
    with router.bind_runtime("memorial-1", attempt_id="attempt-1"):
        pass

    controller.manifests[_GENERATION_ONE] = {
        "keqing:pi": _digest("pi-one"),
        "native": _digest("native-two"),
        "keqing:codex": _digest("codex-new"),
    }
    with router.bind_runtime("memorial-1", attempt_id="attempt-1"):
        replay = current_run_binding()
        assert replay is not None
        assert replay.generation_ids == (_GENERATION_ONE,)


def test_nonempty_generation_binding_uses_strict_persistence(storage) -> None:
    _seed_memorial(storage)
    controller = _Controller()
    router = _router(storage, controller)
    router.assign("memorial-1")
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            """
            CREATE TRIGGER reject_generation_binding
            BEFORE INSERT ON run_system_bindings BEGIN
                SELECT RAISE(ABORT, 'injected generation binding failure');
            END
            """
        )
        unit_of_work.commit()

    with (
        pytest.raises(GenerationBindingUnavailable, match="generation_binding_unavailable"),
        router.bind_runtime("memorial-1", attempt_id="attempt-failed"),
    ):
        pytest.fail("strict generation binding failure must not execute")

    with storage.unit_of_work() as unit_of_work:
        assert (
            SystemSnapshotRepository().get_binding(
                unit_of_work.connection,
                memorial_id="memorial-1",
                attempt_id="attempt-failed",
            )
            is None
        )
        unit_of_work.commit()
