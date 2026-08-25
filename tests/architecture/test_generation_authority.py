"""Architecture guard for runtime-generation state and pointer authority."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_GENERATION_TABLES = {
    "runtime_generation_releases",
    "runtime_generations",
    "runtime_generation_journal",
    "generation_pointers",
}
_ATTEMPT_GENERATION_BINDING_TABLES = {"run_generation_bindings"}
_REGISTRY_MUTATIONS = {
    "install_generation",
    "update_generation_state",
    "remove_generation",
    "reconcile_generation",
    "reconcile_generation_state",
}
_GENERATION_CONTROLLER_READS = {"status_for_scope"}


def _sql_write_targets(source: str) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value
        elif isinstance(node, ast.JoinedStr):
            sql = "".join(
                value.value
                for value in node.values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
        else:
            continue
        matches = re.finditer(
            r"\b(?:insert(?:\s+or\s+\w+)?\s+into|update|delete\s+from)\s+"
            r"(?:[a-z_][a-z0-9_]*\.)?[`\"\[]?([a-z_][a-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )
        for match in matches:
            targets.add(match.group(1).lower())
    return targets


def _generation_controller_calls(source: str) -> set[str]:
    tree = ast.parse(source)
    aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = {target.id for target in node.targets if isinstance(target, ast.Name)}
            references_controller = any(
                (isinstance(part, ast.Attribute) and part.attr == "generation_controller")
                or (isinstance(part, ast.Constant) and part.value == "generation_controller")
                or (isinstance(part, ast.Name) and part.id in aliases)
                for part in ast.walk(node.value)
            )
            if references_controller and not names <= aliases:
                aliases.update(names)
                changed = True
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and (
            (isinstance(node.func.value, ast.Name) and node.func.value.id in aliases)
            or any(
                isinstance(part, ast.Attribute) and part.attr == "generation_controller"
                for part in ast.walk(node.func.value)
            )
        )
    }


def _registry_mutation_calls(source: str) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _REGISTRY_MUTATIONS
    }


def test_scanner_detects_generation_pointer_write() -> None:
    source = 'connection.execute("UPDATE generation_pointers SET version=?")'
    assert _sql_write_targets(source) == {"generation_pointers"}


def test_scanner_detects_every_write_in_a_multi_statement_string() -> None:
    source = '''connection.executescript("""
        INSERT INTO audit_log VALUES (1);
        UPDATE generation_pointers SET version=2;
        DELETE FROM runtime_generation_journal;
    """)'''
    assert _sql_write_targets(source) == {
        "audit_log",
        "generation_pointers",
        "runtime_generation_journal",
    }


def test_scanner_detects_aliased_generation_controller_mutation() -> None:
    source = """
controller = request.app.state.generation_controller
controller.activate(generation_id)
"""
    assert _generation_controller_calls(source) == {"activate"}


def test_only_generation_repository_writes_generation_tables() -> None:
    writers: dict[str, set[str]] = {}
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        if path.name == "migrations.py":
            continue
        targets = _sql_write_targets(path.read_text(encoding="utf-8")) & _GENERATION_TABLES
        if targets:
            writers[path.relative_to(ROOT).as_posix()] = targets

    assert writers == {
        "src/tianshu/storage/generation_repo.py": _GENERATION_TABLES,
    }


def test_attempt_generation_binding_writes_stay_in_storage_authorities() -> None:
    writers: dict[str, set[str]] = {}
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        if path.name == "migrations.py":
            continue
        targets = (
            _sql_write_targets(path.read_text(encoding="utf-8"))
            & _ATTEMPT_GENERATION_BINDING_TABLES
        )
        if targets:
            writers[path.relative_to(ROOT).as_posix()] = targets

    assert writers == {
        "src/tianshu/storage/edict_repo.py": _ATTEMPT_GENERATION_BINDING_TABLES,
        "src/tianshu/storage/system_snapshot_repo.py": (_ATTEMPT_GENERATION_BINDING_TABLES),
    }


def test_registry_generation_mutations_stay_in_the_controller_and_reconciler() -> None:
    callers: dict[str, set[str]] = {}
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        if path.as_posix().endswith("executor/adapters/__init__.py"):
            continue
        calls = _registry_mutation_calls(path.read_text(encoding="utf-8"))
        if calls:
            callers[path.relative_to(ROOT).as_posix()] = calls

    allowed = {"src/tianshu/executor/generation_controller.py"}
    reconciler = "src/tianshu/evolution/reconciler.py"
    allowed.add(reconciler)
    assert set(callers) == allowed


def test_generation_lifecycle_has_no_http_or_cli_write_surface_in_p3() -> None:
    production_surfaces = [
        *(ROOT / "src/tianshu/gateway").rglob("*.py"),
        *(ROOT / "src/tianshu/cli").rglob("*.py"),
    ]
    offenders = []
    for path in production_surfaces:
        source = path.read_text(encoding="utf-8")
        if "generation_controller" not in source:
            continue
        relative = path.relative_to(ROOT).as_posix()
        calls = _generation_controller_calls(source)
        if relative != "src/tianshu/gateway/keqing_api.py" or not calls <= (
            _GENERATION_CONTROLLER_READS
        ):
            offenders.append(relative)
    assert offenders == []
