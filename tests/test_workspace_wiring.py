"""Production wiring and shutdown contracts for governed workspaces."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from tianshu.app import create_app, lifespan
from tianshu.bootstrap.wiring_storage import wire_storage
from tianshu.config import TianshuSettings
from tianshu.executor.workspace_runtime import WORKSPACE_MAIN_SOURCE_ID


def _settings(tmp_path: Path) -> TianshuSettings:
    source = tmp_path / "source"
    source.mkdir()
    return TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "tianshu.sqlite3"),
        workspace_dir=str(source),
        workspace_staging_root=str(tmp_path / "staging"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
        log_dir=str(tmp_path / "logs"),
    )


def test_workspace_staging_root_has_a_separate_safe_default() -> None:
    settings = TianshuSettings(_env_file=None)

    assert settings.workspace_staging_root == "~/.tianshu/workspaces"
    assert settings.workspace_staging_root != settings.workspace_dir


@pytest.mark.parametrize("overlap", ["same", "staging_under_source", "source_under_staging"])
def test_workspace_wiring_rejects_overlapping_roots_before_mutation(
    tmp_path: Path,
    overlap: str,
) -> None:
    if overlap == "same":
        source = staging = tmp_path / "shared"
        source.mkdir()
    elif overlap == "staging_under_source":
        source = tmp_path / "source"
        source.mkdir()
        staging = source / "staging"
    else:
        staging = tmp_path / "staging"
        staging.mkdir()
        source = staging / "source"
        source.mkdir()
    protected = source if overlap == "same" else staging
    if protected.exists():
        protected.chmod(0o755)
        original_mode = protected.stat().st_mode & 0o777
    else:
        original_mode = None
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "tianshu.sqlite3"),
        workspace_dir=str(source),
        workspace_staging_root=str(staging),
    )
    app = FastAPI()

    try:
        with pytest.raises(ValueError, match="workspace.*root"):
            wire_storage(app, settings)
    finally:
        storage = getattr(app.state, "storage", None)
        if storage is not None:
            storage.close()

    assert not hasattr(app.state, "workspace_service")
    if original_mode is None:
        assert not protected.exists()
    else:
        assert protected.stat().st_mode & 0o777 == original_mode


@pytest.mark.asyncio
async def test_lifespan_wires_one_workspace_service_and_the_only_source(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)

    async with lifespan(app):
        runtime = app.state.executor._workspace_runtime  # noqa: SLF001

        assert runtime._service is app.state.workspace_service  # noqa: SLF001
        assert runtime._sources == {  # noqa: SLF001
            WORKSPACE_MAIN_SOURCE_ID: Path(settings.workspace_dir).resolve()
        }
        assert (
            app.state.workspace_service._staging_root
            == Path(  # noqa: SLF001
                settings.workspace_staging_root
            ).resolve()
        )


@pytest.mark.asyncio
async def test_lifespan_drains_executor_before_workspace_and_storage_even_on_cleanup_failure(
    tmp_path: Path,
) -> None:
    app = create_app(_settings(tmp_path))
    calls: list[str] = []

    async with lifespan(app):

        async def executor_shutdown() -> None:
            calls.append("executor")

        async def worker_shutdown() -> None:
            calls.append("worker")

        async def workspace_shutdown() -> None:
            calls.append("workspace")
            raise PermissionError("workspace cleanup failed")

        original_storage_close = app.state.storage.close

        def storage_close() -> None:
            calls.append("storage")
            original_storage_close()

        app.state.executor.shutdown = executor_shutdown
        app.state.worker_pool.shutdown = worker_shutdown
        app.state.workspace_service.shutdown = workspace_shutdown
        app.state.storage.close = storage_close

    assert calls == ["executor", "worker", "workspace", "storage"]
