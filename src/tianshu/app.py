"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.consultation.session import ConsultationSession
from tianshu.cost.manager import CostManager
from tianshu.executor.agent import Agent
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.executor import Executor
from tianshu.executor.hooks import HookRegistry, HookType
from tianshu.executor.lanes import LaneManager
from tianshu.executor.orchestrator.archive import archive_old_iterations
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.worker_pool import WorkerPool
from tianshu.gateway import gateway_router
from tianshu.gateway.credentials_api import credentials_router
from tianshu.gateway.hongluisi_api import hongluisi_router
from tianshu.logging_config import setup_logging
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.manager import MemoryManager
from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.notifier import Notifier
from tianshu.persona.evaluator import PerformanceEvaluator
from tianshu.persona.loader import PersonaLoader
from tianshu.persona.profile_synthesizer import ProfileSynthesizer
from tianshu.persona.profile_trigger import ProfileTrigger
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.persona.selector import OfficialSelector
from tianshu.persona.template_library import TemplateLibrary
from tianshu.planner.planner import Planner
from tianshu.plugins.api import PluginApi
from tianshu.plugins.loader import PluginLoader
from tianshu.providers.manager import ProviderManager
from tianshu.scheduler.scheduler import Scheduler
from tianshu.skills.curator import SkillCurator
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
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.skill_tools import register_skill_tools
from tianshu.universe.code_mutator import CodeMutator
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.deployer import Deployer, DeployPointer
from tianshu.universe.eval_harness import EvalHarness
from tianshu.universe.gate import Gate
from tianshu.universe.manager import UniverseManager
from tianshu.universe.sandbox import SandboxRunner
from tianshu.universe.store import UniverseStore
from tianshu.web import mount_web

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
    register_builtins(
        tools, settings.workspace_dir,
        storage=storage, event_bus=event_bus,
    )

    # --- MCP（藏兵阁外挂）：fire-and-forget 启动，不阻塞 lifespan ---
    # config 同步读（毫秒级），session 启动放后台（broken/慢 server 的退避不会卡 web）。
    from tianshu.tools.mcp import MCPManager
    mcp_manager = MCPManager(tools, storage=storage)
    app.state.mcp_manager = mcp_manager
    try:
        mcp_manager.load_config()
    except Exception:
        logger.exception("[mcp] load_config failed; continuing without MCP tools")

    async def _mcp_bg_start() -> None:
        try:
            await mcp_manager.start()
        except Exception:
            logger.exception("[mcp] background start failed")

    app.state._mcp_start_task = asyncio.create_task(
        _mcp_bg_start(), name="mcp-bg-start"
    )

    # 应用 DB 里持久化的禁用列表（live toggle 在运行时通过 PATCH /api/tools/{name} 更新）
    try:
        disabled = storage.list_disabled_tools()
        tools.apply_disabled(disabled)
        if disabled:
            logger.info("[tools] disabled at startup: %s", sorted(disabled))
    except Exception:
        logger.exception("[tools] failed to load tool_switches; treating all as enabled")

    # 系统默认引擎覆盖（可以在鸿胪寺页动态改，启动时从 DB 加载一次）
    try:
        from tianshu.tools.policy_profile import set_system_engine_overrides
        prefs = storage.get_engine_preferences()
        set_system_engine_overrides(
            fetch_chain=prefs["fetch_chain"],
            search_provider=prefs["search_provider"] or "",
            fallback_mode=prefs["fallback_mode"] or "",
        )
        if any([prefs["fetch_chain"], prefs["search_provider"], prefs["fallback_mode"]]):
            logger.info("[hongluisi] system engine overrides loaded: %s", prefs)
    except Exception:
        logger.exception("[hongluisi] failed to load engine_preferences; using profile defaults")

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
    # register_skill_tools 移到 config_manager 定义之后（它依赖 config_manager.agent_config）

    # --- Memory dir ---
    memory_dir = Path(settings.memory_dir).expanduser()

    # --- Persona ---
    personas_dir = Path(__file__).parent.parent.parent / "personas"

    # memory_search 一直注册；memory_write 需要 memory_dir + personas_dir + event_bus
    register_memory_tools(
        tools,
        storage,
        memory_dir=memory_dir,
        personas_dir=personas_dir,
        event_bus=event_bus,
    )

    runtime_personas_dir = Path(settings.runtime_personas_dir).expanduser()
    persona_loader = PersonaLoader(
        personas_dir,
        storage=storage,
        runtime_personas_dir=runtime_personas_dir,
    )
    persona_loader.load_all()
    app.state.persona_loader = persona_loader
    app.state.runtime_personas_dir = runtime_personas_dir

    # 角色模板库（vendored agency-agents，见 templates/persona/）
    templates_dir = Path(__file__).parent.parent.parent / "templates" / "persona"
    template_library = TemplateLibrary(templates_dir)
    template_library.load()
    app.state.template_library = template_library

    # Re-register submit_edict tool now that persona_loader is available
    # （register_builtins 阶段 persona_loader 还未构造，此处覆盖以启用 persona 校验）
    from tianshu.tools.submit_edict import register_submit_edict
    register_submit_edict(
        tools,
        storage=storage,
        event_bus=event_bus,
        persona_loader=persona_loader,
    )

    # list_personas（T0 只读）—— 让 LLM 能查 DB 实际注册的官员名册。
    # 与 edict 工具组同 toggle —— 因为它的核心用途是支持"颁敕时按擅长领域指派"，
    # 跟 submit_edict 同生命周期，启用语义一致。
    from tianshu.tools.persona_query import register_list_personas
    register_list_personas(tools, storage=storage)

    # 默认禁用敕令管理工具集；通政司 toggle 打开后启用（reload 时同步）
    # submit_edict（写）+ list_edicts / get_edict_status / list_personas（读）
    # 作为同一捆绑能力 —— 助手能"颁敕"才需要"看官员名册帮决策指派"。
    edict_tools = (
        "submit_edict", "list_edicts", "get_edict_status", "list_personas",
    )
    feishu_channel_cfg = storage.get_channel_config("feishu")
    if not (feishu_channel_cfg and feishu_channel_cfg.get("enable_edict_submission")):
        for tool_name in edict_tools:
            tools.disable(tool_name)
        logger.info(
            "[tools] edict tools disabled at startup (toggle 通政司「允许助手下发敕令」可启用)：%s",
            ", ".join(edict_tools),
        )
    else:
        logger.info("[tools] edict tools enabled at startup: %s", ", ".join(edict_tools))

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
        storage=storage,
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
        parallel_universe_enabled=settings.parallel_universe_enabled,
    )
    config_manager = ConfigManager(initial_state, agent_config=agent_config, storage=storage)
    app.state.config_manager = config_manager

    # skill 工具注册：依赖 config_manager.agent_config（guard 开关）；
    # tools / skills / metrics_store / event_bus 均已在前面定义
    register_skill_tools(
        tools, skills, metrics_store=metrics_store,
        guard_agent_created=config_manager.agent_config.skill_guard_agent_created,
        event_bus=event_bus,
    )

    # --- ProviderManager ---
    provider_manager = ProviderManager(storage=storage, config_manager=config_manager)
    provider_manager.sync_all()
    app.state.provider_manager = provider_manager

    # --- Agent ---
    # 实时读 channel_configs.feishu.assistant_persona_id —— agent 用它判断
    # 当前执行 persona 是不是助手，非助手过滤掉 ASSISTANT_ONLY_TOOLS
    # （submit_edict 等），防 cron 触发的执行 persona 递归颁敕。
    def _assistant_persona_id_provider() -> str | None:
        cfg = storage.get_channel_config("feishu")
        return (cfg or {}).get("assistant_persona_id")

    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=skills,
        hook_registry=hook_registry,
        prompt_builder=prompt_builder,
        provider_manager=provider_manager,
        metrics_store=metrics_store,
        assistant_persona_id_provider=_assistant_persona_id_provider,
    )
    app.state.agent = agent
    app.state.skills_loader = skills
    app.state.skill_metrics_store = metrics_store
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

    # EVAL_MODE：沙箱评估时不挂真实外发渠道，避免评估副作用
    if not settings.eval_mode:
        # Register channels from environment
        if settings.feishu_app_id:
            # app bot 模式：FeishuOutbound 在 FeishuBot.start() 内部直接订阅 EventBus，
            # 不通过 ChannelRegistry。互斥：旧 incoming webhook URL 完全跳过。
            pass
        elif settings.feishu_webhook:
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
    else:
        logger.info("[eval_mode] skipping real outbound channel registration (feishu/dingtalk/smtp)")

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

    # --- CostManager（提前创建：FeishuBot /budget 卡片需要）---
    cost_manager = CostManager(storage=storage, event_bus=event_bus)
    app.state.cost_manager = cost_manager

    # --- Channel bots（多实例：每渠道 N 个 bot 实例）---
    from tianshu.gateway.bot_manager import ChannelBotManager

    bot_manager = ChannelBotManager(
        storage=storage,
        event_bus=event_bus,
        approval_manager=approval_manager,
        executor=executor,
        notifier=notifier,
        persona_loader=persona_loader,
        provider_manager=provider_manager,
        cost_manager=cost_manager,
        env_settings=settings,
        app=app,
    )
    app.state.bot_manager = bot_manager
    # EVAL_MODE：沙箱评估时不启动 bot（Feishu/Telegram），避免真实消息推送副作用
    if not settings.eval_mode:
        try:
            await bot_manager.start_all()
        except Exception:
            logger.exception("[gateway] bot_manager.start_all failed; web stays up")
    else:
        logger.info("[eval_mode] skipping bot startup (feishu/telegram bots not started)")
    # 向后兼容别名：部分代码/测试读取 app.state.feishu_bot / telegram_bot（默认实例）。
    app.state.feishu_bot = bot_manager.get("feishu-default")
    app.state.telegram_bot = bot_manager.get("telegram-default")

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
        event_bus=event_bus,
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

    # CostManager 已在 FeishuBot 之前创建（双模式 /budget 卡片需要）

    # --- ConsultationSession ---
    consultation = ConsultationSession(
        persona_loader=persona_loader,
        config_manager=config_manager,
        provider_manager=provider_manager,
        memory_manager=memory_manager,
    )
    app.state.consultation = consultation

    # --- OrchestratorContext for long-task outer loop ---
    from tianshu.executor.orchestrator import OrchestratorContext
    orch_ctx = OrchestratorContext(
        agent=agent,
        storage=storage,
        bus=event_bus,
        actor_llm=provider_manager.get_client(),
        critic_llm=provider_manager.get_client(),
        critic_fallback_llm=None,
        consultation_session=consultation,
        notifier=notifier,
        approvals=approval_manager,  # ApprovalManager outer-loop 接口（wait_for_outer_loop_decision）
        persona_loader=persona_loader,
        provider_manager=provider_manager,
    )
    executor.set_orchestrator_context(orch_ctx)
    app.state.orchestrator_ctx = orch_ctx

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

    # schedule_edict tool —— 对话中安排定时/周期敕令（需 scheduler 就绪后注册）
    # 与 submit_edict 同属"敕令工具组"，同一 enable_edict_submission toggle 控制。
    from tianshu.tools.schedule_edict import register_schedule_edict
    register_schedule_edict(
        tools,
        storage=storage,
        scheduler=scheduler,
        persona_loader=persona_loader,
    )
    if not (feishu_channel_cfg and feishu_channel_cfg.get("enable_edict_submission")):
        tools.disable("schedule_edict")

    # --- EventBus subscriptions ---
    event_bus.on("edict.submitted", scheduler.handle_submitted)
    event_bus.on("edict.scheduled", planner.handle_scheduled, priority=50)
    event_bus.on("plan.completed", executor.handle_plan_completed, priority=100)
    event_bus.on("edict.resume", executor.handle_resume)
    event_bus.on("execution.completed", auditor.handle_execution_completed)
    event_bus.on("execution.completed", cost_manager.handle_execution_completed, priority=150)
    event_bus.on("execution.completed", memory_manager.handle_execution_completed, priority=200)
    event_bus.on("execution.failed", notifier.handle_execution_failed)
    event_bus.on("execution.failed", cost_manager.handle_execution_failed, priority=150)
    event_bus.on("audit.completed", notifier.handle_audit_completed)
    event_bus.on("audit.completed", memory_manager.handle_audit_completed, priority=200)
    event_bus.on("cost.budget_exceeded", notifier.handle_execution_failed)

    # 平行位面：memorial 完成 → 重算其所属位面的适应度
    from tianshu.universe.fitness import compute_fitness

    async def _update_universe_fitness(event) -> None:
        if not config_manager.agent_config.parallel_universe_enabled:
            return
        memorial_id = event.memorial_id
        if not memorial_id:
            return
        memorial = storage.get_memorial(memorial_id)
        universe_id = getattr(memorial, "universe_id", None) if memorial else None
        if not universe_id:
            return
        stats = storage.universe_memorial_stats(universe_id)
        weights = config_manager.agent_config.universe_fitness_weights
        storage.update_universe_fitness(universe_id, compute_fitness(stats, weights=weights))

    event_bus.on("execution.completed", _update_universe_fitness, priority=250)
    event_bus.on("execution.failed", _update_universe_fitness, priority=250)
    event_bus.on("audit.completed", _update_universe_fitness, priority=250)
    # 长任务 outer loop 事件实时广播到 WebSocket
    for outer_loop_event in (
        "outer_loop.started",
        "outer_loop.iteration.started",
        "outer_loop.iteration.finished",
        "outer_loop.checks.failed",
        "outer_loop.escalated",
        "outer_loop.completed",
        "outer_loop.exhausted",
        "outer_loop.approval.requested",
        "outer_loop.approval.received",
        "outer_loop.supervision_completed",
        "outer_loop.resumed",
    ):
        event_bus.on(outer_loop_event, notifier.handle_outer_loop_event)

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
    skill_reviewer = SkillReviewHandler(
        skills, config_manager, skill_validator, metrics_store=metrics_store,
    )
    hook_registry.register(HookType.AGENT_END, skill_reviewer.on_agent_end, priority=200)
    skill_reviewer.attach_event_bus(event_bus)

    # --- ProfileSynthesizer + ProfileTrigger ---
    profile_synthesizer = ProfileSynthesizer(
        llm_client=provider_manager.get_client(),
        drawer_store=drawer_store,
        storage=storage,
        skill_metrics_store=metrics_store,
        personas_runtime_dir=runtime_personas_dir,
        persona_loader=persona_loader,
        memory_dir=memory_dir,
        personas_dir=personas_dir,
    )
    profile_synthesizer.attach_event_bus(event_bus)
    app.state.profile_synthesizer = profile_synthesizer

    profile_trigger = ProfileTrigger(profile_synthesizer, storage)
    app.state.profile_trigger = profile_trigger

    hook_registry.register(HookType.AGENT_END, profile_trigger.handle_agent_end, priority=250)

    # --- SkillCurator (修撰) — 周期性技能库自优化 ---
    skill_curator = SkillCurator(
        llm_client=provider_manager.get_client(),
        loader=skills,
        metrics_store=metrics_store,
        storage=storage,
        config_manager=config_manager,
        runtime_dir=Path("~/.tianshu/runtime").expanduser(),
    )
    skill_curator.attach_event_bus(event_bus)
    app.state.skill_curator = skill_curator

    # --- 平行位面（parallel universe）---
    def _universe_config_snapshot() -> dict:
        from dataclasses import asdict
        return {"agent_config": asdict(config_manager.agent_config)}

    def _universe_config_apply(manifest: dict) -> None:
        ac = manifest.get("agent_config") or {}
        if ac:
            config_manager.update_agent_config(**{
                k: ac[k] for k in ("agent_max_iterations", "agent_timeout_seconds",
                                   "skills_char_budget") if k in ac
            })

    universe_store = UniverseStore(
        root=Path("~/.tianshu/universes").expanduser(),
        live_personas_dir=persona_loader.runtime_dir,
        live_skills_dir=skills.user_dir,
    )
    code_variant_store = CodeVariantStore(
        repo_root=Path(__file__).resolve().parents[2],
        worktrees_root=Path("~/.tianshu/universes/worktrees").expanduser(),
    )
    deploy_pointer = DeployPointer(Path("~/.tianshu/universes/deploy_ptr.json").expanduser())
    code_deployer = Deployer(deploy_pointer)
    universe_manager = UniverseManager(
        storage=storage,
        store=universe_store,
        persona_loader=persona_loader,
        skills_loader=skills,
        config_snapshot=_universe_config_snapshot,
        config_apply=_universe_config_apply,
        event_bus=event_bus,
        agent_config=lambda: config_manager.agent_config,
        code_store=code_variant_store,
        deployer=code_deployer,
    )
    # opt-in 持久化：env 开启，或库中已存在 champion 位面（此前已开启过）→ 续上开启状态，
    # 避免"重启后位面数据还在、功能却悄悄关闭"的困惑态。
    if config_manager.agent_config.parallel_universe_enabled or storage.get_champion_universe():
        config_manager.update_agent_config(parallel_universe_enabled=True)
        universe_manager.ensure_genesis()
    executor.set_universe_manager(universe_manager)
    app.state.universe_manager = universe_manager
    app.state.code_deployer = code_deployer

    _cfg = config_manager.agent_config
    code_gate = Gate(python_exe=sys.executable, timeout_s=_cfg.code_variant_sandbox_timeout_s)
    code_sandbox = SandboxRunner(mem_mb=_cfg.code_variant_sandbox_mem_mb)
    code_eval_harness = EvalHarness(
        storage, code_sandbox, fitness_weights=_cfg.universe_fitness_weights,
    )
    code_mutator = CodeMutator(
        provider_manager.get_client(), evolvable_paths=_cfg.code_variant_evolvable_paths,
    )

    from tianshu.universe.evolver import UniverseEvolver
    universe_evolver = UniverseEvolver(
        llm_client=provider_manager.get_client(),
        manager=universe_manager,
        storage=storage,
        config_manager=config_manager,
        code_store=code_variant_store,
        gate=code_gate,
        sandbox=code_sandbox,
        eval_harness=code_eval_harness,
        code_mutator=code_mutator,
    )
    universe_evolver.attach_event_bus(event_bus)
    app.state.universe_evolver = universe_evolver

    scheduler.register_system_jobs(
        profile_trigger, skill_curator=skill_curator, universe_evolver=universe_evolver,
    )

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
                archive_old_iterations(storage)
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
    try:
        await mcp_manager.shutdown()
    except Exception:
        logger.exception("[mcp] manager shutdown error")
    await bot_manager.stop_all()
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
    app.include_router(credentials_router, prefix="/api")
    app.include_router(hongluisi_router, prefix="/api")
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
