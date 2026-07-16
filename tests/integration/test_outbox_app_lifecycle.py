"""FastAPI lifecycle wiring for the single durable outbox dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from tianshu.app import create_app, lifespan
from tianshu.config import TianshuSettings
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository


def _settings(tmp_path, **overrides) -> TianshuSettings:
    values = {
        "_env_file": None,
        "startup_profile": "demo",
        "db_path": str(tmp_path / "outbox-lifecycle.db"),
        "runtime_personas_dir": str(tmp_path / "personas"),
        "memory_dir": str(tmp_path / "memory"),
        "workspace_staging_root": str(tmp_path / "workspaces"),
        "log_dir": str(tmp_path / "logs"),
        "outbox_poll_interval_seconds": 0.01,
    }
    values.update(overrides)
    return TianshuSettings(**values)


async def _cleanup_failed_start(app) -> None:  # type: ignore[no-untyped-def]
    if getattr(app.state, "_startup_cleanup_complete", False):
        return
    lifecycle = getattr(app.state, "outbox_lifecycle", None)
    if lifecycle is not None and not app.state.outbox_dispatcher.is_stopped:
        await lifecycle.stop()
    app.state.agent.request_shutdown()
    await app.state.scheduler.stop()
    await app.state.executor.shutdown()
    await app.state.worker_pool.shutdown()
    await app.state.workspace_service.shutdown()
    await app.state.code_sandbox.shutdown()
    await app.state.mcp_manager.shutdown()
    await app.state.bot_manager.stop_all()
    app.state.drawer_store.close()
    app.state.storage.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"outbox_poll_interval_seconds": 0},
        {"outbox_poll_interval_seconds": float("nan")},
        {"outbox_lease_seconds": 0},
        {"outbox_lease_seconds": 1.5},
        {"outbox_lease_seconds": True},
        {"outbox_lease_seconds": 1, "outbox_poll_interval_seconds": 0.5},
        {"durable_retry_base_seconds": 0},
        {"durable_retry_base_seconds": True},
        {"outbox_poll_interval_seconds": True},
        {"durable_retry_max_seconds": 0.5, "durable_retry_base_seconds": 1},
        {"outbox_shutdown_timeout_seconds": 0},
    ],
)
def test_outbox_settings_reject_invalid_values(tmp_path, overrides) -> None:
    with pytest.raises(ValidationError):
        _settings(tmp_path, **overrides)


async def test_lifespan_starts_one_configured_dispatcher_after_consumer_wiring(
    tmp_path, monkeypatch
) -> None:
    from tianshu.application.outbox import OutboxDispatcher, OutboxLifecycleState

    settings = _settings(
        tmp_path,
        outbox_poll_interval_seconds=0.02,
        outbox_lease_seconds=37,
        durable_retry_base_seconds=2,
        durable_retry_max_seconds=19,
        outbox_shutdown_timeout_seconds=3,
    )
    app = create_app(settings)
    original_drain_once = OutboxDispatcher.drain_once

    async def drain_after_scheduler_restore(self, *, limit: int = 50) -> int:
        assert app.state.scheduler.is_ready, (
            "initial outbox delivery must not run before scheduler restoration completes"
        )
        return await original_drain_once(self, limit=limit)

    monkeypatch.setattr(OutboxDispatcher, "drain_once", drain_after_scheduler_restore)

    async with lifespan(app):
        lifecycle = app.state.outbox_lifecycle
        dispatcher = app.state.outbox_dispatcher
        assert lifecycle.state is OutboxLifecycleState.RUNNING
        assert lifecycle.is_ready
        assert lifecycle.task is app.state.outbox_task
        assert app.state.outbox_task.get_name() == "outbox-dispatcher"
        assert dispatcher._poll_interval_seconds == 0.02  # noqa: SLF001
        assert dispatcher._lease_seconds == 37  # noqa: SLF001
        assert dispatcher._base_backoff_seconds == 2  # noqa: SLF001
        assert dispatcher._max_backoff_seconds == 19  # noqa: SLF001
        assert dispatcher._shutdown_timeout_seconds == 3  # noqa: SLF001
        assert app.state.scheduler.is_ready

        first_task = lifecycle.task
        with pytest.raises(RuntimeError, match="already started"):
            await lifecycle.start()
        assert lifecycle.task is first_task

    assert lifecycle.state is OutboxLifecycleState.STOPPED
    assert lifecycle.task is not None and lifecycle.task.done()


@pytest.mark.parametrize("failure_point", ["tracing", "telemetry"])
async def test_fallible_startup_finishes_before_outbox_is_created(
    tmp_path,
    monkeypatch,
    failure_point: str,
) -> None:
    from tianshu import bootstrap, observability, telemetry

    settings = _settings(tmp_path, telemetry="on" if failure_point == "telemetry" else "off")
    app = create_app(settings)
    if hasattr(app.state, "tianshu_mcp_server"):
        del app.state.tianshu_mcp_server
    monkeypatch.setattr(bootstrap, "wire_skills_watcher", lambda _app, _settings: None)
    outbox_created = False
    original_wire_outbox = bootstrap.wire_outbox
    stop_order: list[str] = []
    from tianshu.application.run_reconciler import RunReconciler
    from tianshu.scheduler.scheduler import Scheduler

    original_scheduler_stop = Scheduler.stop
    original_reconciler_stop = RunReconciler.stop

    async def scheduler_stop(self) -> None:  # type: ignore[no-untyped-def]
        stop_order.append("scheduler")
        await original_scheduler_stop(self)

    async def reconciler_stop(self) -> None:  # type: ignore[no-untyped-def]
        stop_order.append("reconciler")
        await original_reconciler_stop(self)

    monkeypatch.setattr(Scheduler, "stop", scheduler_stop)
    monkeypatch.setattr(RunReconciler, "stop", reconciler_stop)

    async def record_outbox_creation(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        nonlocal outbox_created
        outbox_created = True
        await original_wire_outbox(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "wire_outbox", record_outbox_creation)
    if failure_point == "tracing":

        def fail_tracing(_settings) -> bool:  # type: ignore[no-untyped-def]
            raise RuntimeError("injected tracing startup failure")

        monkeypatch.setattr(observability, "init_tracing", fail_tracing)
    else:

        async def fail_telemetry(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("injected telemetry startup failure")

        monkeypatch.setattr(telemetry, "emit_startup", fail_telemetry)

    context = lifespan(app)
    try:
        with pytest.raises(RuntimeError, match=f"injected {failure_point}"):
            await context.__aenter__()
        assert not outbox_created
        assert not hasattr(app.state, "outbox_lifecycle")
        assert stop_order == ["scheduler", "reconciler"]
        assert not app.state.scheduler.is_ready
        assert not app.state.run_reconciler.is_ready
    finally:
        await _cleanup_failed_start(app)


async def test_failure_after_outbox_start_confirms_worker_stopped_before_propagating(
    tmp_path,
    monkeypatch,
) -> None:
    from tianshu import app as app_module
    from tianshu import bootstrap

    app = create_app(_settings(tmp_path))
    if hasattr(app.state, "tianshu_mcp_server"):
        del app.state.tianshu_mcp_server
    monkeypatch.setattr(bootstrap, "wire_skills_watcher", lambda _app, _settings: None)
    original_info = app_module.logger.info

    def fail_after_outbox(message, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if message == "Tianshu started on %s:%s":
            raise RuntimeError("injected post-outbox startup failure")
        original_info(message, *args, **kwargs)

    monkeypatch.setattr(app_module.logger, "info", fail_after_outbox)
    context = lifespan(app)
    try:
        with pytest.raises(RuntimeError, match="post-outbox startup failure"):
            await context.__aenter__()
        assert app.state.outbox_dispatcher.is_stopped
        assert app.state.outbox_task.done()
        assert not app.state.outbox_lifecycle.is_ready
    finally:
        await _cleanup_failed_start(app)


async def test_failure_after_bot_and_watcher_start_cleans_reverse_then_closes_storage(
    tmp_path,
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from tianshu import bootstrap

    app = create_app(_settings(tmp_path))
    order: list[str] = []

    async def start_bots(started_app, _settings) -> None:  # type: ignore[no-untyped-def]
        async def stop_bots() -> None:
            order.append("bots")

        started_app.state.bot_manager = SimpleNamespace(stop_all=stop_bots)

    class Watcher:
        def stop(self) -> None:
            order.append("watcher")

    def fail_after_watcher(_app, _settings) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("injected post-watcher startup failure")

    monkeypatch.setattr(bootstrap, "wire_channel_bots", start_bots)
    monkeypatch.setattr(bootstrap, "wire_skills_watcher", lambda *_args: Watcher())
    monkeypatch.setattr(bootstrap, "wire_universe", fail_after_watcher)

    context = lifespan(app)
    with pytest.raises(RuntimeError, match="post-watcher startup failure"):
        await context.__aenter__()
    assert order == ["watcher", "bots"]
    assert app.state.storage._conn is None  # noqa: SLF001


async def test_tracing_shutdown_is_registered_before_later_startup_failure(
    tmp_path,
    monkeypatch,
) -> None:
    from tianshu import bootstrap, observability, telemetry

    app = create_app(_settings(tmp_path, telemetry="on"))
    if hasattr(app.state, "tianshu_mcp_server"):
        del app.state.tianshu_mcp_server
    monkeypatch.setattr(bootstrap, "wire_skills_watcher", lambda *_args: None)
    order: list[str] = []

    def init_tracing(_settings):  # type: ignore[no-untyped-def]
        def shutdown() -> None:
            order.append("tracing")

        return shutdown

    async def fail_telemetry(*_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("injected telemetry startup failure")

    monkeypatch.setattr(observability, "init_tracing", init_tracing)
    monkeypatch.setattr(telemetry, "emit_startup", fail_telemetry)

    context = lifespan(app)
    with pytest.raises(RuntimeError, match="telemetry startup failure"):
        await context.__aenter__()
    assert order == ["tracing"]


async def test_outbox_stops_before_scheduler_executor_channels_and_storage(
    tmp_path, monkeypatch
) -> None:
    app = create_app(_settings(tmp_path))
    context = lifespan(app)
    await context.__aenter__()
    order: list[str] = []

    original_outbox_stop = app.state.outbox_lifecycle.stop
    original_scheduler_stop = app.state.scheduler.stop
    original_executor_shutdown = app.state.executor.shutdown
    original_bots_stop = app.state.bot_manager.stop_all
    original_storage_close = app.state.storage.close

    async def outbox_stop() -> None:
        order.append("outbox")
        await original_outbox_stop()

    async def scheduler_stop() -> None:
        order.append("scheduler")
        await original_scheduler_stop()

    async def executor_shutdown() -> None:
        order.append("executor")
        await original_executor_shutdown()

    async def bots_stop() -> None:
        order.append("channels")
        await original_bots_stop()

    def storage_close() -> None:
        order.append("storage")
        original_storage_close()

    monkeypatch.setattr(app.state.outbox_lifecycle, "stop", outbox_stop)
    monkeypatch.setattr(app.state.scheduler, "stop", scheduler_stop)
    monkeypatch.setattr(app.state.executor, "shutdown", executor_shutdown)
    monkeypatch.setattr(app.state.bot_manager, "stop_all", bots_stop)
    monkeypatch.setattr(app.state.storage, "close", storage_close)

    await context.__aexit__(None, None, None)

    assert order.count("outbox") == 1
    for later in ("scheduler", "executor", "channels", "storage"):
        assert order.index("outbox") < order.index(later)


async def test_outbox_shutdown_timeout_aborts_shared_component_teardown(
    tmp_path, monkeypatch
) -> None:
    from tianshu import bootstrap
    from tianshu.application.outbox import OutboxShutdownTimeout

    app = create_app(_settings(tmp_path))
    if hasattr(app.state, "tianshu_mcp_server"):
        del app.state.tianshu_mcp_server
    monkeypatch.setattr(bootstrap, "wire_skills_watcher", lambda _app, _settings: None)
    context = lifespan(app)
    await context.__aenter__()
    calls: list[str] = []

    original_outbox_stop = app.state.outbox_lifecycle.stop
    original_scheduler_stop = app.state.scheduler.stop
    original_executor_shutdown = app.state.executor.shutdown
    original_bots_stop = app.state.bot_manager.stop_all
    original_storage_close = app.state.storage.close

    async def outbox_timeout() -> None:
        calls.append("outbox")
        raise OutboxShutdownTimeout("bounded drain still live")

    async def downstream_async(name: str) -> None:
        calls.append(name)

    def storage_close() -> None:
        calls.append("storage")

    monkeypatch.setattr(app.state.outbox_lifecycle, "stop", outbox_timeout)
    monkeypatch.setattr(app.state.scheduler, "stop", lambda: downstream_async("scheduler"))
    monkeypatch.setattr(app.state.executor, "shutdown", lambda: downstream_async("executor"))
    monkeypatch.setattr(app.state.bot_manager, "stop_all", lambda: downstream_async("channels"))
    monkeypatch.setattr(app.state.storage, "close", storage_close)

    try:
        with pytest.raises(OutboxShutdownTimeout, match="bounded drain still live"):
            await context.__aexit__(None, None, None)
        assert calls == ["outbox"]
        with app.state.storage._lock:  # noqa: SLF001 - fail-closed teardown proof
            assert app.state.storage._conn.execute("SELECT 1").fetchone()[0] == 1  # noqa: SLF001
        assert app.state.scheduler.is_ready
        assert app.state.outbox_lifecycle.is_ready
    finally:
        await original_outbox_stop()
        app.state.agent.request_shutdown()
        await original_scheduler_stop()
        await original_executor_shutdown()
        await app.state.worker_pool.shutdown()
        await app.state.workspace_service.shutdown()
        await app.state.code_sandbox.shutdown()
        await app.state.mcp_manager.shutdown()
        await original_bots_stop()
        app.state.drawer_store.close()
        original_storage_close()


async def test_file_database_expired_claim_is_recovered_on_app_restart(tmp_path) -> None:
    settings = _settings(tmp_path, outbox_lease_seconds=1)
    storage = Storage(settings.db_path)
    storage.init_db()
    repository = OutboxRepository(storage.unit_of_work)
    event = EventEnvelope(
        event_id="restart-recovery-event",
        event_type="test.lifecycle.recovery",
        timestamp=datetime(1999, 1, 1, tzinfo=UTC),
        producer="tests",
        payload={"proof": "file-db"},
    )
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(unit_of_work.connection, event)
        unit_of_work.commit()
    claimed = repository.claim_batch(
        owner_id="dead-process",
        now=datetime(2000, 1, 1, tzinfo=UTC),
        limit=1,
        lease_seconds=1,
    )
    assert len(claimed) == 1
    storage.close()

    first_app = create_app(settings)
    async with lifespan(first_app):
        first = first_app.state.outbox_repository.get(
            first_app.state.storage._conn,  # noqa: SLF001
            event.event_id,
        )
        assert first is not None
        assert (first.status, first.attempt_count) == ("published", 2)

    second_app = create_app(settings)
    async with lifespan(second_app):
        second = second_app.state.outbox_repository.get(
            second_app.state.storage._conn,  # noqa: SLF001
            event.event_id,
        )
        assert second is not None
        assert (second.status, second.attempt_count) == ("published", 2)
        assert second_app.state.outbox_lifecycle.is_ready
