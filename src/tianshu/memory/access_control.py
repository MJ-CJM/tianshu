"""Memory access control — read/write permission checks + audit logging."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.memory.models import MemoryEntry

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class MemoryAccessPolicy:
    """Policy for memory access between personas."""

    def __init__(
        self,
        persona_id: str,
        can_read: list[str] | None = None,
        can_write: list[str] | None = None,
        share_level: str = "private",
    ) -> None:
        self.persona_id = persona_id
        self.can_read = can_read or []  # persona IDs this persona can read from
        self.can_write = can_write or []  # persona IDs this persona can write to
        self.share_level = share_level  # private | shared | court

    def allows_read(self, from_persona: str) -> bool:
        """Check if this persona can read from another."""
        if from_persona == self.persona_id:
            return True
        return from_persona in self.can_read or self.share_level in ("shared", "court")

    def allows_write(self, to_persona: str) -> bool:
        """Check if this persona can write to another."""
        if to_persona == self.persona_id:
            return True
        return to_persona in self.can_write


# Default policies: each persona can read shared/court entries from all others
DEFAULT_POLICIES: dict[str, MemoryAccessPolicy] = {
    "bingbu": MemoryAccessPolicy(
        "bingbu",
        can_read=["neige", "ducha", "wenyuan"],
        can_write=["wenyuan"],
    ),
    "neige": MemoryAccessPolicy(
        "neige",
        can_read=["bingbu", "ducha", "wenyuan"],
        can_write=["wenyuan"],
    ),
    "ducha": MemoryAccessPolicy(
        "ducha",
        can_read=["bingbu", "neige", "wenyuan"],
        can_write=["wenyuan"],
    ),
    "tongzheng": MemoryAccessPolicy(
        "tongzheng",
        can_read=["wenyuan"],
        can_write=[],
    ),
    "wenyuan": MemoryAccessPolicy(
        "wenyuan",
        can_read=["bingbu", "neige", "ducha", "tongzheng", "hubu"],
        can_write=["bingbu", "neige", "ducha"],
        share_level="court",
    ),
    "hubu": MemoryAccessPolicy(
        "hubu",
        can_read=["wenyuan"],
        can_write=["wenyuan"],
    ),
}


class MemoryAccessControl:
    """Enforces access control on memory operations."""

    def __init__(self, storage: Storage | None = None) -> None:
        self._storage = storage
        self._policies: dict[str, MemoryAccessPolicy] = dict(DEFAULT_POLICIES)

    def get_policy(self, persona_id: str) -> MemoryAccessPolicy:
        return self._policies.get(persona_id, MemoryAccessPolicy(persona_id))

    def set_policy(self, policy: MemoryAccessPolicy) -> None:
        self._policies[policy.persona_id] = policy

    def filter_readable(
        self,
        requestor: str,
        entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """Filter entries to only those readable by the requestor."""
        policy = self.get_policy(requestor)
        result: list[MemoryEntry] = []

        for entry in entries:
            # Own entries are always readable
            if entry.persona_id == requestor:
                result.append(entry)
                continue
            # Court-level entries are readable by all
            if entry.access_level == "court":
                result.append(entry)
                continue
            # Shared entries are readable if policy allows
            if entry.access_level == "shared" and policy.allows_read(entry.persona_id):
                result.append(entry)
                continue
            # Private entries from allowed personas
            if policy.allows_read(entry.persona_id):
                result.append(entry)

        return result

    def can_store(self, writer: str, entry: MemoryEntry) -> bool:
        """Check if writer can store an entry for the given persona."""
        if entry.persona_id == writer:
            return True
        policy = self.get_policy(writer)
        return policy.allows_write(entry.persona_id)
