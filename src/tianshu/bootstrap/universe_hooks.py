"""平行位面（parallel universe）相关的 3 个顶层闭包。

原 lifespan() 里以嵌套闭包形式定义、捕获 config_manager / storage 等外层
变量。按 task-12 设计约定 #4 提为顶层函数，捕获对象改为显式参数：

- `_update_universe_fitness`：注册给 `event_bus.on(...)` 的事件处理器。
  `tianshu.bus.event_bus.EventBus.emit/_run_handlers` 在 handler 抛异常时
  会访问 `handler.__qualname__` 记日志，而 `functools.partial` 实例没有
  `__qualname__`；调用处（wiring_scheduler.py）因此用一层具名闭包转发，
  不直接把 `functools.partial` 对象注册给 event_bus。
- `_universe_config_snapshot` / `_universe_config_apply`：作为
  `UniverseManager(config_snapshot=..., config_apply=...)` 的构造参数，
  UniverseManager 内部只是普通函数调用（如 `self._config_snapshot()`），
  用 `functools.partial` 绑定即可，无此顾虑。
"""

from __future__ import annotations

from dataclasses import asdict

from tianshu.config_manager import ConfigManager
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.universe.fitness import compute_fitness


def _universe_config_snapshot(config_manager: ConfigManager) -> dict:
    return {"agent_config": asdict(config_manager.agent_config)}


def _universe_config_apply(config_manager: ConfigManager, manifest: dict) -> None:
    ac = manifest.get("agent_config") or {}
    if ac:
        config_manager.update_agent_config(
            **{
                k: ac[k]
                for k in ("agent_max_iterations", "agent_timeout_seconds", "skills_char_budget")
                if k in ac
            }
        )


async def _update_universe_fitness(
    event: EventEnvelope, *, config_manager: ConfigManager, storage: Storage
) -> None:
    """平行位面：memorial 完成 → 重算其所属位面的适应度。"""
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
