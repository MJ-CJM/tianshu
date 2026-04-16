"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.cost.manager import CostManager
from tianshu.consultation.session import ConsultationSession
from tianshu.executor.agent import Agent
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.lanes import LaneManager
from tianshu.executor.worker_pool import WorkerPool
from tianshu.providers.manager import ProviderManager
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.executor import Executor
from tianshu.executor.hooks import HookRegistry, HookType
from tianshu.executor.policy_hook import PolicyHook
from tianshu.persona.evaluator import PerformanceEvaluator
from tianshu.gateway import gateway_router
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.manager import MemoryManager
from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.notifier import Notifier
from tianshu.plugins.api import PluginApi
from tianshu.plugins.loader import PluginLoader
from tianshu.persona.loader import PersonaLoader
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.persona.selector import OfficialSelector
from tianshu.planner.planner import Planner
from tianshu.scheduler.scheduler import Scheduler
from tianshu.skills.loader import SkillsLoader, SkillsWatcher
from tianshu.skills.metrics import SkillMetricsStore
from tianshu.skills.reviewer import SkillReviewHandler
from tianshu.skills.validator import SkillValidator
from tianshu.storage import Storage
from tianshu.tools.builtins import register_builtins
from tianshu.tools.memory_tools import register_memory_tools
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.tools.policy_store import (
    CompositeSessionRuleStore,
    InMemorySessionRuleStore,
    SqliteSessionRuleStore,
)
from tianshu.tools.skill_tools import register_skill_tools
from tianshu.tools.registry import ToolRegistry
from tianshu.web import mount_web

