"""Plugin manifest model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PluginManifest(BaseModel):
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    type: Literal["tool", "hook", "channel", "provider", "skill", "command"] = "tool"
    entry_point: str = ""
    dependencies: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    sha256: str = ""
    auto_install: bool = False
