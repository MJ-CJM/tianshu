"""Authenticated read surface for immutable Evidence Bundle v1 exports."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, Response

from tianshu.evidence.models import ClosedEvidenceBundleV1
from tianshu.evidence.service import EvidenceService
from tianshu.gateway.auth import get_auth_context
from tianshu.models.principal import AuthContext
from tianshu.storage import Storage
from tianshu.storage.artifact_repo import EvidenceRepositoryError

evidence_router = APIRouter(tags=["evidence"])

_MESSAGES = {
    "edict_not_found": "edict not found",
    "evidence_not_found": "evidence bundle not found",
    "evidence_not_closed": "evidence bundle is not closed",
    "evidence_invalid": "evidence bundle violates the durable contract",
}


def _raise(context: AuthContext, status_code: int, code: str) -> NoReturn:
    raise HTTPException(
        status_code,
        {
            "code": code,
            "message": _MESSAGES[code],
            "correlation_id": context.correlation_id,
        },
    )


@evidence_router.get("/edicts/{edict_id}/evidence")
def list_edict_evidence(request: Request, edict_id: str) -> dict[str, object]:
    context = get_auth_context(request)
    storage: Storage = request.app.state.storage
    if storage.get_edict(edict_id) is None:
        _raise(context, 404, "edict_not_found")
    try:
        bundles = storage.evidence_repo.list_for_edict(edict_id)
    except EvidenceRepositoryError:
        _raise(context, 409, "evidence_invalid")
    items = [
        {
            "bundle_id": bundle.bundle_id,
            "memorial_id": bundle.memorial_id,
            "status": bundle.status,
            "version": bundle.version,
            "content_hash": (
                bundle.content_hash if isinstance(bundle, ClosedEvidenceBundleV1) else None
            ),
            "created_at": bundle.created_at.isoformat().replace("+00:00", "Z"),
            "closed_at": (
                bundle.closed_at.isoformat().replace("+00:00", "Z")
                if isinstance(bundle, ClosedEvidenceBundleV1)
                else None
            ),
        }
        for bundle in bundles
    ]
    return {"items": items, "correlation_id": context.correlation_id}


@evidence_router.get("/evidence/{bundle_id}/download")
def download_evidence(request: Request, bundle_id: str) -> Response:
    context = get_auth_context(request)
    storage: Storage = request.app.state.storage
    try:
        bundle = storage.evidence_repo.get(bundle_id)
    except EvidenceRepositoryError:
        _raise(context, 409, "evidence_invalid")
    if bundle is None:
        _raise(context, 404, "evidence_not_found")
    if not isinstance(bundle, ClosedEvidenceBundleV1):
        _raise(context, 409, "evidence_not_closed")
    service: EvidenceService = request.app.state.evidence_service
    return Response(
        content=service.export(bundle_id),
        media_type="application/json",
        headers={"ETag": f'"{bundle.content_hash}"'},
    )


__all__ = ["evidence_router"]
