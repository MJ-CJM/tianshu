"""30 天归档任务 —— 清空 actor_output，保留 checks/critic 摘要。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def archive_old_iterations(storage: Storage, retention_days: int = 30) -> int:
    """归档 finished_at 早于 retention_days 的 iteration。返回归档行数。"""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    ids = storage.list_iterations_to_archive(cutoff)
    now = datetime.now(UTC).isoformat()
    for iter_id in ids:
        storage.archive_iteration(iter_id, now)
    if ids:
        logger.info("归档 %d 条 outer_loop_iterations（cutoff=%s）", len(ids), cutoff)
    return len(ids)
