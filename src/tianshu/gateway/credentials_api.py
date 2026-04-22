"""/api/credentials CRUD。Spec §8.1 后端。"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.secrets import (
    Credential,
    CredentialCreate,
    CredentialStore,
    CredentialUpdate,
    CredentialView,
    get_vault,
)
from tianshu.tools.hongluisi.engine_registry import rebuild_engines

logger = logging.getLogger(__name__)

credentials_router = APIRouter(prefix="/credentials", tags=["credentials"])


def _trigger_engine_rebuild() -> None:
    """凭证 CRUD 完成后 live 重建 engine；失败仅 log，不影响 API 成功返回。"""
    try:
        rebuild_engines()
    except Exception:
        logger.exception(
            "rebuild_engines failed; restart required for new key to take effect"
        )


def _store(request: Request) -> CredentialStore:
    vault = get_vault()
    if vault is None:
        raise HTTPException(503, "secret vault unavailable: TIANSHU_SECRET_MASTER_KEY unset")
    storage = request.app.state.storage
    return CredentialStore(storage, vault)


def _to_view(c: Credential) -> CredentialView:
    return CredentialView(
        id=c.id,
        name=c.name,
        host_pattern=c.host_pattern,
        header_template=c.header_template,
        extra_headers=c.extra_headers,
        created_at=c.created_at,
        updated_at=c.updated_at,
        last_used_at=c.last_used_at,
        kind=c.kind,
        provider_name=c.provider_name,
    )


@credentials_router.get("")
def list_credentials(
    request: Request,
    kind: str | None = Query(None, pattern="^(edict_auth|engine_provider)$"),
) -> list[CredentialView]:
    return [_to_view(c) for c in _store(request).list_by_kind(kind)]


@credentials_router.post("", status_code=201)
def create_credential(req: CredentialCreate, request: Request) -> CredentialView:
    try:
        c = _store(request).create(req)
    except ValueError as e:
        raise HTTPException(400, f"create_failed: {e}") from e
    except sqlite3.IntegrityError as e:
        # 通常是 UNIQUE (name) 冲突 —— 给用户可操作的提示
        msg = str(e).lower()
        if "name" in msg or "unique" in msg:
            raise HTTPException(
                409,
                f"凭证名称已存在：{req.name}（请换一个名字，或先删除旧记录）",
            ) from e
        raise HTTPException(409, f"integrity_error: {e}") from e
    except Exception as e:
        logger.exception("create_credential failed")
        raise HTTPException(500, f"internal_error: {type(e).__name__}") from e
    if c.kind == "engine_provider":
        _trigger_engine_rebuild()
    return _to_view(c)


@credentials_router.patch("/{cred_id}")
def update_credential(
    cred_id: str, req: CredentialUpdate, request: Request
) -> CredentialView:
    c = _store(request).update(cred_id, req)
    if c is None:
        raise HTTPException(404, "credential_not_found")
    if c.kind == "engine_provider":
        _trigger_engine_rebuild()
    return _to_view(c)


@credentials_router.delete("/{cred_id}")
def delete_credential(cred_id: str, request: Request) -> dict:
    store = _store(request)
    cred = store.get(cred_id)
    if cred is None:
        raise HTTPException(404, "credential_not_found")

    # 检查 Edict 引用：Task 17 提供 find_edicts_referencing_host（若未就绪，跳过检查，直接删除）
    storage = request.app.state.storage
    if hasattr(storage, "find_edicts_referencing_host"):
        refs = storage.find_edicts_referencing_host(cred.host_pattern)
        if refs:
            raise HTTPException(
                409,
                f"credential referenced by {len(refs)} active edict(s); "
                "remove references first",
            )

    store.delete(cred_id)
    if cred.kind == "engine_provider":
        _trigger_engine_rebuild()
    return {"ok": True}