from tianshu.logging_config import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = TianshuSettings()
    setup_logging(log_dir=settings.log_dir, console_level=settings.log_level)

    # --- Storage ---
    storage = Storage(settings.db_path)
    storage.init_db()
    app.state.storage = storage

    # --- EventBus ---
    event_bus = EventBus(storage=storage)
    app.state.event_bus = event_bus

    # --- HookRegistry ---
    hook_registry = HookRegistry()
    app.state.hook_registry = hook_registry

    # --- Tools ---
    tools = ToolRegistry()
    register_builtins(tools, settings.workspace_dir)

    # --- Skills ---
    builtin_skills_dir = Path(__file__).parent / "skills" / "builtin"
    workspace_path = (
        Path(settings.workspace_dir).resolve()
        if settings.workspace_dir != "."
        else None
    )
    user_skills_dir = Path("~/.tianshu/skills").expanduser()
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    skills = SkillsLoader(
        builtin_dir=builtin_skills_dir,
        workspace_dir=workspace_path,
        user_dir=user_skills_dir,
        char_budget=settings.skills_char_budget,
    )
    metrics_store = SkillMetricsStore(storage._conn)
    register_skill_tools(tools, skills, metrics_store=metrics_store)
    register_memory_tools(tools, storage)

    # --- Memory dir ---
    memory_dir = Path(settings.memory_dir).expanduser()

    # --- Persona ---
    personas_dir = Path(__file__).parent.parent.parent / "personas"
    persona_loader = PersonaLoader(personas_dir, storage=storage)
    persona_loader.load_all()
    app.state.persona_loader = persona_loader

    # --- Memory Palace (Drawer Store) ---
    memory_config = MemoryConfig()
    drawer_db_path = memory_dir / "drawers.sqlite3"
    drawer_db_path.parent.mkdir(parents=True, exist_ok=True)
    drawer_store = DrawerStore(str(drawer_db_path))
    app.state.drawer_store = drawer_store
    app.state.memory_config = memory_config

    prompt_builder = PromptBuilder(
        personas_dir=personas_dir,
        skills_loader=skills,
        memory_dir=memory_dir,
        metrics_store=metrics_store,
        drawer_store=drawer_store,
        memory_config=memory_config,
    )

    # --- LLM Config Manager ---
    initial_state = LLMConfigState(
        name=settings.llm_model,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        api_base=settings.llm_api_base,
        max_retries=settings.llm_max_retries,
        temperature=settings.llm_temperature,
        top_p=settings.llm_top_p,
        max_tokens=settings.llm_max_tokens,
    )
    agent_config = AgentConfigState(
        agent_max_iterations=settings.agent_max_iterations,
        agent_timeout_seconds=settings.agent_timeout_seconds,
        skills_char_budget=settings.skills_char_budget,
    )
    config_manager = ConfigManager(initial_state, agent_config=agent_config, storage=storage)
    app.state.config_manager = config_manager

    # --- ProviderManager ---
    provider_manager = ProviderManager(storage=storage, config_manager=config_manager)
    provider_manager.sync_all()
    app.state.provider_manager = provider_manager

    # --- Agent ---
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=skills,
        hook_registry=hook_registry,
        prompt_builder=prompt_builder,
        provider_manager=provider_manager,
        metrics_store=metrics_store,
    )
    app.state.agent = agent
    app.state.skills_loader = skills
    app.state.tool_registry = tools
    app.state.prompt_builder = prompt_builder
    app.state.personas_dir = personas_dir

    # --- WorkerPool & LaneManager ---
    worker_pool = WorkerPool(max_concurrency=settings.max_global_concurrency)
    app.state.worker_pool = worker_pool

    lane_manager = LaneManager(max_global_concurrency=settings.max_global_concurrency)
    app.state.lane_manager = lane_manager

    # --- Auditor ---
    auditor = Auditor(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
    )
    app.state.auditor = auditor

    # --- ChannelRegistry ---
    channel_registry = ChannelRegistry()
    app.state.channel_registry = channel_registry

    # Register channels from environment
    if settings.feishu_webhook:
        from tianshu.notifier.channels.feishu import FeishuChannel
        channel_registry.register(FeishuChannel(settings.feishu_webhook))
    if settings.dingtalk_webhook:
        from tianshu.notifier.channels.dingtalk import DingTalkChannel
        channel_registry.register(DingTalkChannel(
            settings.dingtalk_webhook,
            secret=settings.dingtalk_secret,
        ))
    if settings.smtp_host:
        from tianshu.notifier.channels.email import EmailChannel
        channel_registry.register(EmailChannel(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            from_addr=settings.smtp_from,
            to_addrs=settings.smtp_to.split(",") if settings.smtp_to else [],
        ))

    # --- Notifier ---
    notifier = Notifier(storage=storage, channel_registry=channel_registry)
    app.state.notifier = notifier

    # --- SessionRuleStore ---
    session_rule_store = CompositeSessionRuleStore(
        in_memory=InMemorySessionRuleStore(),
        sqlite=SqliteSessionRuleStore(storage=storage),
    )
    app.state.session_rule_store = session_rule_store

    # --- Executor ---
    executor = Executor(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        hook_registry=hook_registry,
        session_rule_store=session_rule_store,
    )
    executor.set_agent(agent)
    executor.set_persona_loader(persona_loader)
    app.state.executor = executor

    # --- DAGScheduler ---
    dag_scheduler = DAGScheduler(
        worker_pool=worker_pool,
        agent=agent,
        storage=storage,
        event_bus=event_bus,
        persona_loader=persona_loader,
        prompt_builder=prompt_builder,
    )
    executor.set_dag_scheduler(dag_scheduler)
    executor.set_lane_manager(lane_manager)
    app.state.dag_scheduler = dag_scheduler

    # --- ApprovalManager ---
    approval_manager = ApprovalManager(
        event_bus=event_bus,
        storage=storage,
        session_rule_store=session_rule_store,
    )
    app.state.approval_manager = approval_manager

    # --- PolicyEngine + PolicyHook ---
    policy_engine = PolicyEngine(rules=build_default_rules())
    app.state.policy_engine = policy_engine

    policy_hook = PolicyHook(
        engine=policy_engine,
        workspace_root=Path(settings.workspace_dir).resolve(),
        storage=storage,
        tool_registry=tools,
        session_rule_store=session_rule_store,
        approval_manager=approval_manager,
        notifier=notifier,
    )
    app.state.policy_hook = policy_hook
    hook_registry.register(
        HookType.BEFORE_TOOL_CALL,
        policy_hook.on_before_tool_call,
        priority=5,  # 先于 approval_manager.on_before_tool_call(priority=10) 执行
    )

    # --- MemoryManager ---
    memory_manager = MemoryManager(
        storage=storage,
        config_manager=config_manager,
        hook_registry=hook_registry,
        personas_dir=personas_dir,
        memory_dir=memory_dir,
        drawer_store=drawer_store,
        memory_config=memory_config,
    )
    memory_manager.ensure_memory_dirs()
    app.state.memory_manager = memory_manager

    # --- CostManager ---
    cost_manager = CostManager(storage=storage, event_bus=event_bus)
    app.state.cost_manager = cost_manager

    # --- ConsultationSession ---
    consultation = ConsultationSession(
        persona_loader=persona_loader,
        config_manager=config_manager,
        provider_manager=provider_manager,
        memory_manager=memory_manager,
    )
    app.state.consultation = consultation

    # --- PerformanceEvaluator ---
    evaluator = PerformanceEvaluator(storage=storage)
    app.state.evaluator = evaluator

    # --- OfficialSelector ---
    official_selector = OfficialSelector(persona_loader)
    app.state.official_selector = official_selector

    # --- Planner ---
    planner = Planner(
        event_bus=event_bus,
        storage=storage,
        config_manager=config_manager,
        official_selector=official_selector,
        persona_loader=persona_loader,
        prompt_builder=prompt_builder,
        tool_registry=tools,
    )
    app.state.planner = planner

    # --- Scheduler ---
    scheduler = Scheduler(
        event_bus=event_bus,
        storage=storage,
    )
    app.state.scheduler = scheduler

    # --- EventBus subscriptions ---
    event_bus.on("edict.submitted", scheduler.handle_submitted)
    event_bus.on("edict.scheduled", planner.handle_scheduled, priority=50)
    event_bus.on("plan.completed", executor.handle_plan_completed, priority=100)
    event_bus.on("execution.completed", auditor.handle_execution_completed)
    event_bus.on("execution.completed", cost_manager.handle_execution_completed, priority=150)
    event_bus.on("execution.completed", memory_manager.handle_execution_completed, priority=200)
    event_bus.on("execution.failed", notifier.handle_execution_failed)
    event_bus.on("execution.failed", cost_manager.handle_execution_failed, priority=150)
    event_bus.on("audit.completed", notifier.handle_audit_completed)
    event_bus.on("audit.completed", memory_manager.handle_audit_completed, priority=200)
    event_bus.on("cost.budget_exceeded", notifier.handle_execution_failed)

    # --- PluginApi ---
    plugin_api = PluginApi(
        storage=storage,
        tool_registry=tools,
        hook_registry=hook_registry,
        channel_registry=channel_registry,
        provider_manager=provider_manager,
        skills_loader=skills,
    )
    app.state.plugin_api = plugin_api

    # Discover and register local plugins
    plugins_dir = Path(__file__).parent.parent.parent / "plugins"
    plugin_loader = PluginLoader(plugins_dir)
    for manifest in plugin_loader.discover():
        plugin_api.register_plugin(manifest)

    # Wire event writer for hook execution events (8.2 support)
    hook_registry.set_event_writer(storage)

    # --- Hook registrations ---
    hook_registry.register(HookType.BEFORE_AGENT_START, memory_manager.on_before_agent_start, priority=50)
    hook_registry.register(HookType.BEFORE_ITERATION, cost_manager.on_before_iteration, priority=10)
    hook_registry.register(HookType.LLM_OUTPUT, cost_manager.on_llm_output, priority=50)
    hook_registry.register(HookType.AGENT_END, memory_manager.on_agent_end, priority=100)
    hook_registry.register(HookType.BEFORE_TOOL_CALL, approval_manager.on_before_tool_call, priority=10)

    # Skill review hook (learning loop)
    skill_validator = SkillValidator()
    skill_reviewer = SkillReviewHandler(skills, config_manager, skill_validator)
    hook_registry.register(HookType.AGENT_END, skill_reviewer.on_agent_end, priority=200)

    # --- DigestGenerator ---
    from tianshu.notifier.digest import DigestGenerator
    digest_generator = DigestGenerator(storage=storage)
    app.state.digest_generator = digest_generator

    # Schedule daily digest via cron loop
    async def _digest_cron_loop() -> None:
        """Run daily digest at roughly every 24h."""
        import asyncio
        while True:
            try:
                await asyncio.sleep(86400)  # 24 hours
                digest = digest_generator.generate_daily()
                await notifier.broadcast_ws(digest)
                # Dispatch to all registered external channels
                await channel_registry.send_all(digest, str(digest))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Digest generation failed")

    digest_task = asyncio.create_task(_digest_cron_loop())
    app.state._digest_task = digest_task

    # --- Skills hot-reload watcher ---
    skills_watcher = SkillsWatcher(skills)
    try:
        skills_watcher.start()
    except Exception:
        logger.warning("SkillsWatcher failed to start (watchdog may not be installed)")
        skills_watcher = None

    # --- Backward compat ---
    app.state.running_tasks = executor.running_tasks

    # --- Start scheduler ---
    await scheduler.start()

    logger.info("Tianshu started on %s:%s", settings.host, settings.port)
    yield

    # --- Graceful shutdown ---
    agent.request_shutdown()
    if skills_watcher:
        skills_watcher.stop()
    if hasattr(app.state, "_digest_task") and not app.state._digest_task.done():
        app.state._digest_task.cancel()
    await scheduler.stop()
    await worker_pool.shutdown()
    await executor.shutdown()
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
