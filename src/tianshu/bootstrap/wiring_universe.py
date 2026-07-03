"""UniverseManager / evolver / deployer / code_store 装配。

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
import sys
from pathlib import Path

from fastapi import FastAPI

from tianshu.bootstrap.universe_hooks import _universe_config_apply, _universe_config_snapshot
from tianshu.config import TianshuSettings
from tianshu.universe.code_mutator import CodeMutator
from tianshu.universe.code_store import CodeVariantStore
from tianshu.universe.deployer import Deployer, DeployPointer
from tianshu.universe.diagnostician import Diagnostician
from tianshu.universe.eval_harness import EvalHarness
from tianshu.universe.evolver import UniverseEvolver
from tianshu.universe.gate import Gate
from tianshu.universe.manager import UniverseManager
from tianshu.universe.sandbox import SandboxRunner
from tianshu.universe.store import UniverseStore


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
        repo_root=Path(__file__).resolve().parents[3],
        worktrees_root=Path("~/.tianshu/universes/worktrees").expanduser(),
    )
    deploy_pointer = DeployPointer(Path("~/.tianshu/universes/deploy_ptr.json").expanduser())
    code_deployer = Deployer(deploy_pointer)
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
        storage,
        code_sandbox,
        fitness_weights=_cfg.universe_fitness_weights,
    )
    code_mutator = CodeMutator(
        provider_manager.get_client(),
        evolvable_paths=_cfg.code_variant_evolvable_paths,
    )
    diagnostician = Diagnostician(
        provider_manager.get_client(),
        storage,
        evolvable_paths=_cfg.code_variant_evolvable_paths,
    )

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
    )
    universe_evolver.attach_event_bus(event_bus)
    app.state.universe_evolver = universe_evolver

    scheduler.register_system_jobs(
        profile_trigger,
        skill_curator=skill_curator,
        universe_evolver=universe_evolver,
    )
