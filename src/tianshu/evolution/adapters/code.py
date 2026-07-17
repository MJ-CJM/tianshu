"""Code candidate source validation."""

from collections.abc import Mapping

from tianshu.evolution.adapters.base import BaseCandidateAdapter
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.workspace import CanonicalChangeSet


class CodeCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.CODE

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        CanonicalChangeSet.model_validate(payload)


__all__ = ["CodeCandidateAdapter"]
