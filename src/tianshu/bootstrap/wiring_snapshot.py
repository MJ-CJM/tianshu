"""SystemSnapshot content-source composition."""

from __future__ import annotations

from fastapi import FastAPI

from tianshu import __version__
from tianshu.config import TianshuSettings
from tianshu.evidence.service import dependency_lock_hash
from tianshu.evolution.process_snapshot import (
    ProcessSnapshotBootstrap,
    ProcessSnapshotStartupReport,
)
from tianshu.evolution.system_snapshot import SystemSnapshotResolver
from tianshu.tools.policy_rules import ruleset_digest


def wire_system_snapshot(app: FastAPI, settings: TianshuSettings) -> None:
    """Attach the late-bound SystemSnapshot resolver after all content sources exist."""

    if not settings.system_snapshot_enabled:
        app.state.system_snapshot_resolver = None
        return

    app.state.system_snapshot_resolver = SystemSnapshotResolver(
        kernel_facts=lambda: {
            "dependency_lock_hash": dependency_lock_hash(),
            "tianshu_version": __version__,
        },
        executor_digests=app.state.executor.executor_manifest_digests,
        skills_digest=app.state.skills_loader.content_digest,
        personas_digest=app.state.persona_loader.content_digest,
        policy_rules_digest=ruleset_digest,
        provider_profiles_digest=app.state.model_registry.content_digest,
    )


def initialize_process_snapshot(
    app: FastAPI,
    settings: TianshuSettings,
) -> ProcessSnapshotStartupReport | None:
    """Reconcile the process-wide snapshot before runtime work can start."""

    resolver = app.state.system_snapshot_resolver
    if resolver is None:
        app.state.process_snapshot_report = None
        return None
    report = ProcessSnapshotBootstrap(
        unit_of_work_factory=app.state.storage.unit_of_work,
        resolver=resolver,
        strict=settings.system_snapshot_strict,
        target_digest=settings.system_snapshot_target,
    ).initialize()
    app.state.process_snapshot_report = report
    return report


__all__ = ["initialize_process_snapshot", "wire_system_snapshot"]
