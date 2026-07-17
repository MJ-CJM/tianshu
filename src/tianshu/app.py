"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from tianshu import bootstrap
from tianshu.application.control_center import ControlCenterQueryService
from tianshu.application.edict_detail import EdictDetailQueryService
from tianshu.application.evolution_view import EvolutionCenterQueryService
from tianshu.config import TianshuSettings
from tianshu.gateway import gateway_router
from tianshu.gateway.audit_api import audit_router
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.auth_api import auth_router
from tianshu.gateway.config_api import config_router
from tianshu.gateway.control_center_api import control_center_router
from tianshu.gateway.cost_api import cost_router
from tianshu.gateway.credentials_api import credentials_router
from tianshu.gateway.decisions_api import decisions_router
from tianshu.gateway.edicts_api import edicts_router
from tianshu.gateway.estop_api import estop_router
from tianshu.gateway.evals_api import evals_router
from tianshu.gateway.evidence_api import evidence_router
from tianshu.gateway.evolution_api import evolution_router
from tianshu.gateway.execution_api import execution_router
from tianshu.gateway.hongluisi_api import hongluisi_router
from tianshu.gateway.keqing_api import keqing_router
from tianshu.gateway.mcp_api import mcp_router
from tianshu.gateway.memory_api import memory_router
from tianshu.gateway.personas_api import personas_router
from tianshu.gateway.providers_api import providers_router
from tianshu.gateway.skills_api import skills_router
from tianshu.gateway.system_api import system_router
from tianshu.gateway.system_audit_api import system_audit_router
from tianshu.gateway.universes_api import universes_router
from tianshu.gateway.workspace_api import workspace_router
from tianshu.logging_config import setup_logging
from tianshu.resources.overlay import packaged_defaults
from tianshu.web import mount_web

logger = logging.getLogger(__name__)


def _assess_app_readiness(state):
    """Build the same authoritative readiness report for health and Control Center."""
    from tianshu.diagnostics import ReadinessInputs, assess_readiness, provider_config_check
    from tianshu.storage.migration_ledger import pending_migrations
    from tianshu.storage.migrations import MIGRATIONS

    storage = state.storage
    try:
        with storage._lock:
            storage._conn.execute("SELECT 1").fetchone()
        database_ok = True
    except Exception:  # noqa: BLE001 - fail-closed readiness probe
        database_ok = False
    if database_ok:
        try:
            with storage._lock:
                migrations_ok = not pending_migrations(storage._conn, MIGRATIONS)
        except Exception:  # noqa: BLE001 - fail-closed readiness probe
            migrations_ok = False
    else:
        migrations_ok = False

    tables = {
        "outbox": "outbox_events",
        "decision": "decision_requests",
        "attempt": "execution_attempts",
        "artifact": "artifact_records",
    }
    durable_tables: dict[str, bool] = {}
    for name, table in tables.items():
        try:
            with storage._lock:
                storage._conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            durable_tables[name] = True
        except Exception:  # noqa: BLE001 - fail-closed readiness probe
            durable_tables[name] = False

    def resources_ok() -> bool:
        from tianshu.resources import catalog

        return (catalog.persona_defaults() / "court" / "COURT.md").is_file()

    def optional_integrations() -> dict[str, bool | None]:
        manager = getattr(state, "mcp_manager", None)
        if manager is None:
            return {"mcp": None}
        expected = set(manager.admitted_enabled_names)
        if not expected:
            return {"mcp": None}
        connected = {
            name for name, session in manager.sessions.items() if session.status == "connected"
        }
        failed = expected - connected - set(manager.starting_names)
        return {"mcp": not failed}

    def provider_ready() -> bool:
        return (
            provider_config_check(
                profile=state.settings.startup_profile,
                effective=state.config_manager.state,
                config_source="runtime",
            ).status
            == "pass"
        )

    return assess_readiness(
        ReadinessInputs(
            database_ok=lambda: database_ok,
            migrations_current=lambda: migrations_ok,
            scheduler_ready=lambda: state.scheduler.is_ready,
            worker_ready=lambda: state.worker_pool.is_ready,
            outbox_ready=lambda: durable_tables["outbox"] and state.outbox_lifecycle.is_ready,
            dispatcher_ready=lambda: state.outbox_lifecycle.is_ready,
            decision_ready=lambda: durable_tables["decision"],
            attempt_ready=lambda: durable_tables["attempt"],
            artifact_ready=lambda: durable_tables["artifact"] and state.artifact_store.is_ready,
            delivery_ready=lambda: (
                state.internal_delivery_outbox.probe() and state.internal_delivery_worker.is_ready
            ),
            resources_ok=resources_ok,
            provider_ready=provider_ready,
            provider_profile=lambda: state.settings.startup_profile,
            workspace_ready=lambda: state.workspace_service.is_ready,
            evolution_rollback_ready=state.evolution_reconciler.readiness_probe,
            optional_integrations=optional_integrations,
        )
    )


