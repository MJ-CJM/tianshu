"""PersonaLoader / TemplateLibrary / selector / profile 装配。

三个函数对应原 lifespan() 中不相邻的三段：

- `wire_persona`：`# --- Persona ---` 分区。原分区里第一件事就是
  `register_memory_tools(...)`（需要 personas_dir，紧跟在 personas_dir
  定义之后），按 task-12 设计约定 #3（分区边界严格对齐、分区内语句顺序
  不动）整段保留在这里——这是对目标结构草图的一处偏离（草图把
  register_memory_tools 归在 wiring_memory.py），已在报告中记录。
- `wire_persona_quality`：`# --- PerformanceEvaluator ---` +
  `# --- OfficialSelector ---`，两个分区相邻，合并。
- `wire_profile`：`# --- ProfileSynthesizer + ProfileTrigger ---` 分区。

跨区变量处置：`memory_dir` / `personas_dir` 均为纯路径计算，按设计约定 #2
在每个需要的函数内部重新推导；`feishu_channel_cfg` 只在 wire_persona 内部
使用一次，后面 `# --- Scheduler ---` 附近的 schedule_edict 注册还会再读一次
`storage.get_channel_config("feishu")`——那是另一处，见 wiring_scheduler.py。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.kernel.hooks import HookType
from tianshu.persona.evaluator import PerformanceEvaluator
from tianshu.persona.loader import PersonaLoader
from tianshu.persona.profile_synthesizer import ProfileSynthesizer
from tianshu.persona.profile_trigger import ProfileTrigger
from tianshu.persona.selector import OfficialSelector
from tianshu.persona.template_library import TemplateLibrary
from tianshu.resources.overlay import packaged_defaults
from tianshu.tools.memory_tools import register_memory_tools
from tianshu.tools.persona_query import register_list_personas
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.submit_edict import register_submit_edict

logger = logging.getLogger(__name__)


def _personas_dir() -> Path:
    return packaged_defaults().personas_dir()


def wire_persona(app: FastAPI, settings: TianshuSettings, tools: ToolRegistry) -> None:
    """注册 memory 工具、加载 PersonaLoader/TemplateLibrary、敕令工具组开关。"""
    storage = app.state.storage
    event_bus = app.state.event_bus
    memory_dir = Path(settings.memory_dir).expanduser()

    # --- Persona ---
    personas_dir = _personas_dir()

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

    # 角色模板库（vendored agency-agents，见 tianshu/resources/persona_templates/）
    templates_dir = packaged_defaults().persona_templates_dir()
    template_library = TemplateLibrary(templates_dir)
    template_library.load()
    app.state.template_library = template_library

    # Re-register submit_edict tool now that persona_loader is available
    # （register_builtins 阶段 persona_loader 还未构造，此处覆盖以启用 persona 校验）
    register_submit_edict(
        tools,
        storage=storage,
        event_bus=event_bus,
        persona_loader=persona_loader,
    )

    # list_personas（T0 只读）—— 让 LLM 能查 DB 实际注册的官员名册。
    # 与 edict 工具组同 toggle —— 因为它的核心用途是支持"颁敕时按擅长领域指派"，
    # 跟 submit_edict 同生命周期，启用语义一致。
    register_list_personas(tools, storage=storage)

    # 默认禁用敕令管理工具集；通政司 toggle 打开后启用（reload 时同步）
    # submit_edict（写）+ list_edicts / get_edict_status / list_personas（读）
    # 作为同一捆绑能力 —— 助手能"颁敕"才需要"看官员名册帮决策指派"。
    edict_tools = (
        "submit_edict",
        "list_edicts",
        "get_edict_status",
        "list_personas",
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


def wire_persona_quality(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 PerformanceEvaluator + OfficialSelector。"""
    storage = app.state.storage
    persona_loader = app.state.persona_loader

    # --- PerformanceEvaluator ---
    evaluator = PerformanceEvaluator(storage=storage)
    app.state.evaluator = evaluator

    # --- OfficialSelector ---
    official_selector = OfficialSelector(persona_loader)
    app.state.official_selector = official_selector


def wire_profile(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 ProfileSynthesizer + ProfileTrigger，并挂 AGENT_END hook。"""
    provider_manager = app.state.provider_manager
    drawer_store = app.state.drawer_store
    storage = app.state.storage
    metrics_store = app.state.skill_metrics_store
    runtime_personas_dir = app.state.runtime_personas_dir
    persona_loader = app.state.persona_loader
    event_bus = app.state.event_bus
    hook_registry = app.state.hook_registry
    memory_dir = Path(settings.memory_dir).expanduser()
    personas_dir = _personas_dir()

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
