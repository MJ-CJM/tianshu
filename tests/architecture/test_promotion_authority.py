from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_legacy_manager_exposes_no_direct_switch_or_code_promotion_writes() -> None:
    tree = ast.parse(_source("src/tianshu/universe/manager.py"))
    guarded = {"switch", "rollback", "promote_code_variant"}
    methods = {
        node.name: ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in guarded
    }
    assert guarded <= methods.keys()
    for method in methods.values():
        assert "set_universe_status" not in method
        assert "restore_to_live" not in method
        assert ".stage(" not in method


def test_legacy_gateway_evolver_and_cli_have_no_promotion_bypass() -> None:
    gateway = _source("src/tianshu/gateway/universes_api.py")
    evolver = _source("src/tianshu/universe/evolver.py")
    cli = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "src/tianshu/cli").rglob("*.py")
    )
    assert ".switch(" not in gateway
    assert ".promote_code_variant(" not in gateway
    assert ".switch(" not in evolver
    assert ".promote_code_variant(" not in evolver
    assert ".switch(" not in cli
    assert ".promote_code_variant(" not in cli


def test_promotion_service_is_the_only_application_writer_of_routing_allocations() -> None:
    writers: list[str] = []
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if (
            path.name != "migrations.py"
            and "evolution_routing_allocations" in source
            and any(token in source.upper() for token in ("INSERT INTO", "UPDATE ", "DELETE FROM"))
        ):
            writers.append(str(path.relative_to(ROOT)))
    assert writers == ["src/tianshu/evolution/promotion.py"]


def test_evolution_mutations_require_authenticated_promotion_service() -> None:
    source = _source("src/tianshu/gateway/evolution_api.py")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    mutation_calls = [
        call
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in {"start_canary", "promote", "rollback"}
    ]
    assert mutation_calls
    assert all(any(keyword.arg == "auth" for keyword in call.keywords) for call in mutation_calls)


def test_gate_evaluator_is_the_only_production_gate_snapshot_writer() -> None:
    writers: list[str] = []
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        if path.name == "migrations.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "INSERT INTO evolution_gate_snapshots" in source:
            writers.append(str(path.relative_to(ROOT)))
    assert writers == ["src/tianshu/evolution/gates.py"]
