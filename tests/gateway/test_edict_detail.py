from __future__ import annotations

import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.application.edict_detail import EdictDetailNotFound, EdictDetailUnavailable
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.edicts_api import edicts_router
from tianshu.models import Edict
from tianshu.models.principal import Principal
from tianshu.storage import Storage

TOKEN = "edict-detail-bootstrap-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE_URL = "https://tianshu.example.com"


class _Snapshot:
    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return {
            "schema_version": 1,
            "edict": {"id": "edict-1"},
            "memorials": [],
            "runs": [],
            "decisions": [],
            "evidence": [],
        }


class _DetailService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_snapshot(self, auth, edict_id: str) -> _Snapshot:
        self.calls.append((auth.principal.id, edict_id))
        return _Snapshot()


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash="sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest(),
    )


def _app(tmp_path) -> tuple[FastAPI, Storage, _DetailService]:
    settings = _settings()
    storage = Storage(str(tmp_path / "edict-detail.db"))
    storage.init_db()
    storage.save_edict(
        Edict(
            id="edict-1",
            goal="detail",
            submitter="user:owner",
        )
    )
    service = _DetailService()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.edict_detail_service = service
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(edicts_router, prefix="/api")
    return app, storage, service


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000))


def test_composed_detail_is_authenticated_scoped_and_correlated(tmp_path) -> None:
    app, storage, service = _app(tmp_path)
    mcp_only = app.state.auth_service.issue_pat(
        Principal(
            id="service:detail-reader",
            kind="service",
            display_name="Detail reader",
            scopes=frozenset({"mcp:read"}),
        ),
        label="detail-reader",
        scopes=frozenset({"mcp:read"}),
    )
    try:
        with _client(app) as client:
            anonymous = client.get("/api/edicts/edict-1/detail")
            wrong_scope = client.get(
                "/api/edicts/edict-1/detail",
                headers={"Authorization": f"Bearer {mcp_only.raw_token}"},
            )
            response = client.get("/api/edicts/edict-1/detail", headers=HEADERS)

        assert anonymous.status_code == 401
        assert wrong_scope.status_code == 403
        assert response.status_code == 200
        assert response.json()["data"]["schema_version"] == 1
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
        assert service.calls == [("user:owner", "edict-1")]
    finally:
        storage.close()


def test_composed_detail_maps_hidden_and_unavailable_sources_truthfully(tmp_path) -> None:
    app, storage, _ = _app(tmp_path)

    class _FailingService:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def get_snapshot(self, _auth, _edict_id: str):
            raise self.error

    try:
        with _client(app) as client:
            app.state.edict_detail_service = _FailingService(EdictDetailNotFound("hidden"))
            hidden = client.get("/api/edicts/hidden/detail", headers=HEADERS)
            app.state.edict_detail_service = _FailingService(
                EdictDetailUnavailable("database offline")
            )
            unavailable = client.get("/api/edicts/edict-1/detail", headers=HEADERS)

        assert hidden.status_code == 404
        assert hidden.json()["detail"] == {
            "code": "edict_detail_not_found",
            "message": "edict detail not found",
            "correlation_id": hidden.headers["x-correlation-id"],
        }
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"] == {
            "code": "edict_detail_unavailable",
            "message": "edict detail sources are unavailable",
            "correlation_id": unavailable.headers["x-correlation-id"],
        }
        assert "database offline" not in unavailable.text
    finally:
        storage.close()
