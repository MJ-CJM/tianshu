"""HTTP skill writes are authenticated candidate proposals, never live writes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import frontmatter
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bootstrap.wiring_skills import wire_evolution_services
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.skills_api import skills_router
from tianshu.models.canonical import canonical_sha256
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


def test_http_update_snapshots_complete_authoritative_live_package(tmp_path: Path) -> None:
    app, storage, live = _app(tmp_path)
    skill_root = live / "existing-skill"
    raw_skill = (
        "---\n# trusted comment\nname: existing-skill\ndescription: Existing skill\n"
        "metadata:\n  openclaw:\n    always: true\n"
        "custom:\n  nested: retained\n---\n\nOriginal instructions."
    )
    resource_files = {
        "scripts/run.py": "print('safe')\n",
        "references/guide.md": "# Guide\n",
        "assets/data.txt": "asset\n",
        "templates/empty/.keep": "",
    }
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(raw_skill, encoding="utf-8")
    for relative, value in resource_files.items():
        target = skill_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
    expected_members = [
        {"path": "SKILL.md", "kind": "file", "content": raw_skill},
        {"path": "assets", "kind": "directory", "content": None},
        {"path": "assets/data.txt", "kind": "file", "content": "asset\n"},
        {"path": "references", "kind": "directory", "content": None},
        {"path": "references/guide.md", "kind": "file", "content": "# Guide\n"},
        {"path": "scripts", "kind": "directory", "content": None},
        {"path": "scripts/run.py", "kind": "file", "content": "print('safe')\n"},
        {"path": "templates", "kind": "directory", "content": None},
        {"path": "templates/empty", "kind": "directory", "content": None},
        {"path": "templates/empty/.keep", "kind": "file", "content": ""},
    ]
    expected_base = {
        "name": "existing-skill",
        "state": "present",
        "trust_source": "community",
        "members": expected_members,
    }
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            fetched = client.get("/api/skills/existing-skill", headers=HEADERS)
            assert fetched.status_code == 200
            current_body = fetched.json()["data"]["content"]
            assert current_body == "Original instructions."
            candidate_content = current_body.replace("Original", "Updated")
            proposed = client.put(
                "/api/skills/existing-skill",
                headers=HEADERS,
                json={"content": candidate_content},
            )
            assert proposed.status_code == 200
            candidate_id = proposed.json()["data"]["candidate_id"]
            staged = client.post(f"/api/skills/candidates/{candidate_id}/stage", headers=HEADERS)
        assert staged.status_code == 200
        with storage.unit_of_work() as unit_of_work:
            candidate = EvolutionRepository().get_candidate(unit_of_work.connection, candidate_id)
            unit_of_work.commit()
        assert candidate is not None
        assert candidate.base.version != "absent"
        assert candidate.base.artifact_digest == canonical_sha256(expected_base)
        assert json.loads(app.state.artifact_store.get_bytes(candidate.base.artifact_digest)) == (
            expected_base
        )
        assert candidate.candidate.artifact_digest != candidate.base.artifact_digest
        candidate_package = json.loads(
            app.state.artifact_store.get_bytes(candidate.candidate.artifact_digest)
        )
        candidate_skill = candidate_package["members"][0]
        assert candidate_skill["path"] == "SKILL.md"
        assert candidate_skill["kind"] == "file"
        base_post = frontmatter.loads(raw_skill)
        candidate_post = frontmatter.loads(candidate_skill["content"])
        assert candidate_post.metadata == base_post.metadata
        assert candidate_post.content == candidate_content
        trusted_header = raw_skill.rsplit("\n\nOriginal instructions.", maxsplit=1)[0]
        assert candidate_skill["content"].startswith(f"{trusted_header}\n\n")
        assert candidate_package["members"][1:] == expected_members[1:]
        assert (skill_root / "SKILL.md").read_text("utf-8") == raw_skill
        for relative, value in resource_files.items():
            assert (skill_root / relative).read_text("utf-8") == value
    finally:
        storage.close()


def test_http_update_rejects_unrenderable_live_document_without_persistence(
    tmp_path: Path,
) -> None:
    app, storage, live = _app(tmp_path)
    skill_root = live / "existing-skill"
    raw_skill = "Original instructions without frontmatter."
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(raw_skill, encoding="utf-8")
    tables = (
        "artifact_records",
        "evolution_candidates",
        "evolution_lifecycle_journal",
        "system_audit_events",
        "outbox_events",
    )
    before = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in tables
    }
    try:
        with TestClient(
            app,
            base_url=BASE_URL,
            client=("127.0.0.1", 41000),
            raise_server_exceptions=False,
        ) as client:
            response = client.put(
                "/api/skills/existing-skill",
                headers=HEADERS,
                json={"content": "Updated body."},
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "skill_package_render_invalid"
        after = {
            table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
            for table in tables
        }
        assert after == before
        assert (skill_root / "SKILL.md").read_text("utf-8") == raw_skill
    finally:
        storage.close()


def test_http_update_snapshot_failure_is_stable_and_has_no_persistence(tmp_path: Path) -> None:
    app, storage, live = _app(tmp_path)
    skill_root = live / "existing-skill"
    raw_skill = (
        "---\nname: existing-skill\ndescription: Existing skill\n---\n\nOriginal instructions."
    )
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(raw_skill, encoding="utf-8")
    (skill_root / "assets").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (skill_root / "assets" / "escape.txt").symlink_to(outside)
    tables = (
        "artifact_records",
        "evolution_candidates",
        "evolution_lifecycle_journal",
        "system_audit_events",
        "outbox_events",
    )
    before = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in tables
    }
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            response = client.put(
                "/api/skills/existing-skill",
                headers=HEADERS,
                json={
                    "content": (
                        "---\nname: existing-skill\ndescription: Updated skill\n---\n\nUpdated."
                    )
                },
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "skill_package_snapshot_invalid"
        after = {
            table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
            for table in tables
        }
        assert after == before
        assert (skill_root / "SKILL.md").read_text("utf-8") == raw_skill
        assert outside.read_text("utf-8") == "outside"
    finally:
        storage.close()


def test_http_create_rejects_existing_loader_skill_without_persistence(tmp_path: Path) -> None:
    app, storage, live = _app(tmp_path)
    skill_root = live / "existing-skill"
    raw_skill = (
        "---\nname: existing-skill\ndescription: Existing skill\n---\n\nOriginal instructions."
    )
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(raw_skill, encoding="utf-8")
    tables = (
        "artifact_records",
        "evolution_candidates",
        "evolution_lifecycle_journal",
        "system_audit_events",
        "outbox_events",
    )
    before = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in tables
    }
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            response = client.post(
                "/api/skills",
                headers=HEADERS,
                json={
                    "name": "existing-skill",
                    "content": "---\nname: existing-skill\n---\n\nReplacement.",
                },
            )
        assert response.status_code == 409
        assert response.json()["detail"] == "skill_already_exists"
        after = {
            table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
            for table in tables
        }
        assert after == before
        assert (skill_root / "SKILL.md").read_text("utf-8") == raw_skill
    finally:
        storage.close()
