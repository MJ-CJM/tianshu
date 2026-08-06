"""AgentPersona model — identity definition for each official."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_EXECUTOR_ID = "bingbu"
"""Default persona ID used as fallback when no specific persona is assigned."""


class AgentPersona(BaseModel):
    id: str  # "neige" | "bingbu" | "ducha" | "tongzheng"
    name: str
    department: str
    title: str | None = None  # 部门内职务（如 大学士、协理通政、参谋）；None 表示未指派
    soul_path: Path
    role_path: Path
    memory_path: Path
    skills_dir: Path | None = None
    tools_allowed: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    #: 允许访问的工作区外路径（绝对 glob，如 ``/data/shared/**``）。事前授权，
    #: 由 WorkspaceBoundaryRule 消费；相对 glob 无效（工作区内本就可访问）。
    #: 越界访问不走审批——审批按钮本身是诱导攻击面，见 issue #32。
    allowed_paths: list[str] = Field(default_factory=list)
    skills_allowed: list[str] = Field(default_factory=list)
    tool_tier_max: int = 0
    can_delegate: bool = False
    memory_global_read: bool = False  # 高权限：绕过记忆访问控制，可读所有 persona 的记忆
    delegates_to: list[str] = Field(default_factory=list)
    llm_config_name: str | None = None  # references llm_configs.name; None = use global
