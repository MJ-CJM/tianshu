"""UniverseManager / evolver / code_store 装配。

对应原 lifespan() 的 `# --- 平行位面（parallel universe）---` 单个分区
（原文件里最大的一段连续代码）。

闭包处置：`_universe_config_snapshot` / `_universe_config_apply` 按
task-12 设计约定 #4 提到 `bootstrap/universe_hooks.py`，这里用
`functools.partial` 绑定 config_manager（UniverseManager 内部只是普通
函数调用，没有 EventBus 那种依赖 `__qualname__` 的场景，可以直接用
partial）。`agent_config=lambda: config_manager.agent_config` 不在
task-12 列出的 6 个待提升闭包之列，原样保留为行内 lambda。
"""

from __future__ import annotations

import functools
import logging
import sys
from pathlib import Path

from fastapi import FastAPI

from tianshu.bootstrap.universe_hooks import _universe_config_apply, _universe_config_snapshot
from tianshu.config import TianshuSettings
from tianshu.universe.code_mutator import CodeMutator
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.diagnostician import Diagnostician
from tianshu.universe.eval_harness import EvalHarness
from tianshu.universe.evolver import UniverseEvolver
from tianshu.universe.execution import UniverseExecutionContextFactory
from tianshu.universe.gate import Gate
from tianshu.universe.manager import UniverseManager
from tianshu.universe.sandbox import SandboxRunner
from tianshu.universe.store import UniverseStore

logger = logging.getLogger(__name__)


def _universe_repo_root(settings: TianshuSettings) -> Path:
    """自进化代码变体的源仓库根。wheel 部署必须显式配置；开发模式回退源码树根。"""
    if settings.universe_repo_root:
        return Path(settings.universe_repo_root).expanduser()
    inferred = Path(__file__).resolve().parents[3]
    if not (inferred / ".git").exists():
        logger.warning(
            "universe_repo_root 未配置且推断路径 %s 不是 git 仓库；"
            "wheel 部署下请设置 TIANSHU_UNIVERSE_REPO_ROOT",
            inferred,
        )
    return inferred


def wire_universe(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 UniverseManager + code 变体沙箱/评估/变异 + UniverseEvolver。"""
    storage = app.state.storage
    event_bus = app.state.event_bus
    persona_loader = app.state.persona_loader
    skills = app.state.skills_loader
    config_manager = app.state.config_manager
    executor = app.state.executor
    provider_manager = app.state.provider_manager
    scheduler = app.state.scheduler
    profile_trigger = app.state.profile_trigger
    skill_curator = app.state.skill_curator

    # --- 平行位面（parallel universe）---
    universe_store = UniverseStore(
        root=Path("~/.tianshu/universes").expanduser(),
        live_personas_dir=persona_loader.runtime_dir,
        live_skills_dir=skills.user_dir,
    )
    code_variant_store = CodeVariantStore(
        repo_root=_universe_repo_root(settings),
        worktrees_root=Path("~/.tianshu/universes/worktrees").expanduser(),
    )
    universe_manager = UniverseManager(
        storage=storage,
        store=universe_store,
        persona_loader=persona_loader,
        skills_loader=skills,
        config_snapshot=functools.partial(_universe_config_snapshot, config_manager),
        config_apply=functools.partial(_universe_config_apply, config_manager),
        event_bus=event_bus,
        agent_config=lambda: config_manager.agent_config,
        code_store=code_variant_store,
        challenger_router=app.state.challenger_router,
    )
    # opt-in 持久化：env 开启，或库中已存在 champion 位面（此前已开启过）→ 续上开启状态，
    # 避免"重启后位面数据还在、功能却悄悄关闭"的困惑态。
    if config_manager.agent_config.parallel_universe_enabled or storage.get_champion_universe():
        config_manager.update_agent_config(parallel_universe_enabled=True)
        universe_manager.ensure_genesis()
    executor.set_universe_manager(universe_manager)
    app.state.universe_manager = universe_manager

    _cfg = config_manager.agent_config
    universe_execution_context_factory = UniverseExecutionContextFactory(
        security_mode=settings.security_mode
    )
    code_gate = Gate(
        app.state.execution_gateway,
        context_factory=universe_execution_context_factory,
        python_exe=sys.executable,
        timeout_s=_cfg.code_variant_sandbox_timeout_s,
    )
    code_sandbox = SandboxRunner(
        app.state.execution_gateway,
        context_factory=universe_execution_context_factory,
        runtime_timeout_s=_cfg.code_variant_sandbox_timeout_s,
    )
    app.state.universe_execution_context_factory = universe_execution_context_factory
    app.state.code_gate = code_gate
    app.state.code_sandbox = code_sandbox
    eval_base_env: dict[str, str] = {}
    if settings.eval_llm_api_key:
        eval_base_env["TIANSHU_LLM_API_KEY"] = "${settings:eval_llm_api_key}"
    elif settings.llm_api_key:
        eval_base_env["TIANSHU_LLM_API_KEY"] = "${settings:llm_api_key}"
    api_base = settings.eval_llm_api_base or settings.llm_api_base
    model = settings.eval_llm_model or settings.llm_model
    if api_base:
        eval_base_env["TIANSHU_LLM_API_BASE"] = api_base
    if model:
        eval_base_env["TIANSHU_LLM_MODEL"] = model
    code_eval_harness = EvalHarness(
        storage,
        code_sandbox,
        fitness_weights=_cfg.universe_fitness_weights,
        base_env=eval_base_env,
    )
    # 修撰效果门(迭代 6,ADR-0007)复用同一评估器 + 变体仓根 —— curator 惰性取用
    app.state.code_eval_harness = code_eval_harness
    app.state.code_variant_store = code_variant_store
    code_mutator = CodeMutator(
        provider_manager.get_client(),
        evolvable_paths=_cfg.code_variant_evolvable_paths,
    )
    diagnostician = Diagnostician(
        provider_manager.get_client(),
        storage,
        evolvable_paths=_cfg.code_variant_evolvable_paths,
    )
    app.state.diagnostician = diagnostician  # 太医奏折(迭代 7)出口用

    # feature-flag 灰度(迭代 6):自研 SQLite flag 表,晋升灰度旋钮 / 秒级回退的控制面
    from tianshu.feature_flags import FeatureFlags

    feature_flags = FeatureFlags(storage)
    app.state.feature_flags = feature_flags

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
        diagnostician=diagnostician,
        feature_flags=feature_flags,
        consultation=getattr(app.state, "consultation", None),
        # 画像驱动进化(迭代 6):延迟读取起居注画像——diarist 在 wire_digest 才装配
        profile_provider=lambda: (
            app.state.diarist.read_profile() if getattr(app.state, "diarist", None) else ""
        ),
    )
    universe_evolver.attach_event_bus(event_bus)
    app.state.universe_evolver = universe_evolver

    scheduler.register_system_jobs(
        profile_trigger,
        skill_curator=skill_curator,
        universe_evolver=universe_evolver,
    )
