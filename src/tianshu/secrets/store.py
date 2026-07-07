"""CredentialStore：把 Storage 行转为 Credential domain 对象。"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from ulid import ULID

from tianshu.secrets.models import (
    Credential,
    CredentialCreate,
    CredentialUpdate,
)
from tianshu.secrets.vault import SecretVault
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_credential(row) -> Credential:
    # Handle old schema rows pre-migration defensively.
    # NOTE: row 是 sqlite3.Row，非 dict —— `in row` 会走 __iter__ 逐值比较，
    # 语义与 `in row.keys()`（比较列名）不同，不能按 SIM118 建议改写。
    kind: Literal["edict_auth", "engine_provider"]
    try:
        kind = row["kind"] if "kind" in row.keys() else "edict_auth"  # noqa: SIM118
    except (IndexError, KeyError):
        kind = "edict_auth"
    try:
        provider = row["provider_name"] if "provider_name" in row.keys() else None  # noqa: SIM118
    except (IndexError, KeyError):
        provider = None
    try:
        enabled_val = row["enabled"] if "enabled" in row.keys() else 1  # noqa: SIM118
    except (IndexError, KeyError):
        enabled_val = 1
    return Credential(
        id=row["id"],
        name=row["name"],
        host_pattern=row["host_pattern"],
        header_template=row["header_template"],
        extra_headers=json.loads(row["extra_headers"]),
        encrypted_value=row["encrypted_value"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_used_at=(datetime.fromisoformat(row["last_used_at"]) if row["last_used_at"] else None),
        kind=kind or "edict_auth",
        provider_name=provider,
        enabled=bool(enabled_val),
    )


class CredentialStore:
    def __init__(self, storage: Storage, vault: SecretVault) -> None:
        self._storage = storage
        self._vault = vault

    def create(self, req: CredentialCreate) -> Credential:
        cred_id = str(ULID())
        encrypted = self._vault.encrypt(req.value)

        if req.kind == "engine_provider":
            if not req.provider_name:
                raise ValueError("engine_provider credential requires provider_name")
            if req.provider_name not in {"jina", "tavily", "firecrawl"}:
                raise ValueError(f"unsupported provider_name: {req.provider_name}")
            existing = self._storage.find_credentials_by_provider(req.provider_name)
            if existing is not None:
                raise ValueError(
                    f"provider '{req.provider_name}' already configured; "
                    "update existing credential instead"
                )
            host_pattern = ""
            header_template = ""
        else:  # edict_auth
            if not req.host_pattern or not req.header_template:
                raise ValueError("edict_auth credential requires host_pattern and header_template")
            host_pattern = req.host_pattern
            header_template = req.header_template

        self._storage.insert_credential(
            cred_id=cred_id,
            name=req.name,
            host_pattern=host_pattern,
            header_template=header_template,
            extra_headers_json=json.dumps(req.extra_headers),
            encrypted_value=encrypted,
            now_iso=_now_iso(),
            kind=req.kind,
            provider_name=req.provider_name,
        )
        row = self._storage.get_credential_by_id(cred_id)
        return _row_to_credential(row)

    def list_all(self) -> list[Credential]:
        return [_row_to_credential(r) for r in self._storage.list_credentials()]

    def list_by_kind(self, kind: str | None = None) -> list[Credential]:
        return [_row_to_credential(r) for r in self._storage.list_credentials(kind=kind)]

    def get(self, cred_id: str) -> Credential | None:
        row = self._storage.get_credential_by_id(cred_id)
        return _row_to_credential(row) if row else None

    def find_for_provider(self, provider_name: str) -> Credential | None:
        row = self._storage.find_credentials_by_provider(provider_name)
        return _row_to_credential(row) if row else None

    def decrypt_value(self, cred: Credential) -> str:
        """公开解密接口，给 engine 工厂用。"""
        return self._vault.decrypt(cred.encrypted_value)

    def find_for_host(self, host: str) -> Credential | None:
        """最具体匹配：字面 > 通配 > None。"""
        rows = self._storage.find_credentials_by_host(host)
        exact = [r for r in rows if r["host_pattern"] == host]
        if exact:
            return _row_to_credential(exact[0])
        for r in rows:
            pat = r["host_pattern"]
            if pat.startswith("*.") and host.endswith(pat[1:]):
                return _row_to_credential(r)
        return None

    def update(self, cred_id: str, req: CredentialUpdate) -> Credential | None:
        if self._storage.get_credential_by_id(cred_id) is None:
            return None
        enc = self._vault.encrypt(req.value) if req.value is not None else None
        extra = json.dumps(req.extra_headers) if req.extra_headers is not None else None
        self._storage.update_credential(
            cred_id,
            encrypted_value=enc,
            extra_headers_json=extra,
            enabled=req.enabled,
            now_iso=_now_iso(),
        )
        return self.get(cred_id)

    def delete(self, cred_id: str) -> bool:
        if self._storage.get_credential_by_id(cred_id) is None:
            return False
        self._storage.soft_delete_credential(cred_id, _now_iso())
        return True

    def mark_used(self, cred_id: str) -> None:
        self._storage.mark_credential_used(cred_id, _now_iso())


def resolve_provider_key(
    store: CredentialStore | None,
    provider_name: str,
    env_var: str,
) -> tuple[str | None, str]:
    """返回 (api_key, source)；source ∈ {'db','env','none'}。

    DB 配置优先 → env 回落 → None。解密失败不抛，记日志回落 env。
    """
    import os

    if store is not None:
        cred = store.find_for_provider(provider_name)
        if cred is not None:
            try:
                return store.decrypt_value(cred), "db"
            except Exception:
                logger.exception(
                    "decrypt provider '%s' failed, falling back to env",
                    provider_name,
                )
    key = os.getenv(env_var)
    if key:
        return key, "env"
    return None, "none"
