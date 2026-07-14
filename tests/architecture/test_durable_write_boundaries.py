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
_EVENT_BUS_PATH = _ROOT / "src/tianshu/bus/event_bus.py"
_EVENT_CONSUMER_FILES = (
    _ROOT / "src/tianshu/bootstrap/wiring_scheduler.py",
    _ROOT / "src/tianshu/gateway/core/outbound.py",
    _ROOT / "src/tianshu/gateway/feishu/approval_card.py",
    _ROOT / "src/tianshu/gateway/telegram/approval_kb.py",
    _ROOT / "src/tianshu/gateway/personas_api.py",
)
_INGRESS_METHODS = {
    _ROOT / "src/tianshu/edict_ops.py": {"submit_new_edict"},
    _ROOT / "src/tianshu/gateway/edicts_api.py": {"create_edict"},
    _ROOT / "src/tianshu/gateway/mcp_server.py": {"submit_edict"},
    _ROOT / "src/tianshu/gateway/core/edict_bridge.py": {"create_new"},
    _ROOT / "src/tianshu/tools/submit_edict.py": {"submit_edict"},
    _ROOT / "src/tianshu/tools/schedule_edict.py": {"_create"},
    _ROOT / "src/tianshu/executor/approvals.py": {"_handle_amend"},
    _ROOT / "src/tianshu/cli/commands/edict.py": {"submit"},
}
_FORBIDDEN_INGRESS_WRITES = {"save_edict", "save_memorial", "append_event"}


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


def test_event_bus_has_no_storage_dependency_or_persistence_calls() -> None:
    tree = ast.parse(_EVENT_BUS_PATH.read_text(encoding="utf-8"), filename=str(_EVENT_BUS_PATH))
    storage_imports = [
        node.lineno
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.startswith("tianshu.storage")
        )
        or (
            isinstance(node, ast.Import)
            and any(alias.name.startswith("tianshu.storage") for alias in node.names)
        )
    ]
    persistence_calls = [
        f"{call.func.attr}:{call.lineno}"
        for call in _calls(_EVENT_BUS_PATH)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in {"append_event", "append_event_envelope", "save_event"}
    ]

    assert storage_imports == []
    assert persistence_calls == []


def test_production_event_consumers_have_explicit_stable_names() -> None:
    unnamed: list[str] = []
    for path in _EVENT_CONSUMER_FILES:
        for call in _calls(path):
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "on":
                continue
            consumer_name = next(
                (keyword.value for keyword in call.keywords if keyword.arg == "consumer_name"),
                None,
            )
            if consumer_name is None or (
                isinstance(consumer_name, ast.Constant) and not str(consumer_name.value).strip()
            ):
                unnamed.append(f"{path.name}:{call.lineno}")

    assert unnamed == []


def test_named_top_level_ingress_methods_do_not_write_storage_directly() -> None:
    violations: list[str] = []
    for path, method_names in _INGRESS_METHODS.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in method_names
        ):
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _FORBIDDEN_INGRESS_WRITES
                ):
                    violations.append(f"{path.name}:{function.name}:{node.func.attr}:{node.lineno}")
                if isinstance(node, ast.Attribute) and node.attr == "_conn":
                    violations.append(f"{path.name}:{function.name}:_conn:{node.lineno}")

    assert violations == []


def test_bot_business_submission_calls_propagate_ingress_identity() -> None:
    paths = (
        _ROOT / "src/tianshu/gateway/core/assistant_branch.py",
        _ROOT / "src/tianshu/gateway/core/edict_branch.py",
        _ROOT / "src/tianshu/gateway/feishu/bot.py",
        _ROOT / "src/tianshu/gateway/telegram/bot.py",
    )
    missing: list[str] = []
    for path in paths:
        for call in _calls(path):
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
                "create_new",
                "continue_or_create",
            }:
                continue
            keywords = {keyword.arg for keyword in call.keywords}
            if "source_message_id" not in keywords:
                missing.append(f"{path.name}:{call.lineno}")

    assert missing == []
