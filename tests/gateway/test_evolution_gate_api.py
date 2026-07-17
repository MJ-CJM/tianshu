"""Authenticated candidate and gate read/evaluate API contracts."""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.evolution.test_gate_evaluator import NOW, _staged_candidate
from tianshu.app import create_app, lifespan
from tianshu.bootstrap.wiring_skills import wire_evolution_services
from tianshu.config import TianshuSettings
from tianshu.evolution.gates import GateEvaluator
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.evolution_api import evolution_router
from tianshu.models.evolution_candidate import GateName
from tianshu.storage.facade import Storage

TOKEN = "evolution-gate-bootstrap-token"
BASE_URL = "https://tianshu.example.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _app(tmp_path) -> tuple[FastAPI, Storage, str, int]:
    settings = TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash="sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    storage = Storage(str(tmp_path / "evolution-gate-api.db"))
    storage.init_db()
    candidate = _staged_candidate(storage)
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.evolution_gate_evaluator = GateEvaluator(storage, clock=lambda: NOW)
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(evolution_router, prefix="/api")
    return app, storage, candidate.candidate_id, candidate.version


def test_candidate_gate_evaluate_and_read_are_authenticated_and_correlated(tmp_path) -> None:
    app, storage, candidate_id, version = _app(tmp_path)
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            anonymous = client.post(
                f"/api/evolution/candidates/{candidate_id}/gate/evaluate",
                json={"expected_version": version},
            )
            evaluated = client.post(
                f"/api/evolution/candidates/{candidate_id}/gate/evaluate",
                headers=HEADERS,
                json={"expected_version": version, "passed": True},
            )
            read = client.get(f"/api/evolution/candidates/{candidate_id}/gate", headers=HEADERS)
        assert anonymous.status_code == 401
        assert evaluated.status_code == 200
        assert evaluated.json()["data"]["promotion_allowed"] is False
        assert evaluated.json()["data"]["blocking_gates"] == [gate.value for gate in GateName]
        assert "passed" not in evaluated.json()["data"]
        assert evaluated.json()["correlation_id"] == evaluated.headers["x-correlation-id"]
        assert read.status_code == 200
        assert read.json()["data"] == evaluated.json()["data"]
    finally:
        storage.close()


def test_task_three_api_exposes_no_promote_canary_or_rollback_route(tmp_path) -> None:
    app, storage, _candidate_id, _version = _app(tmp_path)
    try:
        paths = {route.path for route in app.routes}
        assert not any(
            token in path for path in paths for token in ("promote", "canary", "rollback")
        )
    finally:
        storage.close()


def test_composition_wires_candidate_gate_and_skill_install_services(tmp_path) -> None:
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "wired.db"),
        artifact_dir=str(tmp_path / "artifacts"),
        workspace_dir=str(tmp_path / "workspace"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
    )
    app = FastAPI()
    storage = Storage(settings.db_path)
    storage.init_db()
    app.state.storage = storage
    from tianshu.evidence.service import ArtifactStore

    app.state.artifact_store = ArtifactStore(
        settings.artifact_dir,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=settings.artifact_max_bytes,
        max_total_bytes=settings.artifact_quota_bytes,
    )
    try:
        wire_evolution_services(app, settings, skill_target=tmp_path / "skills")
        assert app.state.candidate_service is not None
        assert app.state.evolution_gate_evaluator is not None
        assert app.state.skill_install_service is not None
    finally:
        storage.close()


@pytest.mark.asyncio
async def test_create_app_lifespan_wires_task_three_services_and_closes_storage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TIANSHU_RUNTIME_SKILLS_DIR", str(tmp_path / "runtime-skills"))
    settings = TianshuSettings(
        _env_file=None,
        startup_profile="demo",
        db_path=str(tmp_path / "lifespan.db"),
        artifact_dir=str(tmp_path / "artifacts"),
        workspace_dir=str(tmp_path / "workspace"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
        log_dir=str(tmp_path / "logs"),
    )
    app = create_app(settings)

    async with lifespan(app):
        storage = app.state.storage
        assert app.state.candidate_service is not None
        assert app.state.evolution_gate_evaluator is not None
        assert app.state.skill_install_service is not None
        assert storage._conn.execute("SELECT 1").fetchone()[0] == 1

    assert storage._conn is None
