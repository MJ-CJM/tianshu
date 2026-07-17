"""Memory candidate source validation."""

from collections.abc import Mapping

from tianshu.evolution.adapters.base import BaseCandidateAdapter
from tianshu.memory.models import MemoryEntry
from tianshu.memory.safety import validate_content
from tianshu.models.evolution_candidate import CandidateKind


class MemoryCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.MEMORY

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        entry = MemoryEntry.model_validate(payload)
        validate_content(entry.content)


__all__ = ["MemoryCandidateAdapter"]
