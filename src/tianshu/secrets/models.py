"""凭证 DTO。Spec Section 4.2。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Credential(BaseModel):
    id: str
    name: str
    host_pattern: str
    header_template: str  # 例: "Authorization: Bearer {value}"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    encrypted_value: bytes  # ciphertext
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    kind: Literal["edict_auth", "engine_provider", "llm_provider"] = "edict_auth"
    provider_name: str | None = None
    enabled: bool = True


class CredentialCreate(BaseModel):
    name: str
    value: str  # plaintext，加密后丢
    kind: Literal["edict_auth", "engine_provider", "llm_provider"] = "edict_auth"
    # edict_auth required fields（engine_provider / llm_provider 忽略）
    host_pattern: str = ""
    header_template: str = ""
    extra_headers: dict[str, str] = Field(default_factory=dict)
    # engine_provider / llm_provider required field
    provider_name: str | None = None


class CredentialUpdate(BaseModel):
    value: str | None = None
    extra_headers: dict[str, str] | None = None
    enabled: bool | None = None


class CredentialView(BaseModel):
    """返回给前端用，不包含 encrypted_value / value。"""

    id: str
    name: str
    host_pattern: str
    header_template: str
    extra_headers: dict[str, str]
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    kind: Literal["edict_auth", "engine_provider", "llm_provider"] = "edict_auth"
    provider_name: str | None = None
    enabled: bool = True