def _async_stop(callback: Callable[[], None]) -> Callable[[], Awaitable[None]]:
    async def stop() -> None:
        callback()

    return stop


def _task_stop(task: asyncio.Task) -> Callable[[], Awaitable[None]]:
    async def stop() -> None:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    return stop


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: TianshuSettings = app.state.settings
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    _mcp_stop: asyncio.Event | None = None
    _mcp_task: asyncio.Task | None = None
    startup_stops: list[Callable[[], Awaitable[None]]] = []
    skills_watcher = None
    tracing_shutdown: Callable[[], None] | None = None

    async def _stop_mcp_server() -> None:
        if _mcp_stop is None or _mcp_task is None:
            return
        _mcp_stop.set()
        try:
            await _mcp_task
        except Exception:
            logger.exception("[mcp-server] session manager shutdown error")

    async def _stop_outbox_if_created() -> None:
        lifecycle = getattr(app.state, "outbox_lifecycle", None)
        if lifecycle is not None:
            await lifecycle.stop()

    async def _stop_internal_delivery_if_created() -> None:
        worker = getattr(app.state, "internal_delivery_worker", None)
        if worker is not None:
            await worker.stop()

    async def _cleanup_started() -> None:
        for stop in reversed(startup_stops):
            try:
                await stop()
            except BaseException:
                logger.exception("background component startup cleanup failed")

    try:
        bootstrap.wire_storage(app, settings)
        startup_stops.append(_async_stop(app.state.storage.close))
        startup_stops.append(app.state.workspace_service.shutdown)
        app.state.auth_service = AuthService(app.state.storage, settings)

        bootstrap.wire_evolution_services(
            app,
            settings,
            skill_target=bootstrap.runtime_skills_target(),
        )

        tools = await bootstrap.wire_tools(app, settings)
        startup_stops.append(app.state.mcp_manager.shutdown)
        startup_stops.append(_task_stop(app.state._mcp_start_task))
        skills, metrics_store = bootstrap.wire_skills(app, settings)
        bootstrap.wire_persona(app, settings, tools)
        prompt_builder = bootstrap.wire_memory_palace(app, settings, skills, metrics_store)
        startup_stops.append(_async_stop(app.state.drawer_store.close))
        bootstrap.wire_llm_config(app, settings)
        bootstrap.wire_skill_tools(app, settings, tools, skills, metrics_store)
        bootstrap.wire_provider_and_agent(
            app,
            settings,
            tools,
            skills,
            metrics_store,
            prompt_builder,
        )
        startup_stops.append(_async_stop(app.state.agent.request_shutdown))
        bootstrap.wire_worker_lane(app, settings)
        startup_stops.append(app.state.worker_pool.shutdown)
        bootstrap.wire_auditor(app, settings)
        bootstrap.wire_channels(app, settings)
        bootstrap.wire_executor(app, settings)
        startup_stops.append(app.state.executor.shutdown)
        bootstrap.wire_cost_manager(app, settings)
        await bootstrap.wire_channel_bots(app, settings)
        startup_stops.append(app.state.bot_manager.stop_all)
        bootstrap.wire_policy(app, settings)
        bootstrap.wire_memory_manager(app, settings)
        bootstrap.wire_consultation(app, settings)
        bootstrap.wire_persona_quality(app, settings)
        bootstrap.wire_scheduling(app, settings)
        bootstrap.wire_plugins(app, settings)
        bootstrap.wire_hook_registrations(app, settings)
        bootstrap.wire_profile(app, settings)
        bootstrap.wire_skill_curator(app, settings)
        skills_watcher = bootstrap.wire_skills_watcher(app, settings)
        if skills_watcher is not None:
            startup_stops.append(_async_stop(skills_watcher.stop))
        bootstrap.wire_universe(app, settings)
        startup_stops.append(app.state.code_sandbox.shutdown)
        bootstrap.wire_digest(app, settings)
        startup_stops.append(_task_stop(app.state._digest_task))

        # --- Backward compat ---
        app.state.running_tasks = app.state.executor.running_tasks

        # Register stop before start so partial startup is also cleaned up.
        startup_stops.append(app.state.run_reconciler.stop)
        await app.state.run_reconciler.start()
        startup_stops.append(app.state.scheduler.stop)
        await app.state.scheduler.start()

        # --- MCP server session manager(stateless 模式仍需运行;挂载见 create_app)---
        mcp_server = getattr(app.state, "tianshu_mcp_server", None)
        if mcp_server is not None:
            _mcp_stop = asyncio.Event()
            _mcp_ready: asyncio.Event = asyncio.Event()

            async def _run_mcp_session_manager() -> None:
                async with mcp_server.session_manager.run():
                    _mcp_ready.set()
                    assert _mcp_stop is not None
                    await _mcp_stop.wait()

            startup_stops.append(_stop_mcp_server)
            _mcp_task = asyncio.create_task(_run_mcp_session_manager())
            ready_waiter = asyncio.create_task(_mcp_ready.wait())
            try:
                done, _ = await asyncio.wait(
                    {_mcp_task, ready_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if _mcp_task in done:
                    await _mcp_task
                    raise RuntimeError("MCP session manager exited during startup")
                await ready_waiter
            finally:
                if not ready_waiter.done():
                    ready_waiter.cancel()

        # --- OTel GenAI 埋点(迭代 3):默认关;设 TIANSHU_OTEL_ENDPOINT 才导出 ---
        from tianshu import observability

        tracing_shutdown = observability.init_tracing(settings)
        if tracing_shutdown is not None:
            startup_stops.append(_async_stop(tracing_shutdown))

        # --- opt-in 遥测(迭代 3,ADR-0003):默认关;首启明示,一行 env 永久关 ---
        from tianshu import telemetry

        if telemetry.is_enabled(settings.telemetry):
            logger.info(
                "[telemetry] 已启用(opt-in):仅上报版本+启动事件,不含任务内容。"
                "设 TIANSHU_TELEMETRY=off 永久关闭。"
            )
            await telemetry.emit_startup(
                settings,
                instance_id=f"{settings.host}:{settings.port}",
            )

        # Durable only up to the internal notification handler. Provider/channel
        # acceptance remains best-effort and is intentionally outside this Gate.
        from tianshu.notifier.delivery_outbox import (
            InternalDeliveryOutbox,
            InternalDeliveryWorker,
        )

        internal_delivery_outbox = InternalDeliveryOutbox(app.state.storage.unit_of_work)
        app.state.notifier.set_delivery_outbox(internal_delivery_outbox)
        await app.state.notifier.drain_legacy_pending()
        internal_delivery_worker = InternalDeliveryWorker(
            internal_delivery_outbox,
            app.state.notifier.deliver_internal,
            owner_id=f"internal-delivery-{uuid4().hex}",
            lease_seconds=settings.outbox_lease_seconds,
            base_backoff_seconds=settings.durable_retry_base_seconds,
            max_backoff_seconds=settings.durable_retry_max_seconds,
            poll_interval_seconds=settings.outbox_poll_interval_seconds,
        )
        app.state.internal_delivery_outbox = internal_delivery_outbox
        app.state.internal_delivery_worker = internal_delivery_worker
        startup_stops.append(_stop_internal_delivery_if_created)
        await internal_delivery_worker.start()
        app.state.internal_delivery_task = internal_delivery_worker.task

        # Outbox starts last after every consumer is registered.
        startup_stops.append(_stop_outbox_if_created)
        await bootstrap.wire_outbox(app, settings)
        app.state.control_center_service = ControlCenterQueryService(
            unit_of_work=app.state.storage.unit_of_work,
            decision_repository=app.state.storage.decision_repo,
            run_state_repository=app.state.storage.run_state_repo,
            evidence_repository=app.state.storage.evidence_repo,
            readiness_status=lambda: _assess_app_readiness(app.state).status,
        )
        app.state.edict_detail_service = EdictDetailQueryService(app.state.storage)
        app.state.evolution_center_service = EvolutionCenterQueryService()
        logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    except BaseException:
        await _cleanup_started()
        app.state._startup_cleanup_complete = True
        raise
    yield

    # Stop durable delivery and timer production before draining claimed work.
    await app.state.outbox_lifecycle.stop()
    await app.state.internal_delivery_worker.stop()
    await app.state.scheduler.stop()
    await app.state.run_reconciler.stop()

    # --- MCP server session manager 停止 ---
    if _mcp_stop is not None and _mcp_task is not None:
        _mcp_stop.set()
        try:
            await _mcp_task
        except Exception:
            logger.exception("[mcp-server] session manager shutdown error")
    if tracing_shutdown is not None:
        tracing_shutdown()

    # --- Graceful shutdown ---
    app.state.agent.request_shutdown()
    if skills_watcher:
        skills_watcher.stop()
    if hasattr(app.state, "_digest_task") and not app.state._digest_task.done():
        app.state._digest_task.cancel()
        await asyncio.gather(app.state._digest_task, return_exceptions=True)
    await app.state.executor.shutdown()
    await app.state.worker_pool.shutdown()
    try:
        await app.state.workspace_service.shutdown()
    except Exception:
        logger.exception("[workspace] service shutdown error")
    try:
        await app.state.code_sandbox.shutdown()
    except Exception:
        logger.exception("[universe] sandbox shutdown error")
    try:
        await app.state.mcp_manager.shutdown()
    except Exception:
        logger.exception("[mcp] manager shutdown error")
    await app.state.bot_manager.stop_all()
    app.state.drawer_store.close()
    app.state.storage.close()
    logger.info("Tianshu shutdown complete")


def create_app(settings: TianshuSettings | None = None) -> FastAPI:
    settings = settings or TianshuSettings()
    app = FastAPI(title="Tianshu", version="0.4.2", lifespan=lifespan)
    app.state.settings = settings
    app.state.public_webhook_paths = set()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            list(settings.allowed_origins_list) if settings.security_mode == "secure-remote" else []
        ),
        allow_origin_regex=(
            None
            if settings.security_mode == "secure-remote"
            else r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(gateway_router, prefix="/api")
    app.include_router(auth_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(config_router, prefix="/api")
    app.include_router(control_center_router, prefix="/api")
    app.include_router(cost_router, prefix="/api")
    app.include_router(credentials_router, prefix="/api")
    app.include_router(decisions_router, prefix="/api")
    app.include_router(edicts_router, prefix="/api")
    app.include_router(evidence_router, prefix="/api")
    app.include_router(evolution_router, prefix="/api")
    app.include_router(estop_router, prefix="/api")
    app.include_router(evals_router, prefix="/api")
    app.include_router(execution_router, prefix="/api")
    app.include_router(hongluisi_router, prefix="/api")
    app.include_router(keqing_router, prefix="/api")
    app.include_router(mcp_router, prefix="/api")
    app.include_router(memory_router, prefix="/api")
    app.include_router(personas_router, prefix="/api")
    app.include_router(providers_router, prefix="/api")
    app.include_router(skills_router, prefix="/api")
    app.include_router(system_audit_router, prefix="/api")
    app.include_router(system_router, prefix="/api")
    app.include_router(universes_router, prefix="/api")
    app.include_router(workspace_router, prefix="/api")
    from tianshu.gateway.tongzheng_api import tongzheng_router

    app.include_router(tongzheng_router, prefix="/api")

    @app.api_route("/health", methods=["GET", "HEAD"])
    async def health():
        # legacy liveness 兼容（旧消费者）；owned 消费者一律使用 /health/ready
        return {"status": "ok"}

    @app.api_route("/health/live", methods=["GET", "HEAD"])
    async def health_live():
        # 只证明进程/事件循环可应答，不代表依赖就绪
        return {"schema_version": "1", "status": "live"}

    @app.api_route("/health/ready", methods=["GET", "HEAD"])
    async def health_ready(request: Request):
        from fastapi.responses import JSONResponse

        report = await asyncio.to_thread(_assess_app_readiness, app.state)
        http_status = 200 if report.status in ("ready", "degraded") else 503
        # 未认证调用方（任何 runtime mode）只拿摘要；已认证才看内部检查细节
        authenticated = request.scope.get("state", {}).get("auth_context") is not None
        if not authenticated:
            return JSONResponse(report.to_summary_dict(), status_code=http_status)
        detail = report.to_detail_dict()
        detail["profile"] = app.state.settings.startup_profile
        return JSONResponse(detail, status_code=http_status)

    # MCP server(可选能力:mcp extra 未安装则跳过)——外部 MCP 宿主经 POST /mcp 驱动天枢
    try:
        from tianshu.gateway.mcp_server import build_mcp_server

        mcp_server = build_mcp_server(app)
        app.state.tianshu_mcp_server = mcp_server
        app.mount("/mcp", mcp_server.streamable_http_app())
        logger.info("MCP server mounted at /mcp")
    except ImportError:
        logger.info("mcp extra not installed; MCP server disabled")

    # Conditionally mount frontend static files (container-integrated mode)
    web_static_dir = settings.static_dir
    if not web_static_dir:
        packaged_web = packaged_defaults().web_static_dir()
        web_static_dir = str(packaged_web) if packaged_web is not None else ""
    if web_static_dir and mount_web(app, web_static_dir):
        logger.info("Web UI mounted from %s", web_static_dir)
    else:
        logger.info("No static files found, running in API-only mode")

    return app
