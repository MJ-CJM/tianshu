"""/api/credentials CRUD。Spec §8.1 后端。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from tianshu.secrets import (
    Credential,
    CredentialCreate,
    CredentialStore,
    CredentialUpdate,
    CredentialView,
    get_vault,
)

logger = logging.getLogger(__name__)

credentials_router = APIRouter(prefix="/credentials", tags=["credentials"])


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
    )


@credentials_router.get("")
def list_credentials(request: Request) -> list[CredentialView]:
    return [_to_view(c) for c in _store(request).list_all()]


@credentials_router.post("", status_code=201)
def create_credential(req: CredentialCreate, request: Request) -> CredentialView:
    try:
        c = _store(request).create(req)
    except Exception as e:
        logger.exception("create_credential failed")
        raise HTTPException(400, f"create_failed: {type(e).__name__}") from e
    return _to_view(c)


@credentials_router.patch("/{cred_id}")
def update_credential(
    cred_id: str, req: CredentialUpdate, request: Request
) -> CredentialView:
    c = _store(request).update(cred_id, req)
    if c is None:
        raise HTTPException(404, "credential_not_found")
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
    return {"ok": True}
