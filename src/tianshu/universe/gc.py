"""位面行为快照目录的磁盘回收（GC）。

只回收两类目录，且**只删磁盘、不动 DB 记录**（证据链不可变，与 memorial 语义一致）：

1. 孤儿目录：磁盘有目录、DB 无记录（历史泄漏或异常中断残留）
2. 超期归档：DB 状态为 ``archived``，且目录 mtime 超过保留期

冠军 / 挑战者位面、``worktrees/``、非 ULID 命名的目录一律不碰。

超期基准取目录 mtime 而非 DB 时间戳：表中只有 ``created_at``（无 ``archived_at``），
而 mtime 恰好表达"这份磁盘数据最后一次被写入是什么时候"，正是回收要问的问题。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 位面目录名是 26 字符 Crockford Base32 ULID；不匹配的目录（如 worktrees/）不是回收对象。
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_ARCHIVED = "archived"


class UniverseGC:
    """按保留策略回收位面快照目录。"""

    def __init__(
        self,
        storage: Any,
        store: Any,
        *,
        retention_days: int,
        event_bus: Any = None,
    ) -> None:
        self._storage = storage
        self._store = store
        self._retention_days = retention_days
        self._bus = event_bus

    async def run(self, *, trigger_source: str = "cron") -> dict:
        """执行一轮回收；磁盘操作放线程池，避免阻塞事件循环。"""
        result = await asyncio.to_thread(self._collect)
        if result["reclaimed"]:
            self._emit(result, trigger_source)
        logger.info(
            "Universe GC: reclaimed %d dir(s) (orphan=%d, expired=%d), kept %d",
            len(result["reclaimed"]),
            len(result["orphans"]),
            len(result["expired"]),
            result["kept"],
        )
        return result

    # --- internals ---

    def _collect(self) -> dict:
        root = self._store.root
        if not root.is_dir():
            return {"reclaimed": [], "orphans": [], "expired": [], "kept": 0, "failed": []}

        known = {u["id"]: u for u in self._storage.list_universes(include_archived=True)}
        cutoff = time.time() - self._retention_days * 86400

        orphans: list[str] = []
        expired: list[str] = []
        failed: list[str] = []
        kept = 0

        for entry in root.iterdir():
            if not entry.is_dir() or not _ULID_RE.match(entry.name):
                continue  # worktrees/ 与任何非位面目录

            record = known.get(entry.name)
            if record is None:
                orphans.append(entry.name)
            elif record["status"] == _ARCHIVED and self._mtime(entry) < cutoff:
                expired.append(entry.name)
            else:
                kept += 1

        reclaimed: list[str] = []
        for universe_id in [*orphans, *expired]:
            try:
                shutil.rmtree(root / universe_id)
            except OSError:
                logger.warning("Universe GC: failed to remove %s", universe_id, exc_info=True)
                failed.append(universe_id)
            else:
                reclaimed.append(universe_id)

        return {
            "reclaimed": reclaimed,
            "orphans": orphans,
            "expired": expired,
            "kept": kept,
            "failed": failed,
        }

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            # 读不到时间戳就当它刚被动过，留到下一轮再判，避免误删。
            return time.time()

    def _emit(self, result: dict, trigger_source: str) -> None:
        if not self._bus:
            return
        from tianshu.models.events import make_event

        self._bus.fire(
            make_event(
                event_type="universe.gc",
                edict_id=None,
                memorial_id=None,
                producer="universe_gc",
                payload={
                    "trigger_source": trigger_source,
                    "reclaimed": result["reclaimed"],
                    "orphan_count": len(result["orphans"]),
                    "expired_count": len(result["expired"]),
                    "retention_days": self._retention_days,
                },
            )
        )
