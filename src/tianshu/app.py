"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tianshu.agent import Agent
from tianshu.config import TianshuSettings
from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.gateway import gateway_router
from tianshu.skills.loader import SkillsLoader
from tianshu.storage import Storage
from tianshu.tools.builtins import register_builtins
from tianshu.tools.registry import ToolRegistry
from tianshu.web import mount_web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = TianshuSettings()

    # Storage
    storage = Storage(settings.db_path)
    storage.init_db()
    app.state.storage = storage

    # Tools
    tools = ToolRegistry()
    register_builtins(tools, settings.workspace_dir)

    # Skills
    builtin_skills_dir = Path(__file__).parent / "skills" / "builtin"
    workspace_path = (
        Path(settings.workspace_dir).resolve()
        if settings.workspace_dir != "."
        else None
    )
    skills = SkillsLoader(
        builtin_dir=builtin_skills_dir,
        workspace_dir=workspace_path,
        char_budget=settings.skills_char_budget,
    )

    # LLM Config Manager
    initial_state = LLMConfigState(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        max_tokens=settings.llm_max_tokens,
    )
    config_manager = ConfigManager(initial_state)
    app.state.config_manager = config_manager

    # Agent
    agent = Agent(
        config_manager=config_manager, tools=tools, skills=skills, settings=settings
    )
    app.state.agent = agent
    app.state.settings = settings

    app.state.running_tasks = set()

    logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    yield

    # Graceful shutdown: cancel running agent tasks, then clean up
    agent.request_shutdown()
    for task in list(app.state.running_tasks):
        task.cancel()
    await asyncio.gather(*app.state.running_tasks, return_exceptions=True)
    storage.close()
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
