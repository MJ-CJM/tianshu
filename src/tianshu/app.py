"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from tianshu import bootstrap
from tianshu.config import TianshuSettings
from tianshu.gateway import gateway_router
from tianshu.gateway.audit_api import audit_router
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.auth_api import auth_router
from tianshu.gateway.config_api import config_router
from tianshu.gateway.cost_api import cost_router
from tianshu.gateway.credentials_api import credentials_router
from tianshu.gateway.edicts_api import edicts_router
from tianshu.gateway.estop_api import estop_router
from tianshu.gateway.evals_api import evals_router
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: TianshuSettings = app.state.settings
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    bootstrap.wire_storage(app, settings)
    app.state.auth_service = AuthService(app.state.storage, settings)
    tools = await bootstrap.wire_tools(app, settings)
    skills, metrics_store = bootstrap.wire_skills(app, settings)
    bootstrap.wire_persona(app, settings, tools)
    prompt_builder = bootstrap.wire_memory_palace(app, settings, skills, metrics_store)
    bootstrap.wire_llm_config(app, settings)
    bootstrap.wire_skill_tools(app, settings, tools, skills, metrics_store)
    bootstrap.wire_provider_and_agent(app, settings, tools, skills, metrics_store, prompt_builder)
    bootstrap.wire_worker_lane(app, settings)
    bootstrap.wire_auditor(app, settings)
    bootstrap.wire_channels(app, settings)
    bootstrap.wire_executor(app, settings)
    bootstrap.wire_cost_manager(app, settings)
    await bootstrap.wire_channel_bots(app, settings)
    bootstrap.wire_policy(app, settings)
    bootstrap.wire_memory_manager(app, settings)
    bootstrap.wire_consultation(app, settings)
    bootstrap.wire_persona_quality(app, settings)
    bootstrap.wire_scheduling(app, settings)
    bootstrap.wire_plugins(app, settings)
    bootstrap.wire_hook_registrations(app, settings)
    bootstrap.wire_profile(app, settings)
    bootstrap.wire_skill_curator(app, settings)
    bootstrap.wire_universe(app, settings)
    bootstrap.wire_digest(app, settings)
    skills_watcher = bootstrap.wire_skills_watcher(app, settings)

    # --- Backward compat ---
    app.state.running_tasks = app.state.executor.running_tasks

    # --- Start scheduler ---
    await app.state.scheduler.start()

    # --- MCP server session manager(stateless 模式仍需运行;挂载见 create_app)---
    # 放独立后台 task:anyio TaskGroup 的 cancel scope 必须在同一 task 进出,
    # 而 lifespan 的 startup/teardown 可能被测试框架驱动在不同 task 上。
    _mcp_stop: asyncio.Event | None = None
    _mcp_task: asyncio.Task | None = None
    mcp_server = getattr(app.state, "tianshu_mcp_server", None)
    if mcp_server is not None:
        _mcp_stop = asyncio.Event()
        _mcp_ready: asyncio.Event = asyncio.Event()

        async def _run_mcp_session_manager() -> None:
            async with mcp_server.session_manager.run():
                _mcp_ready.set()
                assert _mcp_stop is not None
                await _mcp_stop.wait()

        _mcp_task = asyncio.create_task(_run_mcp_session_manager())
        await _mcp_ready.wait()

    # --- OTel GenAI 埋点(迭代 3):默认关;设 TIANSHU_OTEL_ENDPOINT 才导出 ---
    from tianshu import observability

    observability.init_tracing(settings)

    # --- opt-in 遥测(迭代 3,ADR-0003):默认关;首启明示,一行 env 永久关 ---
    from tianshu import telemetry

    if telemetry.is_enabled(settings.telemetry):
        logger.info(
            "[telemetry] 已启用(opt-in):仅上报版本+启动事件,不含任务内容。"
            "设 TIANSHU_TELEMETRY=off 永久关闭。"
        )
        await telemetry.emit_startup(settings, instance_id=f"{settings.host}:{settings.port}")

    # Start durable dispatch last: all consumers, scheduler recovery, MCP, tracing,
    # and telemetry startup have completed. Any failure before yield must confirm
    # this worker stopped before the startup exception is allowed to escape.
    try:
        await bootstrap.wire_outbox(app, settings)
        logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    except BaseException:
        outbox_lifecycle = getattr(app.state, "outbox_lifecycle", None)
        if outbox_lifecycle is not None:
            await outbox_lifecycle.stop()
        raise
    yield

    # Do not close consumers or shared Storage while a durable drain is live.
    await app.state.outbox_lifecycle.stop()

    # --- MCP server session manager 停止 ---
    if _mcp_stop is not None and _mcp_task is not None:
        _mcp_stop.set()
        try:
            await _mcp_task
        except Exception:
            logger.exception("[mcp-server] session manager shutdown error")

    # --- Graceful shutdown ---
    app.state.agent.request_shutdown()
    if skills_watcher:
        skills_watcher.stop()
    if hasattr(app.state, "_digest_task") and not app.state._digest_task.done():
        app.state._digest_task.cancel()
    await app.state.scheduler.stop()
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
    app.include_router(cost_router, prefix="/api")
    app.include_router(credentials_router, prefix="/api")
    app.include_router(edicts_router, prefix="/api")
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

        from tianshu.diagnostics import ReadinessInputs, assess_readiness, provider_config_check

        state = app.state

        def _probe_database() -> tuple[bool, bool]:
            """(可连接, 迁移到位)。与 Doctor 同口径：pending_migrations 判定迁移。

            纯同步 SQLite，走 storage 锁并在线程池执行，不阻塞事件循环。
            """
            from tianshu.storage.migration_ledger import pending_migrations
            from tianshu.storage.migrations import MIGRATIONS

            storage = state.storage
            try:
                with storage._lock:
                    storage._conn.execute("SELECT 1").fetchone()
            except Exception:  # noqa: BLE001 - readiness 探针不得抛出
                return False, False
            try:
                with storage._lock:
                    pending = pending_migrations(storage._conn, MIGRATIONS)
            except Exception:  # noqa: BLE001
                return True, False
            return True, not pending

        database_ok, migrations_ok = await asyncio.to_thread(_probe_database)

        def _resources_ok() -> bool:
            from tianshu.resources import catalog

            return (catalog.persona_defaults() / "court" / "COURT.md").is_file()

        def _optional_integrations() -> dict[str, bool | None]:
            """MCP 的**真实**健康信号。

            两个陷阱：会话对象存在 ≠ 已连接（真实字段是 ``status``）；启动就没连上的
            server 压根不会进 ``sessions``（``_start_one`` 失败返回 None）——所以必须拿
            "应连"基线（enabled ∧ 准入）去比"实连"，否则最常见的生产故障（npx 拉包失败、
            命令不存在）在健康端点上完全不可见。
            """
            manager = getattr(state, "mcp_manager", None)
            if manager is None:
                return {"mcp": None}
            expected = set(manager.admitted_enabled_names)
            if not expected:
                return {"mcp": None}  # 未配置任何 enabled server = 未启用
            connected = {
                name for name, session in manager.sessions.items() if session.status == "connected"
            }
            # 启动中的 server 既未连上也未失败：算作失败会让每次冷启动先报一段假降级。
            # 只有"既没连上、也不在启动中"才是真失败。
            failed = expected - connected - set(manager.starting_names)
            return {"mcp": not failed}

        def _provider_ready() -> bool:
            """判定源 = 运行时实际会用到的 active 配置，不是 env（见 provider_config_check）。"""
            return (
                provider_config_check(
                    profile=state.settings.startup_profile,
                    effective=state.config_manager.state,
                    config_source="runtime",
                ).status
                == "pass"
            )

        report = assess_readiness(
            ReadinessInputs(
                database_ok=lambda: database_ok,
                migrations_current=lambda: migrations_ok,
                scheduler_ready=lambda: state.scheduler.is_ready,
                worker_ready=lambda: state.worker_pool.is_ready,
                outbox_ready=lambda: state.outbox_lifecycle.is_ready,
                resources_ok=_resources_ok,
                provider_ready=_provider_ready,
                provider_profile=lambda: state.settings.startup_profile,
                workspace_ready=lambda: state.workspace_service.is_ready,
                optional_integrations=_optional_integrations,
            )
        )
        http_status = 200 if report.status in ("ready", "degraded") else 503
        # 未认证调用方（任何 runtime mode）只拿摘要；已认证才看内部检查细节
        authenticated = request.scope.get("state", {}).get("auth_context") is not None
        if not authenticated:
            return JSONResponse(report.to_summary_dict(), status_code=http_status)
        detail = report.to_detail_dict()
        detail["profile"] = state.settings.startup_profile
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
