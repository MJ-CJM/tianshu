"""P5 executor evolution API, composition, and read-only boundary contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.evolution.test_candidate_adapters import (
    _proposal as candidate_proposal,
)
from tests.evolution.test_candidate_adapters import (
    _service as candidate_service,
)
from tests.evolution.test_gate_evaluator import NOW, _staged_candidate
from tests.evolution.test_promotion_fail_closed import (
    _GateAuthority,
    _green,
    _ready,
)
from tianshu.bootstrap.wiring_skills import wire_evolution_services
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.evolution.gates import GateEvaluator
from tianshu.evolution.promotion import (
    PromotionConflict,
    PromotionService,
    StartCanaryCommand,
    UnavailablePromotionAdapter,
)
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.evolution_api import evolution_router
from tianshu.gateway.keqing_api import keqing_router
from tianshu.models.evolution_candidate import CandidateKind, CandidateLifecycle
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage
from tianshu.storage.evolution_repo import EvolutionRepository

_BASE_URL = "https://tianshu.example.com"
_BOOTSTRAP_TOKEN = "executor-evolution-api-bootstrap-token"
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _settings(tmp_path: Path, *, name: str) -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / f"{name}.db"),
        artifact_dir=str(tmp_path / f"{name}-artifacts"),
        workspace_dir=str(tmp_path / f"{name}-workspace"),
        memory_dir=str(tmp_path / f"{name}-memory"),
        runtime_personas_dir=str(tmp_path / f"{name}-personas"),
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=(
            "sha256:" + hashlib.sha256(_BOOTSTRAP_TOKEN.encode()).hexdigest()
        ),
    )


def _artifact_store(storage: Storage, root: Path) -> ArtifactStore:
    return ArtifactStore(
        root,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
        clock=lambda: NOW,
    )


def _secure_app(storage: Storage, settings: TianshuSettings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.include_router(evolution_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def _pat(app: FastAPI, *, principal_id: str, admin: bool) -> dict[str, str]:
    scopes = frozenset({"api", "admin"} if admin else {"api"})
    issued = app.state.auth_service.issue_pat(
        Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=principal_id,
            scopes=scopes,
        ),
        label=principal_id,
        scopes=scopes,
    )
    return {"Authorization": f"Bearer {issued.raw_token}"}


def _command(candidate_version: int, *, key: str) -> StartCanaryCommand:
    return StartCanaryCommand(
        expected_version=candidate_version,
        idempotency_key=key,
        reason="begin reviewed executor canary",
        allocation_basis_points=250,
        allocation_seed_id="executor-api-seed",
    )


def _owner_auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Executor owner",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="executor-sync-canary-test",
    )


def _seed_executor_candidate(storage: Storage) -> None:
    storage._conn.execute(  # noqa: SLF001 - durable read-only projection fixture
        """
        INSERT INTO evolution_candidates (
            candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES ('candidate-executor-status', 1, 'executor', 'executor:keqing:pi',
                  '{}', ?, '{}', '{}', ?, '{}', ?, 0, '[]', NULL, '{}',
                  'proposed', 1, ?, ?)
        """,
        (_HASH_A, _HASH_B, _HASH_C, NOW.isoformat(), NOW.isoformat()),
    )
    storage._conn.commit()  # noqa: SLF001 - durable read-only projection fixture


def test_generic_stage_is_admin_only_and_preserves_exact_envelopes(tmp_path: Path) -> None:
    settings = _settings(tmp_path, name="stage")
    storage = Storage(settings.db_path)
    storage.init_db()
    artifacts = _artifact_store(storage, Path(settings.artifact_dir))
    service = candidate_service(storage, artifacts)
    candidate = service.propose(candidate_proposal(CandidateKind.EXECUTOR))
    app = _secure_app(storage, settings)
    app.state.candidate_service = service
    app.state.evolution_gate_evaluator = GateEvaluator(storage, clock=lambda: NOW)
    api_headers = _pat(app, principal_id="user:api", admin=False)
    admin_headers = _pat(app, principal_id="user:admin", admin=True)
    path = f"/api/evolution/candidates/{candidate.candidate_id}/stage"

    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            denied = client.post(path, headers=api_headers)
            missing = client.post(
                "/api/evolution/candidates/candidate-missing/stage",
                headers=admin_headers,
            )
            staged = client.post(path, headers=admin_headers)
            staged_version = staged.json()["data"]["candidate"]["version"]
            app.state.evolution_gate_evaluator.evaluate(
                candidate.candidate_id,
                expected_version=staged_version,
            )
            conflict = client.post(path, headers=admin_headers)

        assert denied.status_code == 403
        assert denied.json() == {
            "error": {
                "code": "insufficient_scope",
                "message": "request rejected by the security boundary",
                "correlation_id": denied.headers["x-correlation-id"],
            }
        }
        assert missing.status_code == 404
        assert missing.json() == {"detail": {"code": "candidate_not_found"}}
        assert staged.status_code == 200
        assert set(staged.json()) == {"data", "correlation_id"}
        assert set(staged.json()["data"]) == {"candidate", "staged_artifact"}
        assert staged.json()["data"]["candidate"]["candidate_id"] == candidate.candidate_id
        assert staged.json()["data"]["candidate"]["lifecycle"] == "staged"
        assert staged.json()["correlation_id"] == staged.headers["x-correlation-id"]
        assert conflict.status_code == 409
        assert conflict.json() == {"detail": {"code": "candidate_stage_conflict"}}
    finally:
        storage.close()


def test_gate_api_binds_additional_evidence_into_candidate_and_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path, name="gate-evidence")
    storage = Storage(settings.db_path)
    storage.init_db()
    candidate = _staged_candidate(storage)
    app = _secure_app(storage, settings)
    app.state.evolution_gate_evaluator = GateEvaluator(storage, clock=lambda: NOW)
    admin_headers = _pat(app, principal_id="user:admin", admin=True)
    additional_id = "evidence:executor-api-additional"

    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            response = client.post(
                f"/api/evolution/candidates/{candidate.candidate_id}/gate/evaluate",
                headers=admin_headers,
                json={
                    "expected_version": candidate.version,
                    "additional_evidence_bundle_ids": [additional_id],
                },
            )

        assert response.status_code == 200
        assert response.json()["data"]["evidence_bundle_ids"] == [additional_id]
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
        with storage.unit_of_work() as unit_of_work:
            durable = EvolutionRepository().get_candidate(
                unit_of_work.connection,
                candidate.candidate_id,
            )
            snapshot_ids = unit_of_work.connection.execute(
                """SELECT evidence_bundle_ids_json
                   FROM evolution_gate_snapshots
                   WHERE candidate_id=?""",
                (candidate.candidate_id,),
            ).fetchone()[0]
            unit_of_work.commit()
        assert durable is not None
        assert durable.evidence_bundle_ids == (additional_id,)
        assert tuple(json.loads(snapshot_ids)) == (additional_id,)
    finally:
        storage.close()


def test_default_disabled_executor_adapter_fails_closed_for_sync_and_http_canary(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, name="disabled-canary")
    assert settings.executor_generation_enabled is False
    storage = Storage(settings.db_path)
    storage.init_db()
    artifacts = _artifact_store(storage, Path(settings.artifact_dir))
    app = _secure_app(storage, settings)
    app.state.artifact_store = artifacts
    app.state.evidence_service = EvidenceService(storage, artifacts)
    wire_evolution_services(app, settings, skill_target=tmp_path / "runtime-skills")
    candidate = _ready(storage, CandidateKind.EXECUTOR)
    app.state.promotion_service = PromotionService(
        storage,
        _GateAuthority(_green(candidate)),
        adapter_resolver=app.state.promotion_adapters.__getitem__,
        clock=lambda: NOW,
    )
    admin_headers = _pat(app, principal_id="user:admin", admin=True)

    try:
        assert isinstance(
            app.state.promotion_adapters[CandidateKind.EXECUTOR],
            UnavailablePromotionAdapter,
        )
        with pytest.raises(PromotionConflict, match="executor_canary_requires_async_path"):
            app.state.promotion_service.start_canary(
                candidate.candidate_id,
                _command(candidate.version, key="sync-disabled"),
                auth=_owner_auth(),
            )

        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            response = client.post(
                f"/api/evolution/candidates/{candidate.candidate_id}/canary",
                headers=admin_headers,
                json=_command(candidate.version, key="http-disabled").model_dump(mode="json"),
            )

        assert response.status_code == 409
        assert response.json() == {"detail": {"code": "executor_generation_unavailable"}}
        with storage.unit_of_work() as unit_of_work:
            durable = EvolutionRepository().get_candidate(
                unit_of_work.connection,
                candidate.candidate_id,
            )
            routing_count = unit_of_work.connection.execute(
                "SELECT COUNT(*) FROM evolution_routing_allocations WHERE candidate_id=?",
                (candidate.candidate_id,),
            ).fetchone()[0]
            unit_of_work.commit()
        assert durable is not None
        assert durable.lifecycle is CandidateLifecycle.READY
        assert routing_count == 0
    finally:
        storage.close()


def test_keqing_status_does_not_create_executor_evolution_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = Storage(str(tmp_path / "keqing-read-only.db"))
    storage.init_db()
    _seed_executor_candidate(storage)
    artifacts = _artifact_store(storage, tmp_path / "keqing-artifacts")
    artifacts.put_bytes(
        b"read-only-sentinel",
        media_type="application/octet-stream",
        redaction="test_sentinel",
    )
    monkeypatch.setattr(
        "tianshu.gateway.keqing_api._detect_installed_version",
        lambda _binary: None,
    )
    app = FastAPI()
    app.include_router(keqing_router)
    app.state.storage = storage
    app.state.config_manager = SimpleNamespace(
        agent_config=SimpleNamespace(keqing_gateway_enabled=False)
    )
    tables = (
        "evolution_candidates",
        "artifact_records",
        "executor_generation_authorities",
        "runtime_generations",
    )
    before = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in tables
    }
    changes_before = storage._conn.total_changes  # noqa: SLF001

    try:
        with TestClient(app) as client:
            response = client.get("/keqing/status")
        after = {
            table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
            for table in tables
        }
        changes_after = storage._conn.total_changes  # noqa: SLF001

        assert response.status_code == 200
        assert before == {
            "evolution_candidates": 1,
            "artifact_records": 1,
            "executor_generation_authorities": 0,
            "runtime_generations": 0,
        }
        assert after == before
        assert changes_after == changes_before
    finally:
        storage.close()
