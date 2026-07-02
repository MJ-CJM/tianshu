"""4-layer memory stack: L0 identity → L1 critical facts → L2 recall → L3 deep search."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import DrawerResult

if TYPE_CHECKING:
    from tianshu.memory.drawer import MemoryBackend

logger = logging.getLogger(__name__)


class MemoryStack:
    """Orchestrates the 4-layer memory retrieval stack."""

    def __init__(self, store: MemoryBackend, config: MemoryConfig) -> None:
        self._store = store
        self._config = config

    async def get_l1(self, wing: str) -> str:
        """L1: Critical facts — always loaded into prompt."""
        if not self._config.enabled or not self._config.l1_enabled:
            return ""
        return await self._store.get_l1(wing, max_chars=self._config.l1_max_chars)

    async def recall(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        include_court: bool = False,
    ) -> list[DrawerResult]:
        """L2: On-demand recall — filtered search before execution."""
        if not self._config.enabled or not self._config.l2_recall_enabled:
            return []

        results = await self._store.search(
            query,
            wing=wing,
            room=room,
            n_results=self._config.l2_n_results,
        )

        if include_court and wing != "court":
            court_results = await self._store.search(
                query,
                wing="court",
                n_results=self._config.l2_n_results,
            )
            results = self._merge_results(results, court_results)

        return results

    async def deep_search(
        self,
        query: str,
        n_results: int = 20,
    ) -> list[DrawerResult]:
        """L3: Deep search — full palace, no wing filter."""
        if not self._config.enabled:
            return []
        return await self._store.search(query, n_results=n_results)

    @staticmethod
    def _merge_results(
        primary: list[DrawerResult],
        secondary: list[DrawerResult],
    ) -> list[DrawerResult]:
        """Merge two result lists, deduplicate by drawer_id, sort by score."""
        seen: set[str] = set()
        merged: list[DrawerResult] = []
        for r in primary + secondary:
            if r.drawer_id not in seen:
                seen.add(r.drawer_id)
                merged.append(r)
        merged.sort(key=lambda r: -r.score)
        return merged
