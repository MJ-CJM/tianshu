"""HTTP skill writes are authenticated candidate proposals, never live writes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bootstrap.wiring_skills import wire_evolution_services
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.skills_api import skills_router
from tianshu.skills.loader import SkillsLoader
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.facade import Storage

TOKEN = "skill-candidate-bootstrap-token"
BASE_URL = "https://tianshu.example.com"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _app(tmp_path: Path) -> tuple[FastAPI, Storage, Path]:
    settings = TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash="sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest(),
        workspace_dir=str(tmp_path / "workspace"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
    )
    storage = Storage(str(tmp_path / "skills-api.db"))
    storage.init_db()
    live = tmp_path / "live-skills"
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.artifact_store = ArtifactStore(
        tmp_path / "artifacts",
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=1024 * 1024,
        max_total_bytes=8 * 1024 * 1024,
    )
    app.state.skills_loader = SkillsLoader(builtin_dir=tmp_path / "builtin-skills", user_dir=live)
    app.state.skill_metrics_store = None
    app.state.public_webhook_paths = set()
    wire_evolution_services(app, settings, skill_target=live)
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(skills_router, prefix="/api")
    return app, storage, live


def test_http_propose_and_stage_delegate_service_without_live_write(tmp_path: Path) -> None:
    app, storage, live = _app(tmp_path)
    content = "---\nname: api-skill\ndescription: API skill\n---\n\nSafe."
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            anonymous = client.post("/api/skills", json={"name": "api-skill", "content": content})
            proposed = client.post(
                "/api/skills",
                headers=HEADERS,
                json={"name": "api-skill", "content": content},
            )
            candidate_id = proposed.json()["data"]["candidate_id"]
            staged = client.post(f"/api/skills/candidates/{candidate_id}/stage", headers=HEADERS)
        assert anonymous.status_code == 401
        assert proposed.status_code == 201
        assert proposed.json()["data"]["lifecycle"] == "proposed"
        assert staged.status_code == 200
        assert staged.json()["data"]["lifecycle"] == "staged"
        with storage.unit_of_work() as unit_of_work:
            candidate = EvolutionRepository().get_candidate(unit_of_work.connection, candidate_id)
            unit_of_work.commit()
        assert candidate is not None
        assert candidate.base.artifact_digest != candidate.candidate.artifact_digest
        assert candidate.base.canonical_digest != candidate.candidate.canonical_digest
        assert candidate.base.version == "absent"
        assert candidate.rollback.champion_ref == candidate.base
        assert not (live / "api-skill").exists()
    finally:
        storage.close()
