"""AgentPersona model — identity definition for each official."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class AgentPersona(BaseModel):
    id: str  # "neige" | "bingbu" | "ducha" | "tongzheng"
    name: str
    department: str
    soul_path: Path
    role_path: Path
    memory_path: Path
    skills_dir: Path | None = None
    tools_allowed: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    tool_tier_max: int = 0
    can_delegate: bool = False
    delegates_to: list[str] = Field(default_factory=list)
