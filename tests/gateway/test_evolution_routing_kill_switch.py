"""Wiring evidence for the global evolution-routing kill switch."""

from pathlib import Path

from fastapi import FastAPI

from tianshu.bootstrap.wiring_skills import (
    initialize_evolution_routing_audit,
    wire_evolution_services,
)
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.executor.capabilities import get_executor_manifest
from tianshu.storage.facade import Storage


def test_disabled_routing_is_unready_and_emits_startup_audit(tmp_path: Path) -> None:
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "routing-disabled.db"),
        artifact_dir=str(tmp_path / "artifacts"),
        workspace_dir=str(tmp_path / "workspace"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
        evolution_routing_enabled=False,
    )
    storage = Storage(settings.db_path)
    storage.init_db()
    app = FastAPI()
    app.state.storage = storage
    app.state.artifact_store = ArtifactStore(
        settings.artifact_dir,
        storage.artifact_repo,
        storage.unit_of_work,
        max_object_bytes=settings.artifact_max_bytes,
        max_total_bytes=settings.artifact_quota_bytes,
    )
    app.state.evidence_service = EvidenceService(
        storage,
        app.state.artifact_store,
        executor_manifest_provider=get_executor_manifest,
    )
    try:
        wire_evolution_services(app, settings, skill_target=tmp_path / "skills")

        assert app.state.evolution_reconciler.readiness_probe() is False
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM system_audit_events"
            ).fetchone()[0]
            == 0
        )
        initialize_evolution_routing_audit(app, settings)
        audit = storage._conn.execute(  # noqa: SLF001
            """SELECT action, outcome, reason_code FROM system_audit_events
               WHERE action='evolution_routing_disabled'"""
        ).fetchall()
        outbox = storage._conn.execute(  # noqa: SLF001
            """SELECT event_type FROM outbox_events
               WHERE event_type='evolution_routing_disabled'"""
        ).fetchall()
        assert [tuple(row) for row in audit] == [
            (
                "evolution_routing_disabled",
                "succeeded",
                "evolution_routing_disabled",
            )
        ]
        assert [tuple(row) for row in outbox] == [("evolution_routing_disabled",)]
    finally:
        storage.close()
