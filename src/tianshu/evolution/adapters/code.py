"""Code candidate source validation."""

from collections.abc import Mapping

from pydantic import ValidationError

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.workspace import CanonicalChangeSet


class CodeCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.CODE

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            change_set = CanonicalChangeSet.model_validate(payload)
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("code source validation failed") from None
        return change_set.model_dump(mode="json")


__all__ = ["CodeCandidateAdapter"]
