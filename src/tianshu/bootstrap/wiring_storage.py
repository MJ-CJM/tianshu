"""Storage / EventBus / HookRegistry 装配。

对应原 `tianshu.app.lifespan()` 中相邻的三个分区注释：
`# --- Storage ---` / `# --- EventBus ---` / `# --- HookRegistry ---`。
三者顺序相邻且无跨区变量穿线问题，合并为一个 wiring 函数。
"""

from __future__ import annotations

from fastapi import FastAPI

from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.kernel.hooks import HookRegistry
from tianshu.storage import Storage


def wire_storage(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 Storage / EventBus / HookRegistry 并挂载到 app.state。"""
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
