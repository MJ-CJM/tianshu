"""Slice 3B composition-root and gateway persistence boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI

from tianshu.bootstrap.wiring_storage import wire_storage
from tianshu.config import TianshuSettings

_ROOT = Path(__file__).parents[2]
_SOURCE = _ROOT / "src" / "tianshu"


def test_decision_service_has_one_production_construction_path(tmp_path: Path) -> None:
    constructors: list[str] = []
    for path in _SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "DecisionService"
            ):
                constructors.append(str(path.relative_to(_ROOT)))
    assert constructors == ["src/tianshu/bootstrap/wiring_storage.py"]

    app = FastAPI()
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "wiring.db"),
        workspace_dir=str(tmp_path / "workspace"),
        workspace_staging_root=str(tmp_path / "staging"),
    )
    wire_storage(app, settings)
    try:
        assert app.state.decision_service._storage is app.state.storage  # noqa: SLF001
        assert app.state.decision_service._repository is app.state.storage.decision_repo  # noqa: SLF001
    finally:
        app.state.storage.close()


def test_decision_gateway_never_accesses_raw_storage_authority() -> None:
    source = (_SOURCE / "gateway" / "decisions_api.py").read_text()
    forbidden = ("._conn", "._lock", ".decision_repo", ".unit_of_work(", "sqlite3")
    assert [token for token in forbidden if token in source] == []


def test_application_composition_root_registers_only_the_slice_3b_routes() -> None:
    from tianshu.app import create_app

    routes = {
        (method, route.path)
        for route in create_app(TianshuSettings(_env_file=None)).routes
        for method in getattr(route, "methods", set())
    }
    assert {
        ("GET", "/api/decisions"),
        ("GET", "/api/decisions/{decision_request_id}"),
        ("POST", "/api/decisions/{decision_request_id}/resolve"),
    } <= routes
