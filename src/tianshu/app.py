"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tianshu import bootstrap
from tianshu.config import TianshuSettings
from tianshu.gateway import gateway_router
from tianshu.gateway.audit_api import audit_router
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
from tianshu.gateway.universes_api import universes_router
from tianshu.logging_config import setup_logging
from tianshu.web import mount_web

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = TianshuSettings()
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    bootstrap.wire_storage(app, settings)
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

    logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    yield

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
    await app.state.worker_pool.shutdown()
    await app.state.executor.shutdown()
    try:
        await app.state.mcp_manager.shutdown()
    except Exception:
        logger.exception("[mcp] manager shutdown error")
    await app.state.bot_manager.stop_all()
    app.state.drawer_store.close()
    app.state.storage.close()
    logger.info("Tianshu shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Tianshu", version="0.2.7", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(gateway_router, prefix="/api")
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
    app.include_router(system_router, prefix="/api")
    app.include_router(universes_router, prefix="/api")
    from tianshu.gateway.tongzheng_api import tongzheng_router

    app.include_router(tongzheng_router, prefix="/api")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

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
    settings = TianshuSettings()
    if mount_web(app, settings.static_dir):
        logger.info("Web UI mounted from %s", settings.static_dir)
    else:
        logger.info("No static files found, running in API-only mode")

    return app
