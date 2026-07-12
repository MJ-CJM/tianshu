"""Memory palace (DrawerStore) / PromptBuilder / MemoryManager 装配。

两个函数对应原 lifespan() 中不相邻的两段：

- `wire_memory_palace`：`# --- Memory Palace (Drawer Store) ---` 分区。
  该分区在原代码里紧接着构造 PromptBuilder（用到刚创建的 drawer_store/
  memory_config），拆分会把 PromptBuilder 的构造和它需要的 drawer_store
  拆到两个函数、两处 app.state 读写之间，反而引入不必要的穿线；
  按 task-12 设计约定 #3（分区边界严格对齐、分区内语句顺序不动），
  PromptBuilder 保留在本分区、本文件内——这是对目标结构草图的一处偏离
  （草图把 PromptBuilder 归在 wiring_persona.py），已在报告中记录。
- `wire_memory_manager`：`# --- MemoryManager ---` 分区，位置远在后面，
  此时 storage/config_manager/hook_registry/drawer_store/memory_config
  均已在 app.state，走 service-locator 即可。

跨区变量处置：`memory_dir` 原本只在 lifespan 顶部算一次
（`# --- Memory dir ---`），后被多个分区复用；它是 `Path(settings.memory_dir)
.expanduser()` 的纯计算、幂等无副作用，按设计约定 #2 优先选择——在每个
需要它的 wiring 函数内部重新推导，不再作为独立分区出现。`personas_dir`
同理（纯路径表达式），在这里也是重新推导，而不是等 Agent 分区把它塞进
app.state 之后再读。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer_store import DrawerStore
from tianshu.memory.manager import MemoryManager
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.resources.overlay import packaged_defaults
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.metrics import SkillMetricsStore


def _personas_dir() -> Path:
    return packaged_defaults().personas_dir()


def wire_memory_palace(
    app: FastAPI,
    settings: TianshuSettings,
    skills: SkillsLoader,
    metrics_store: SkillMetricsStore,
) -> PromptBuilder:
    """创建 DrawerStore + PromptBuilder。

    返回 prompt_builder：Agent 分区（wire_provider_and_agent）构造 Agent
    时需要这个实例，此时它还未写入 app.state，由 lifespan 显式传递。
    """
    storage = app.state.storage
    memory_dir = Path(settings.memory_dir).expanduser()
    personas_dir = _personas_dir()

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
        runtime_personas_dir=Path(settings.runtime_personas_dir).expanduser(),
    )
    return prompt_builder


def wire_memory_manager(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 MemoryManager 并确保记忆目录存在。"""
    storage = app.state.storage
    config_manager = app.state.config_manager
    hook_registry = app.state.hook_registry
    drawer_store = app.state.drawer_store
    memory_config = app.state.memory_config
    memory_dir = Path(settings.memory_dir).expanduser()
    personas_dir = _personas_dir()

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
