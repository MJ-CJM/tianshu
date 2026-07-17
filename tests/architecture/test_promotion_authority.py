from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOVERNED_LIFECYCLES = {"CANARY", "PROMOTED", "ROLLBACK_PENDING", "ROLLED_BACK"}


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _governed_lifecycle_write_sites(source: str, *, path: str) -> list[str]:
    """Find direct and locally aliased writes to governed lifecycle values."""

    def governed_value(expression: ast.expr, aliases: set[str]) -> bool:
        return (
            (isinstance(expression, ast.Attribute) and expression.attr in GOVERNED_LIFECYCLES)
            or (
                isinstance(expression, ast.Constant)
                and expression.value in {"canary", "promoted", "rollback_pending", "rolled_back"}
            )
            or (isinstance(expression, ast.Name) and expression.id in aliases)
        )

    def governed_update(expression: ast.expr, aliases: set[str]) -> bool:
        if isinstance(expression, ast.Name):
            return expression.id in aliases
        if not isinstance(expression, ast.Dict):
            return False
        return any(
            isinstance(key, ast.Constant)
            and key.value == "lifecycle"
            and governed_value(value, aliases)
            for key, value in zip(expression.keys, expression.values, strict=True)
        )

    sites: list[str] = []
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    functions = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    for function in functions:
        parent = parents.get(function)
        qualifier = (
            f"{parent.name}.{function.name}" if isinstance(parent, ast.ClassDef) else function.name
        )
        value_aliases: set[str] = set()
        update_aliases: set[str] = set()
        for node in sorted(ast.walk(function), key=lambda item: getattr(item, "lineno", 0)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not function:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                if value is not None and governed_value(value, value_aliases):
                    value_aliases.update(names)
                if value is not None and governed_update(value, value_aliases | update_aliases):
                    update_aliases.update(names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "model_copy":
                    update = next(
                        (item.value for item in node.keywords if item.arg == "update"), None
                    )
                    if update is not None and governed_update(
                        update, value_aliases | update_aliases
                    ):
                        sites.append(f"{path}:{qualifier}:{node.lineno}")
                if (
                    node.func.attr == "__setattr__"
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "lifecycle"
                    and governed_value(node.args[1], value_aliases)
                ):
                    sites.append(f"{path}:{qualifier}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                lifecycle = next(
                    (item.value for item in node.keywords if item.arg == "lifecycle"), None
                )
                if lifecycle is not None and governed_value(lifecycle, value_aliases):
                    sites.append(f"{path}:{qualifier}:{node.lineno}")
    return sites


def test_lifecycle_writer_scanner_detects_aliased_model_copy_save_bypass() -> None:
    bypass = """
def bypass(repository, connection, candidate):
    updates = {"lifecycle": CandidateLifecycle.CANARY}
    changed = candidate.model_copy(update=updates)
    return repository.save_candidate(connection, changed, expected_version=candidate.version)
"""
    assert _governed_lifecycle_write_sites(bypass, path="synthetic.py") == ["synthetic.py:bypass:4"]


def test_only_promotion_service_builds_governed_lifecycle_transitions() -> None:
    sites: list[str] = []
    for source_path in (ROOT / "src/tianshu").rglob("*.py"):
        relative = str(source_path.relative_to(ROOT))
        if source_path.name == "migrations.py":
            continue
        sites.extend(
            _governed_lifecycle_write_sites(source_path.read_text(encoding="utf-8"), path=relative)
        )
    authorities = {":".join(site.split(":")[:2]) for site in sites}
    assert authorities == {
        "src/tianshu/evolution/promotion.py:PromotionService.start_canary",
        "src/tianshu/evolution/promotion.py:PromotionService.promote",
        "src/tianshu/evolution/promotion.py:PromotionService.rollback",
    }

    sql_writers = []
    for source_path in (ROOT / "src/tianshu").rglob("*.py"):
        source = source_path.read_text(encoding="utf-8").lower()
        if "update evolution_candidates" in source and "lifecycle" in source:
            sql_writers.append(str(source_path.relative_to(ROOT)))
    assert sql_writers == ["src/tianshu/storage/evolution_repo.py"]


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
