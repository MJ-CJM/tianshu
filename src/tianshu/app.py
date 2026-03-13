"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tianshu.agent import Agent
from tianshu.config import TianshuSettings
from tianshu.gateway import gateway_router
from tianshu.llm import LLMClient
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

    # LLM
    llm = LLMClient(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        max_retries=settings.llm_max_retries,
    )

    # Agent
    agent = Agent(llm=llm, tools=tools, skills=skills, settings=settings)
    app.state.agent = agent
    app.state.settings = settings

    # Graceful shutdown (only in main thread)
    import threading

    if threading.current_thread() is threading.main_thread():
        def _handle_signal(sig, _frame):
            logger.info("Received signal %s, shutting down...", sig)
            agent.request_shutdown()

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

    logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    yield

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
