from __future__ import annotations

import hashlib
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.evidence._fixtures import evidence_service, seed_closed_run
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.models.principal import Principal
from tianshu.storage.artifact_repo import EvidenceRepositoryError

_TOKEN = "evidence-api-bootstrap-token"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}
_BASE_URL = "https://tianshu.example.com"


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=("sha256:" + hashlib.sha256(_TOKEN.encode()).hexdigest()),
    )


@pytest.fixture
def evidence_api(storage, tmp_path):
    from tianshu.gateway.evidence_api import evidence_router

    edict, memorial = seed_closed_run(storage)
    storage._conn.execute(
        "UPDATE edicts SET submitter=? WHERE id=?",
        ("user:owner", edict.id),
    )
    storage._conn.commit()
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(memorial.id)
    closed = service.close(memorial.id, expected_version=opened.version)
    settings = _settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.evidence_service = service
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(evidence_router, prefix="/api")
    return app, edict, closed, service


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=_BASE_URL, client=("127.0.0.1", 41000))


def test_evidence_list_and_download_are_authenticated_canonical_and_scoped(
    evidence_api,
) -> None:
    app, edict, closed, service = evidence_api
    mcp_only = app.state.auth_service.issue_pat(
        Principal(
            id="service:evidence-reader",
            kind="service",
            display_name="Evidence reader",
            scopes=frozenset({"mcp:read"}),
        ),
        label="evidence-reader",
        scopes=frozenset({"mcp:read"}),
    )

    with _client(app) as client:
        anonymous = client.get(f"/api/edicts/{edict.id}/evidence")
        wrong_scope = client.get(
            f"/api/edicts/{edict.id}/evidence",
            headers={"Authorization": f"Bearer {mcp_only.raw_token}"},
        )
        wrong_scope_download = client.get(
            f"/api/evidence/{closed.bundle_id}/download",
            headers={"Authorization": f"Bearer {mcp_only.raw_token}"},
        )
        listed = client.get(f"/api/edicts/{edict.id}/evidence", headers=_HEADERS)
        downloaded = client.get(
            f"/api/evidence/{closed.bundle_id}/download",
            headers=_HEADERS,
        )

    assert anonymous.status_code == 401
    assert wrong_scope.status_code == wrong_scope_download.status_code == 403
    assert listed.status_code == downloaded.status_code == 200
    assert listed.json()["items"] == [
        {
            "bundle_id": closed.bundle_id,
            "memorial_id": closed.memorial_id,
            "status": "closed",
            "version": 2,
            "content_hash": closed.content_hash,
            "created_at": "2026-07-17T08:09:10Z",
            "closed_at": "2026-07-17T08:09:10Z",
        }
    ]
    assert listed.json()["correlation_id"] == listed.headers["x-correlation-id"]
    assert downloaded.content == service.export(closed.bundle_id)
    assert downloaded.headers["content-type"] == "application/json"
    assert downloaded.headers["etag"] == f'"{closed.content_hash}"'


def test_evidence_api_maps_unknown_resources_without_disclosure(evidence_api) -> None:
    app, _, closed, _ = evidence_api

    with _client(app) as client:
        missing_edict = client.get("/api/edicts/unknown/evidence", headers=_HEADERS)
        missing_bundle = client.get(
            "/api/evidence/evidence:unknown/download",
            headers=_HEADERS,
        )

    assert missing_edict.status_code == missing_bundle.status_code == 404
    assert missing_edict.json()["detail"]["code"] == "edict_not_found"
    assert missing_bundle.json()["detail"]["code"] == "evidence_not_found"
    assert closed.content_hash not in missing_bundle.text


def test_evidence_routes_hide_owned_exports_from_another_api_principal(
    evidence_api,
    monkeypatch,
) -> None:
    app, edict, closed, service = evidence_api
    other = app.state.auth_service.issue_pat(
        Principal(
            id="user:other",
            kind="human",
            display_name="Other user",
            scopes=frozenset({"api"}),
        ),
        label="other-user",
        scopes=frozenset({"api"}),
    )
    owner_export = service.export(closed.bundle_id)
    export = Mock(wraps=service.export)
    monkeypatch.setattr(service, "export", export)
    get_bundle = Mock(side_effect=EvidenceRepositoryError("corrupt bundle"))
    list_bundles = Mock(side_effect=EvidenceRepositoryError("corrupt bundle"))
    monkeypatch.setattr(app.state.storage.evidence_repo, "get", get_bundle)
    monkeypatch.setattr(app.state.storage.evidence_repo, "list_for_edict", list_bundles)
    headers = {"Authorization": f"Bearer {other.raw_token}"}

    with _client(app) as client:
        listed = client.get(f"/api/edicts/{edict.id}/evidence", headers=headers)
        downloaded = client.get(
            f"/api/evidence/{closed.bundle_id}/download",
            headers=headers,
        )

    assert listed.status_code == downloaded.status_code == 404
    assert listed.json()["detail"]["code"] == "edict_not_found"
    assert downloaded.json()["detail"]["code"] == "evidence_not_found"
    assert closed.bundle_id not in listed.text
    assert closed.bundle_id not in downloaded.text
    assert closed.content_hash not in listed.text
    assert closed.content_hash not in downloaded.text
    assert downloaded.content != owner_export
    get_bundle.assert_not_called()
    list_bundles.assert_not_called()
    export.assert_not_called()
