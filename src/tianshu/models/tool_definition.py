"""Tool contribution metadata shared by plugins and the execution registry."""

from pydantic import BaseModel

from tianshu.models.side_effect import SideEffectSemantics


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict
    tier: int = 0
    max_result_chars: int = 8000
    side_effect: bool = False
    managed_effect_semantics: SideEffectSemantics | None = None


__all__ = ["ToolDefinition"]
