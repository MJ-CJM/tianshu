"""FastAPI application entry point."""

from __future__ import annotations

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
from tianshu.gateway.execution_api import execution_router
from tianshu.gateway.hongluisi_api import hongluisi_router
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

    logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    yield

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
    app = FastAPI(title="Tianshu", version="0.1.0", lifespan=lifespan)
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
    app.include_router(execution_router, prefix="/api")
    app.include_router(hongluisi_router, prefix="/api")
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

    # Conditionally mount frontend static files (container-integrated mode)
    settings = TianshuSettings()
    if mount_web(app, settings.static_dir):
        logger.info("Web UI mounted from %s", settings.static_dir)
    else:
        logger.info("No static files found, running in API-only mode")

    return app
