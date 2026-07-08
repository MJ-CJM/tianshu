"""FeatureFlags —— 灰度开关求值(迭代 6「演化 2.0」,自研不接 OpenFeature)。

约 50 行求值(spec P2-H):布尔开关 + 按 subject 哈希的百分比灰度。**deny-by-default**
(flag 不存在或未启用一律 False)。接口刻意留 OpenFeature Provider 形状(is_enabled),
留迁移后路。用途:已过门禁未全量的进化产物挂 flag,按 cohort 灰度、秒级回退。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.storage import Storage


def _bucket(key: str, subject: str) -> int:
    """稳定的 0–99 分桶:同 (key, subject) 恒定,不同 flag 分布独立(key 混入哈希)。"""
    digest = hashlib.sha256(f"{key}:{subject}".encode()).hexdigest()
    return int(digest, 16) % 100


class FeatureFlags:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def is_enabled(self, key: str, subject: str | None = None) -> bool:
        """flag 是否对 subject 生效。deny-by-default;rollout_pct 按哈希灰度。"""
        row = self._storage.get_flag(key)
        if not row or not row["enabled"]:
            return False
        pct = int(row["rollout_pct"])
        if pct >= 100:
            return True
        if pct <= 0:
            return False
        return _bucket(key, subject or "") < pct

    def set(
        self,
        key: str,
        *,
        enabled: bool,
        rollout_pct: int = 100,
        description: str | None = None,
    ) -> None:
        rollout_pct = max(0, min(100, int(rollout_pct)))
        self._storage.set_flag(
            key,
            enabled=enabled,
            rollout_pct=rollout_pct,
            description=description,
            updated_at=datetime.now(UTC).isoformat(),
        )

    def list_all(self) -> list[dict]:
        return self._storage.list_flags()

    def delete(self, key: str) -> None:
        self._storage.delete_flag(key)
