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
    historian: object | None = None,
    diarist: object | None = None,
    evolution_unlock: object | None = None,
    xunan: object | None = None,
) -> None:
    """Run daily digest at roughly every 24h; 顺带跑后台史官 + 起居注蒸馏(迭代 4)。"""
    day = 0
    while True:
        try:
            await asyncio.sleep(86400)  # 24 hours
            day += 1
            digest = digest_generator.generate_daily()
            await notifier.broadcast_ws(digest)
            # Dispatch to all registered external channels
            await channel_registry.send_all(digest, str(digest))
            archive_old_iterations(storage)
            # 后台史官 + 起居注:蒸馏执行知识 / 用户偏好(零对话 token)
            if historian is not None:
                await historian.distill_recent()  # type: ignore[attr-defined]
            if diarist is not None:
                await diarist.synthesize()  # type: ignore[attr-defined]
            # 自进化请旨解锁:行为层达阈值主动上奏折(迭代 6,ADR-0004)
            if evolution_unlock is not None:
                await evolution_unlock.check_and_petition()  # type: ignore[attr-defined]
            # 实录馆·自动汇编(迭代 7):每 7 个日循环推一次《实录》周报
            if day % 7 == 0:
                weekly = digest_generator.generate_weekly()
                await notifier.broadcast_ws(weekly)
                await channel_registry.send_all(weekly, weekly.get("narrative", str(weekly)))
            # 巡按御史·主动巡检(迭代 7):异常则主动上奏(健康则不打扰)
            if xunan is not None:
                report = xunan.patrol()  # type: ignore[attr-defined]
                if not report.get("healthy", True):
                    msg = {"type": "xunan.report", "title": "巡按奏报", **report}
                    await notifier.broadcast_ws(msg)
                    await channel_registry.send_all(
                        msg, f"巡按奏报:{len(report['findings'])} 项异常"
                    )
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

    # --- 后台史官 + 起居注(迭代 4)——与摘要 cron 共用周期 ---
    from tianshu.memory.diarist import Diarist
    from tianshu.memory.historian import Historian
    from tianshu.memory.kg import KnowledgeGraph

    historian = Historian(storage, app.state.config_manager)
    app.state.historian = historian
    kg = KnowledgeGraph(storage)
    app.state.knowledge_graph = kg
    diarist = Diarist(storage, app.state.config_manager, kg)
    app.state.diarist = diarist

    # --- 自进化请旨解锁(迭代 6,ADR-0004)——同周期检查行为层是否达阈值上奏折 ---
    from tianshu.universe.unlock import EvolutionUnlock

    evolution_unlock = EvolutionUnlock(
        storage,
        app.state.config_manager,
        notifier=notifier,
        bus=getattr(app.state, "event_bus", None),
    )
    app.state.evolution_unlock = evolution_unlock

    # --- 巡按御史(迭代 7)——随 digest cron 周期巡检系统健康 ---
    from tianshu.executor.xunan import Xunan

    xunan = Xunan(storage)
    app.state.xunan = xunan

    # Schedule daily digest via cron loop
    digest_task = asyncio.create_task(
        _digest_cron_loop(
            digest_generator,
            notifier,
            channel_registry,
            storage,
            historian,
            diarist,
            evolution_unlock,
            xunan,
        )
    )
    app.state._digest_task = digest_task
