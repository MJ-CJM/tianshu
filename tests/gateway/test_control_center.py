from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.app import create_app
from tianshu.application.control_center import ControlCenterUnavailable
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.control_center_api import control_center_router
from tianshu.models.control_center import ControlCenterSnapshotV1
from tianshu.storage import Storage

TOKEN = "control-center-bootstrap-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE_URL = "https://tianshu.example.com"
NOW = datetime(2026, 7, 17, 9, tzinfo=UTC)


class SnapshotService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.principals: list[str] = []

    def get_snapshot(self, auth):
        self.principals.append(auth.principal.id)
        if self.fail:
            raise ControlCenterUnavailable("source unavailable")
        return ControlCenterSnapshotV1(
            generated_at=NOW,
            readiness="ready",
            active_run_total=0,
            pending_decision_total=0,
            evidence_total=0,
            active_runs=(),
            pending_decisions=(),
            recent_evidence=(),
            evolution_status="not_enabled",
        )


def _app(tmp_path, service: SnapshotService) -> tuple[FastAPI, Storage]:
    settings = TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash="sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    storage = Storage(str(tmp_path / "control-center.db"))
    storage.init_db()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.control_center_service = service
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(control_center_router, prefix="/api")
    return app, storage


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000))


def test_snapshot_is_authenticated_principal_scoped_and_correlated(tmp_path) -> None:
    service = SnapshotService()
    app, storage = _app(tmp_path, service)
    try:
        with _client(app) as client:
            anonymous = client.get("/api/control")
            response = client.get("/api/control", headers=HEADERS)
        assert anonymous.status_code == 401
        assert response.status_code == 200
        assert service.principals == ["user:owner"]
        assert response.json()["data"]["schema_version"] == 1
        assert response.json()["data"]["active_run_total"] == 0
        assert response.json()["data"]["pending_decision_total"] == 0
        assert response.json()["data"]["evidence_total"] == 0
        assert response.json()["data"]["evolution_status"] == "not_enabled"
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    finally:
        storage.close()


def test_source_failure_is_a_correlated_503_not_an_empty_success(tmp_path) -> None:
    app, storage = _app(tmp_path, SnapshotService(fail=True))
    try:
        with _client(app) as client:
            response = client.get("/api/control", headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "control_center_unavailable",
            "message": "control center sources are unavailable",
            "correlation_id": response.headers["x-correlation-id"],
        }
        assert "active_runs" not in response.text
    finally:
        storage.close()


def test_snapshot_contract_serializes_workspace_totals(tmp_path) -> None:
    app, storage = _app(tmp_path, SnapshotService())
    try:
        with _client(app) as client:
            response = client.get("/api/control", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["data"]["unarchived_edict_total"] == 0
        assert response.json()["data"]["awaiting_follow_up_total"] == 0
        assert response.json()["data"]["cancelled_edict_total"] == 0
    finally:
        storage.close()


def test_composition_root_registers_the_control_center_route() -> None:
    app = create_app(TianshuSettings(_env_file=None))
    assert any(route.path == "/api/control" for route in app.routes)
