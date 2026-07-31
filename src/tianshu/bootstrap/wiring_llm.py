"""ConfigManager / ProviderManager / Agent / CostManager 装配。

三个函数对应原 lifespan() 中的三段代码：

- `wire_llm_config`：`# --- LLM Config Manager ---` 分区。
- `wire_provider_and_agent`：`# --- ProviderManager ---` +
  `# --- Agent ---` 两个相邻分区，合并。
- `wire_cost_manager`：`# --- CostManager（提前创建）---` 分区，
  位置在 ApprovalManager 之后、Channel bots 之前，离前两个较远，
  但内容仍属"LLM/cost 基础设施"一类，按目标结构放在同一文件、
  作为独立函数在其原有位置调用。

跨区变量处置：`tools` / `skills` / `metrics_store` / `prompt_builder`
均由更早的 wiring 函数创建、此时尚未写入 app.state，由 lifespan 显式
传参（见各自 wiring 文件的说明）。`personas_dir` 为纯路径计算，
按设计约定 #2 在本文件内重新推导，而不是等这里把它写进 app.state
之后再读回来。
"""

from __future__ import annotations

import functools
import logging
import os

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.cost.manager import CostManager
from tianshu.executor.agent import Agent
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.providers.demo import demo_llm_config_state
from tianshu.providers.manager import ProviderManager
from tianshu.resources.overlay import packaged_defaults
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.metrics import SkillMetricsStore
from tianshu.storage import Storage
from tianshu.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _assistant_persona_id_provider(storage: Storage) -> str | None:
    """实时读 channel_configs.feishu.assistant_persona_id。

    原 lifespan 内嵌闭包提为顶层，捕获对象（storage）改为显式参数，
    调用处用 functools.partial 绑定。agent 用它判断当前执行 persona
    是不是助手，非助手过滤掉 ASSISTANT_ONLY_TOOLS（submit_edict 等），
    防 cron 触发的执行 persona 递归颁敕。
    """
    cfg = storage.get_channel_config("feishu")
    return (cfg or {}).get("assistant_persona_id")


def wire_llm_config(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 模型注册表（catalog + registry）与 ConfigManager（LLM 配置 + agent 配置）。"""
    from pathlib import Path

    from tianshu.cost.tracker import set_pricing_resolver
    from tianshu.providers.model_catalog import ModelCatalog
    from tianshu.providers.registry import ModelProviderRegistry
    from tianshu.secrets.store import CredentialStore
    from tianshu.secrets.vault import get_vault

    storage = app.state.storage

    # --- 模型注册表（provider 一等实体 + models.dev 目录）---
    cache_path = Path(settings.db_path).expanduser().parent / "cache" / "models_catalog.json"
    catalog = ModelCatalog(cache_path=cache_path, usd_cny_rate=settings.llm_usd_cny_rate)
    vault = get_vault()
    credential_store = CredentialStore(storage, vault) if vault is not None else None
    model_registry = ModelProviderRegistry(storage, catalog, credential_store)
    app.state.model_catalog = catalog
    app.state.model_registry = model_registry
    # 成本核算的默认价换到目录快照（含配置汇率与磁盘缓存）
    set_pricing_resolver(catalog.pricing_cny_by_model)

    # --- LLM Config Manager ---
    # env 种子转译（仅库空时生效一次）：vault 在场 → key 加密入凭证库（重启后
    # 与 env 无关，doctor/readiness 判定一致）；vault 缺失 → 退为 $ENV 引用
    # （pydantic 只读 .env 不导出到进程 env，补一次 setdefault 保证可解析）。
    seed_api_key = ""
    if settings.llm_api_key:
        if vault is not None:
            seed_api_key = settings.llm_api_key
        else:
            os.environ.setdefault("TIANSHU_LLM_API_KEY", settings.llm_api_key)
            seed_api_key = "$ENV:TIANSHU_LLM_API_KEY"
    initial_state = LLMConfigState(
        name=settings.llm_model,
        model=settings.llm_model,
        api_key=seed_api_key,
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
    runtime_override = None
    if settings.startup_profile == "demo":
        # runtime-only 掩蔽：解析路径只见 demo，llm_configs/providers 表零改动，
        # 退出 demo 后 live 配置原样恢复。
        runtime_override = demo_llm_config_state()
        logger.info("[demo] startup_profile=demo：LLM 解析已切换到零网络确定性档位")
    config_manager = ConfigManager(
        initial_state,
        agent_config=agent_config,
        storage=storage,
        runtime_override=runtime_override,
        model_registry=model_registry,
    )
    app.state.config_manager = config_manager


def wire_provider_and_agent(
    app: FastAPI,
    settings: TianshuSettings,
    tools: ToolRegistry,
    skills: SkillsLoader,
    metrics_store: SkillMetricsStore,
    prompt_builder: PromptBuilder,
) -> None:
    """创建 ProviderManager + Agent，并把它们及关联对象挂到 app.state。"""
    storage = app.state.storage
    config_manager = app.state.config_manager
    hook_registry = app.state.hook_registry
    personas_dir = packaged_defaults().personas_dir()

    # --- ProviderManager ---
    demo_mode = settings.startup_profile == "demo"
    provider_manager = ProviderManager(
        storage=storage,
        config_manager=config_manager,
        demo_mode=demo_mode,
        model_registry=getattr(app.state, "model_registry", None),
    )
    if not demo_mode:
        # demo 档位绝不把 runtime 状态同步/删除到 providers 表
        provider_manager.sync_all()
    app.state.provider_manager = provider_manager

    # --- Agent ---
    # 实时读 channel_configs.feishu.assistant_persona_id —— agent 用它判断
    # 当前执行 persona 是不是助手，非助手过滤掉 ASSISTANT_ONLY_TOOLS
    # （submit_edict 等），防 cron 触发的执行 persona 递归颁敕。
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=skills,
        hook_registry=hook_registry,
        prompt_builder=prompt_builder,
        provider_manager=provider_manager,
        metrics_store=metrics_store,
        assistant_persona_id_provider=functools.partial(_assistant_persona_id_provider, storage),
    )
    app.state.agent = agent
    app.state.skills_loader = skills
    app.state.skill_metrics_store = metrics_store
    app.state.tool_registry = tools
    app.state.prompt_builder = prompt_builder
    app.state.personas_dir = personas_dir


def wire_cost_manager(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 CostManager（提前创建：FeishuBot /budget 卡片需要）。"""
    storage = app.state.storage
    event_bus = app.state.event_bus

    # --- CostManager（提前创建：FeishuBot /budget 卡片需要）---
    cost_manager = CostManager(storage=storage, event_bus=event_bus)
    app.state.cost_manager = cost_manager
    event_bus.on(
        "execution.cancelled",
        cost_manager.handle_execution_cancelled,
        consumer_name="cost.execution_cancelled.v1",
        priority=150,
    )
    from tianshu.llm import set_usage_observer

    set_usage_observer(cost_manager.observe_llm_usage)

    # --- 出厂预算护栏(迭代 3,放手四保险第④条)---
    # 首次启动若无 global 预算,落出厂默认每日上限;已有则尊重用户设置不覆盖。
    guardrail = settings.daily_budget_guardrail_cny
    if guardrail > 0 and storage.get_budget("global") is None:
        cost_manager.set_budget("global", guardrail, period="daily")
        logger.info("[budget] 出厂预算护栏已就绪:每日全局上限 ¥%.2f", guardrail)
