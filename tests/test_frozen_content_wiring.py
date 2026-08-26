"""Focused wiring contracts for per-run frozen skill content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

import tianshu.bootstrap.wiring_skills as wiring_skills
from tianshu.bootstrap.wiring_skills import wire_evolution_services, wire_skills_watcher
from tianshu.config import TianshuSettings
from tianshu.evidence.service import ArtifactStore, EvidenceService
from tianshu.executor.capabilities import get_executor_manifest
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.frozen_content import (
    FrozenContentViewsV1,
    FrozenSkillsViewV1,
    frozen_skills_view_digest,
)
from tianshu.storage.facade import Storage


def _settings(tmp_path: Path, **updates: object) -> TianshuSettings:
    values: dict[str, object] = {
        "db_path": str(tmp_path / "frozen-content.db"),
        "artifact_dir": str(tmp_path / "artifacts"),
        "workspace_dir": str(tmp_path / "workspace"),
        "memory_dir": str(tmp_path / "memory"),
        "runtime_personas_dir": str(tmp_path / "personas"),
    }
    values.update(updates)
    return TianshuSettings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def _wire_app(tmp_path: Path, settings: TianshuSettings) -> tuple[FastAPI, Storage]:
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
    wire_evolution_services(app, settings, skill_target=tmp_path / "skills")
    return app, storage


def test_evolution_wiring_late_binds_view_factory_and_promotion_invalidation(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    app, storage = _wire_app(tmp_path, settings)
    frozen_skills = FrozenSkillsViewV1(
        source_digest="a" * 64,
        effective_digest=frozen_skills_view_digest(
            skills={},
            load_all_entries=(),
        ),
        skills={},
    )

    class Loader:
        invalidation_calls = 0

        def invalidate_cache(self) -> None:
            self.invalidation_calls += 1

        def freeze_view(self) -> FrozenSkillsViewV1:
            return frozen_skills

    loader = Loader()
    app.state.skills_loader = loader
    try:
        view_factory = app.state.challenger_router._view_factory  # noqa: SLF001
        assert view_factory is not None
        assert view_factory() == FrozenContentViewsV1(skills=frozen_skills)
        assert app.state.challenger_router._frozen_content_views is True  # noqa: SLF001
        assert app.state.challenger_router._frozen_content_views_enforced is True  # noqa: SLF001

        adapter = app.state.promotion_adapters[CandidateKind.SKILL]
        adapter._invalidate_cache()  # noqa: SLF001
        assert loader.invalidation_calls == 1
    finally:
        storage.close()


def test_promotion_invalidation_remains_wired_when_frozen_views_are_off(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app, storage = _wire_app(tmp_path, settings)

    class Loader:
        invalidation_calls = 0

        def invalidate_cache(self) -> None:
            self.invalidation_calls += 1

    loader = Loader()
    app.state.skills_loader = loader
    try:
        assert app.state.challenger_router._frozen_content_views is False  # noqa: SLF001
        adapter = app.state.promotion_adapters[CandidateKind.SKILL]
        adapter._invalidate_cache()  # noqa: SLF001
        assert loader.invalidation_calls == 1
    finally:
        storage.close()


def test_watcher_receives_on_change_only_when_frozen_views_are_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    constructor_kwargs: list[dict[str, Any]] = []

    class Watcher:
        def __init__(self, _loader: object, **kwargs: Any) -> None:
            constructor_kwargs.append(kwargs)

        def start(self) -> None:
            return None

    monkeypatch.setattr(wiring_skills, "SkillsWatcher", Watcher)
    app = FastAPI()
    app.state.skills_loader = object()

    assert wire_skills_watcher(app, _settings(tmp_path)) is not None
    assert constructor_kwargs.pop() == {}

    assert (
        wire_skills_watcher(
            app,
            _settings(tmp_path, frozen_content_views=True),
        )
        is not None
    )
    enabled_kwargs = constructor_kwargs.pop()
    assert set(enabled_kwargs) == {"on_change"}
    assert callable(enabled_kwargs["on_change"])
