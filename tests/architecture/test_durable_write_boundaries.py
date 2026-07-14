"""Static guards for the first durable submission boundary."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SUBMISSION_FILES = (
    _ROOT / "src/tianshu/application/edicts.py",
    _ROOT / "src/tianshu/storage/unit_of_work.py",
    _ROOT / "src/tianshu/storage/outbox_repo.py",
)


def _calls(path: Path) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        if isinstance(node, ast.Call)
    ]


def test_submission_boundary_never_opens_a_sqlite_connection() -> None:
    violations: list[str] = []
    for path in _SUBMISSION_FILES:
        for call in _calls(path):
            if isinstance(call.func, ast.Name) and call.func.id == "connect":
                violations.append(f"{path.name}:{call.lineno}")
            if (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "sqlite3"
                and call.func.attr == "connect"
            ):
                violations.append(f"{path.name}:{call.lineno}")

    assert violations == []


def test_application_service_uses_connection_primitives_not_public_transactions() -> None:
    path = _ROOT / "src/tianshu/application/edicts.py"
    forbidden = {
        call.func.attr
        for call in _calls(path)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in {"save_edict", "save_memorial"}
    }

    assert forbidden == set()
