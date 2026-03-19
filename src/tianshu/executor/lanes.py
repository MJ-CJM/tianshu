"""Lane-based concurrency control — session and global back-pressure."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SessionLane:
    """Per-edict concurrency limiter."""

    def __init__(self, max_concurrency: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max = max_concurrency

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def available(self) -> int:
        return self._semaphore._value

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()


class GlobalLane:
    """System-wide concurrency limiter (back-pressure)."""

    def __init__(self, max_concurrency: int = 8) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max = max_concurrency

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def available(self) -> int:
        return self._semaphore._value

    @property
    def active(self) -> int:
        return self._max - self._semaphore._value

    async def acquire(self) -> None:
        await self._semaphore.acquire()

    def release(self) -> None:
        self._semaphore.release()

    def status(self) -> dict:
        return {
            "max_concurrency": self._max,
            "active": self.active,
            "available": self.available,
        }


class LaneManager:
    """Manages session-level and global-level lanes."""

    def __init__(self, max_global_concurrency: int = 8) -> None:
        self._global = GlobalLane(max_global_concurrency)
        self._sessions: dict[str, SessionLane] = {}

    @property
    def global_lane(self) -> GlobalLane:
        return self._global

    def get_session_lane(self, edict_id: str, max_concurrency: int = 1) -> SessionLane:
        """Get or create a session lane for an edict."""
        if edict_id not in self._sessions:
            self._sessions[edict_id] = SessionLane(max_concurrency)
        return self._sessions[edict_id]

    def remove_session(self, edict_id: str) -> None:
        self._sessions.pop(edict_id, None)

    def status(self) -> dict:
        return {
            "global": self._global.status(),
            "sessions": {
                eid: {"max": lane.max_concurrency, "available": lane.available}
                for eid, lane in self._sessions.items()
            },
        }
