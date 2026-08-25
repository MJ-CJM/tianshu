"""Process-local dispatch backed by the durable attempt lease authority."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.application.run_dispatcher import (
    AttemptAuthority,
    AttemptRunResult,
    RunDispatcher,
)
from tianshu.application.run_reconciler import RunReconciler
from tianshu.models import Edict, Memorial
from tianshu.models.attempt import AttemptDisposition
from tianshu.models.canonical import RedactedError
from tianshu.storage import Storage
from tianshu.storage.attempt_ledger import AttemptConflict

_NOW = datetime(2026, 7, 15, 8, tzinfo=UTC)


class _PassthroughRouter:
    @contextmanager
    def bind_runtime(self, memorial_id: str, *, attempt_id: str | None = None):
        del memorial_id, attempt_id
        yield None


_ROUTER = _PassthroughRouter()


class _Clock:
    def __init__(self, now: datetime = _NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _seed(storage: Storage, *, max_attempts: int = 3) -> None:
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id="memorial-1", edict_id="edict-1", attempt=1))
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
            max_attempts=max_attempts,
        )
        uow.commit()


def _insert_child_attempt(storage: Storage) -> None:
    storage.save_edict(Edict(id="edict-child", goal="test child"))
    storage.save_memorial(
        Memorial(
            id="dag-child",
            edict_id="edict-child",
            parent_memorial_id="memorial-1",
            dag_node_id="node-1",
        )
    )
    with storage.unit_of_work() as uow:
        uow.connection.execute(
            """
            INSERT INTO execution_attempts (
                attempt_id, schema_version, memorial_id, attempt_no, status,
                owner_id, fencing_token, lease_expires_at, heartbeat_at,
                available_at, max_attempts, failure_json, version, created_at, updated_at
            ) VALUES (
                'child-attempt', 1, 'dag-child', 1, 'claimable',
                NULL, 0, NULL, NULL, ?, 3, NULL, 1, ?, ?
            )
            """,
            (_NOW.isoformat(), _NOW.isoformat(), _NOW.isoformat()),
        )
        uow.commit()


@pytest.mark.asyncio
async def test_direct_child_claim_fails_before_runner_even_if_attempt_was_injected(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "child-claim.db")
    _insert_child_attempt(storage)
    runs = 0

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        nonlocal runs
        del authority
        runs += 1
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        runner,
        owner_id="worker",
        challenger_router=_ROUTER,
        clock=_Clock(),
    )
    try:
        with pytest.raises(AttemptConflict, match="not a root") as error:
            await dispatcher.dispatch("dag-child")
        assert "dag-child" not in str(error.value)
        assert runs == 0
        assert dispatcher.active_count == 0
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, fencing_token FROM execution_attempts"
        ).fetchone()
        assert tuple(row) == ("claimable", None, 0)
    finally:
        await dispatcher.stop()
        storage.close()


@pytest.mark.asyncio
async def test_two_dispatchers_execute_one_due_attempt_once(tmp_path: Path) -> None:
    database = tmp_path / "race.db"
    first = _open(database)
    _seed(first)
    second = _open(database)
    runners: list[str] = []

    def runner(owner_id: str):
        async def run(authority: AttemptAuthority) -> AttemptRunResult:
            assert authority.owner_id == owner_id
            runners.append(owner_id)
            return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

        return run

    dispatcher_a = RunDispatcher(
        first.attempt_repo,
        runner("worker-a"),
        owner_id="worker-a",
        challenger_router=_ROUTER,
        clock=_Clock(),
    )
    dispatcher_b = RunDispatcher(
        second.attempt_repo,
        runner("worker-b"),
        owner_id="worker-b",
        challenger_router=_ROUTER,
        clock=_Clock(),
    )
    try:
        claimed = await asyncio.gather(
            dispatcher_a.dispatch("memorial-1"),
            dispatcher_b.dispatch("memorial-1"),
        )
        async with asyncio.timeout(1):
            await asyncio.gather(
                dispatcher_a.wait_until_idle(),
                dispatcher_b.wait_until_idle(),
            )
        assert sorted(claimed) == [False, True]
        assert len(runners) == 1
        assert (
            first._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts"
            ).fetchone()[0]
            == "succeeded"
        )
    finally:
        await dispatcher_a.stop()
        await dispatcher_b.stop()
        second.close()
        first.close()


@pytest.mark.asyncio
async def test_restart_waits_for_expiry_then_dispatches_attempt_two(tmp_path: Path) -> None:
    database = tmp_path / "restart.db"
    first = _open(database)
    _seed(first)
    first_clock = _Clock()
    runner_cancelled = asyncio.Event()

    async def abandoned_runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        try:
            await asyncio.Future()
        finally:
            runner_cancelled.set()

    abandoned = RunDispatcher(
        first.attempt_repo,
        abandoned_runner,
        owner_id="worker-old",
        challenger_router=_ROUTER,
        clock=first_clock,
        lease_seconds=30,
        heartbeat_interval_seconds=29,
        shutdown_timeout_seconds=0.02,
    )
    assert await abandoned.dispatch("memorial-1")
    await asyncio.sleep(0)
    assert abandoned.active_count == 1
    await abandoned.stop()
    await runner_cancelled.wait()
    first.close()

    restarted = _open(database)
    restart_clock = _Clock(_NOW + timedelta(seconds=29))
    runs: list[AttemptAuthority] = []

    async def recovered_runner(authority: AttemptAuthority) -> AttemptRunResult:
        runs.append(authority)
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    dispatcher = RunDispatcher(
        restarted.attempt_repo,
        recovered_runner,
        owner_id="worker-new",
        challenger_router=_ROUTER,
        clock=restart_clock,
    )
    reconciler = RunReconciler(
        restarted.attempt_repo,
        dispatcher,
        clock=restart_clock,
    )
    try:
        assert await reconciler.reconcile_once() == 0
        restart_clock.now = _NOW + timedelta(seconds=30)
        assert await reconciler.reconcile_once() == 1
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()
        assert [(run.owner_id, run.fencing_token) for run in runs] == [("worker-new", 2)]
        rows = restarted._conn.execute(  # noqa: SLF001
            "SELECT attempt_no, status FROM execution_attempts ORDER BY attempt_no"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(1, "failed"), (2, "succeeded")]
    finally:
        await reconciler.stop()
        restarted.close()


@pytest.mark.asyncio
async def test_local_runner_exits_after_exact_expiry_before_attempt_two_starts(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "local-overlap.db")
    _seed(storage)
    clock = _Clock()
    first_started = asyncio.Event()
    first_cancelled = asyncio.Event()
    active_runners = 0
    max_active_runners = 0
    runs: list[int] = []

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        nonlocal active_runners, max_active_runners
        active_runners += 1
        max_active_runners = max(max_active_runners, active_runners)
        runs.append(authority.fencing_token)
        try:
            if authority.fencing_token == 1:
                first_started.set()
                await asyncio.Future()
            return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)
        finally:
            active_runners -= 1
            if authority.fencing_token == 1:
                first_cancelled.set()

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        runner,
        owner_id="worker",
        challenger_router=_ROUTER,
        clock=clock,
        lease_seconds=1,
        heartbeat_interval_seconds=0.01,
    )
    try:
        assert await dispatcher.dispatch("memorial-1")
        await first_started.wait()
        clock.now = _NOW + timedelta(seconds=1)

        assert not await dispatcher.dispatch("memorial-1")
        assert dispatcher.active_count == 1
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 1
        )

        await first_cancelled.wait()
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()
        assert dispatcher.active_count == 0

        assert await dispatcher.dispatch("memorial-1")
        assert dispatcher.active_count == 1
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()

        assert runs == [1, 2]
        assert max_active_runners == 1
        rows = storage._conn.execute(  # noqa: SLF001
            "SELECT attempt_no, status, fencing_token FROM execution_attempts ORDER BY attempt_no"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (1, "failed", 1),
            (2, "succeeded", 2),
        ]
    finally:
        await dispatcher.stop()
        storage.close()


@pytest.mark.asyncio
async def test_expired_max_attempt_is_dead_lettered_once_and_never_run(tmp_path: Path) -> None:
    storage = _open(tmp_path / "dlq.db")
    _seed(storage, max_attempts=1)
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-1",
        owner_id="old-worker",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    clock = _Clock(_NOW + timedelta(seconds=30))
    runs = 0

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        nonlocal runs
        del authority
        runs += 1
        return AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED)

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        runner,
        owner_id="new-worker",
        challenger_router=_ROUTER,
        clock=clock,
    )
    reconciler = RunReconciler(storage.attempt_repo, dispatcher, clock=clock)
    try:
        assert await reconciler.reconcile_once() == 0
        assert await reconciler.reconcile_once() == 0
        assert runs == 0
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts"
            ).fetchone()[0]
            == "dead_letter"
        )
    finally:
        await reconciler.stop()
        storage.close()


class _LoseHeartbeatRepository:
    def __init__(self, storage: Storage) -> None:
        self._delegate = storage.attempt_repo
        self.heartbeat_calls = 0

    def claim(self, **kwargs: object):
        return self._delegate.claim(**kwargs)  # type: ignore[arg-type]

    def heartbeat(self, **kwargs: object) -> bool:
        del kwargs
        self.heartbeat_calls += 1
        return False

    def complete(self, **kwargs: object) -> bool:
        return self._delegate.complete(**kwargs)  # type: ignore[arg-type]

    def list_dispatchable_memorial_ids(self, **kwargs: object) -> tuple[str, ...]:
        return self._delegate.list_dispatchable_memorial_ids(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_heartbeat_loss_cancels_runner_without_false_success(tmp_path: Path) -> None:
    storage = _open(tmp_path / "heartbeat-loss.db")
    _seed(storage)
    repository = _LoseHeartbeatRepository(storage)
    cancelled = asyncio.Event()

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        del authority
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    dispatcher = RunDispatcher(
        repository,
        runner,
        owner_id="worker",
        challenger_router=_ROUTER,
        clock=_Clock(),
        heartbeat_interval_seconds=0.01,
    )
    try:
        assert await dispatcher.dispatch("memorial-1")
        await cancelled.wait()
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()
        assert repository.heartbeat_calls == 1
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts"
            ).fetchone()[0]
            == "claimed"
        )
    finally:
        await dispatcher.stop()
        storage.close()


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (AttemptRunResult(disposition=AttemptDisposition.SUCCEEDED), ("succeeded",)),
        (
            AttemptRunResult(
                disposition=AttemptDisposition.FAILED,
                failure=RedactedError(
                    code="run_failed",
                    message="run failed",
                    retryable=False,
                    details_hash=None,
                ),
            ),
            ("failed",),
        ),
        (AttemptRunResult(disposition=AttemptDisposition.SUSPENDED), ("suspended",)),
        (
            AttemptRunResult(
                disposition=AttemptDisposition.RETRY,
                failure=RedactedError(
                    code="retry",
                    message="retry later",
                    retryable=True,
                    details_hash=None,
                ),
                retry_at=_NOW + timedelta(seconds=5),
            ),
            ("failed", "claimable"),
        ),
    ],
)
@pytest.mark.asyncio
async def test_runner_result_maps_to_exact_durable_outcome(
    tmp_path: Path,
    result: AttemptRunResult,
    expected: tuple[str, ...],
) -> None:
    storage = _open(tmp_path / f"{result.disposition.value}.db")
    _seed(storage)

    async def runner(authority: AttemptAuthority) -> AttemptRunResult:
        assert authority.memorial_id == "memorial-1"
        return result

    dispatcher = RunDispatcher(
        storage.attempt_repo,
        runner,
        owner_id="worker",
        challenger_router=_ROUTER,
        clock=_Clock(),
    )
    try:
        assert await dispatcher.dispatch("memorial-1")
        async with asyncio.timeout(1):
            await dispatcher.wait_until_idle()
        statuses = storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts ORDER BY attempt_no"
        ).fetchall()
        assert tuple(row[0] for row in statuses) == expected
    finally:
        await dispatcher.stop()
        storage.close()
