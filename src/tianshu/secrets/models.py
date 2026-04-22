"""凭证 DTO。Spec Section 4.2。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Credential(BaseModel):
    id: str
    name: str
    host_pattern: str
    header_template: str      # 例: "Authorization: Bearer {value}"
    extra_headers: dict[str, str] = Field(default_factory=dict)
    encrypted_value: bytes    # ciphertext
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None


class CredentialCreate(BaseModel):
    name: str
    host_pattern: str
    header_template: str
    value: str                # plaintext，加密后丢
    extra_headers: dict[str, str] = Field(default_factory=dict)


class CredentialUpdate(BaseModel):
    value: str | None = None
    extra_headers: dict[str, str] | None = None


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
