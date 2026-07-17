"""Persona candidate source validation."""

from collections.abc import Mapping

from tianshu.evolution.adapters.base import BaseCandidateAdapter
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.persona.model import AgentPersona


class PersonaCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.PERSONA

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        AgentPersona.model_validate(payload)


__all__ = ["PersonaCandidateAdapter"]
