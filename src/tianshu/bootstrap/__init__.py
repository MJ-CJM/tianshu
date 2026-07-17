"""Tianshu 应用装配（wiring）包。

`tianshu.app.lifespan()` 原本是一个 ~700 行的"上帝组合根"：按
`# --- X ---` 注释分区顺序，把 20+ 个子系统对象逐一创建并挂到
`app.state` 上。本包把每个分区（或若干相邻分区）拆成一个
`wire_xxx(app, settings) -> None` 函数（需要内部 await/create_task 的用
`async def`），lifespan() 只负责按【原分区顺序】依次调用这些函数——
调用顺序不可调整，很多子系统之间存在隐式的装配顺序依赖（例如
skill 工具注册必须在 config_manager 创建之后）。

依赖传递沿用现有 service-locator 模式：前面的 wiring 函数把对象放进
`app.state`，后面的 wiring 函数从 `app.state` 取用。少数尚未写入
`app.state` 就被后续 wiring 需要的有状态对象（`tools` / `skills` /
`metrics_store` / `prompt_builder`）由 lifespan 显式按参数传递，
详见各 wiring 函数的 docstring 与 task-12 报告里的"跨区变量处置表"。

6 个原 lifespan 内嵌闭包已提为顶层函数：
`_mcp_bg_start`（wiring_tools）、`_assistant_persona_id_provider`
（wiring_llm）、`_update_universe_fitness` / `_universe_config_snapshot`
/ `_universe_config_apply`（universe_hooks）、`_digest_cron_loop`
（digest_cron）。
"""

from __future__ import annotations

from tianshu.bootstrap.digest_cron import wire_digest
from tianshu.bootstrap.wiring_channels import wire_channel_bots, wire_channels
from tianshu.bootstrap.wiring_executor import (
    wire_executor,
    wire_hook_registrations,
    wire_policy,
    wire_worker_lane,
)
from tianshu.bootstrap.wiring_llm import wire_cost_manager, wire_llm_config, wire_provider_and_agent
from tianshu.bootstrap.wiring_memory import wire_memory_manager, wire_memory_palace
from tianshu.bootstrap.wiring_outbox import wire_outbox
from tianshu.bootstrap.wiring_persona import wire_persona, wire_persona_quality, wire_profile
from tianshu.bootstrap.wiring_scheduler import (
    wire_auditor,
    wire_consultation,
    wire_plugins,
    wire_scheduling,
)
from tianshu.bootstrap.wiring_skills import (
    wire_evolution_services,
    wire_skill_curator,
    wire_skill_tools,
    wire_skills,
    wire_skills_watcher,
)
from tianshu.bootstrap.wiring_storage import wire_storage
from tianshu.bootstrap.wiring_tools import wire_tools
from tianshu.bootstrap.wiring_universe import wire_universe

__all__ = [
    "wire_auditor",
    "wire_channel_bots",
    "wire_channels",
    "wire_consultation",
    "wire_cost_manager",
    "wire_digest",
    "wire_executor",
    "wire_evolution_services",
    "wire_hook_registrations",
    "wire_llm_config",
    "wire_memory_manager",
    "wire_memory_palace",
    "wire_outbox",
    "wire_persona",
    "wire_persona_quality",
    "wire_plugins",
    "wire_policy",
    "wire_profile",
    "wire_provider_and_agent",
    "wire_scheduling",
    "wire_skill_curator",
    "wire_skill_tools",
    "wire_skills",
    "wire_skills_watcher",
    "wire_storage",
    "wire_tools",
    "wire_universe",
    "wire_worker_lane",
]
