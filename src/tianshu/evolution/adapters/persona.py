"""Persona candidate source validation."""

from collections.abc import Mapping
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.persona.model import AgentPersona


def _portable_path(value: str | None) -> str | None:
    if value is None:
        return None
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("persona paths must be portable relative paths")
    return value


class _PersonaSourceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    department: str
    title: str | None = None
    soul_path: str
    role_path: str
    memory_path: str
    skills_dir: str | None = None
    tools_allowed: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    skills_allowed: list[str] = Field(default_factory=list)
    tool_tier_max: int = 0
    can_delegate: bool = False
    memory_global_read: bool = False
    delegates_to: list[str] = Field(default_factory=list)
    llm_config_name: str | None = None

    _validate_paths = field_validator("soul_path", "role_path", "memory_path", "skills_dir")(
        _portable_path
    )


class PersonaCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.PERSONA

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            source = _PersonaSourceV1.model_validate(payload)
            AgentPersona.model_validate(source.model_dump())
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("persona source validation failed") from None
        return source.model_dump(mode="json")


__all__ = ["PersonaCandidateAdapter"]
