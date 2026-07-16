from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI

from tianshu.bootstrap import wiring_storage
from tianshu.config import TianshuSettings
from tianshu.storage import Storage


@pytest.mark.parametrize("failure_point", ["artifact_store", "workspace_service"])
def test_wire_storage_closes_owned_storage_once_when_setup_fails(
    tmp_path,
    monkeypatch,
    failure_point: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "startup.db"),
        artifact_dir=str(tmp_path / "artifacts"),
        workspace_dir=str(source),
        workspace_staging_root=str(tmp_path / "staging"),
    )
    storage = Storage(settings.db_path)
    close_calls = 0
    real_close = storage.close

    def close() -> None:
        nonlocal close_calls
        close_calls += 1
        real_close()

    def fail(*args, **kwargs):
        raise RuntimeError(f"injected {failure_point} failure")

    monkeypatch.setattr(storage, "close", close)
    monkeypatch.setattr(wiring_storage, "Storage", lambda _: storage)
    target: tuple[str, Callable[..., object]]
    if failure_point == "artifact_store":
        target = ("ArtifactStore", fail)
    else:
        target = ("WorkspaceService", fail)
    monkeypatch.setattr(wiring_storage, *target)

    with pytest.raises(RuntimeError, match=f"injected {failure_point} failure"):
        wiring_storage.wire_storage(FastAPI(), settings)

    assert close_calls == 1
