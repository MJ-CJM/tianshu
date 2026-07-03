"""每日摘要（digest）：DigestGenerator 装配 + cron 循环。

`_digest_cron_loop` 原为 lifespan() 内嵌闭包，捕获 digest_generator /
notifier / channel_registry / storage。按 task-12 设计约定 #4 提为顶层
函数，捕获对象改为显式参数；`asyncio.create_task(...)` 处直接调用它拿到
协程对象再传入，用法等价于原来直接调用闭包。

`wire_digest` 对应原 `# --- DigestGenerator ---` 分区（含紧随其后、没有
独立分区注释的 cron 任务创建代码）。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.executor.orchestrator.archive import archive_old_iterations
from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.digest import DigestGenerator
from tianshu.notifier.notifier import Notifier
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


async def _digest_cron_loop(
    digest_generator: DigestGenerator,
    notifier: Notifier,
    channel_registry: ChannelRegistry,
    storage: Storage,
) -> None:
    """Run daily digest at roughly every 24h."""
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


def wire_digest(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 DigestGenerator，并启动每日摘要 cron 循环任务。"""
    storage = app.state.storage
    notifier = app.state.notifier
    channel_registry = app.state.channel_registry

    # --- DigestGenerator ---
    digest_generator = DigestGenerator(storage=storage)
    app.state.digest_generator = digest_generator

    # Schedule daily digest via cron loop
    digest_task = asyncio.create_task(
        _digest_cron_loop(digest_generator, notifier, channel_registry, storage)
    )
    app.state._digest_task = digest_task
