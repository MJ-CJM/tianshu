from __future__ import annotations

import ast
import re
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
            or (
                isinstance(expression, ast.Call)
                and (
                    (
                        isinstance(expression.func, ast.Name)
                        and expression.func.id == "CandidateLifecycle"
                    )
                    or (
                        isinstance(expression.func, ast.Attribute)
                        and expression.func.attr == "CandidateLifecycle"
                    )
                )
                and len(expression.args) == 1
                and not expression.keywords
                and governed_value(expression.args[0], aliases)
            )
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
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                lifecycle = next(
                    (item.value for item in node.keywords if item.arg == "lifecycle"), None
                )
                is_candidate_constructor = (
                    isinstance(node.func, ast.Name) and node.func.id == "EvolutionCandidateV1"
                ) or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "EvolutionCandidateV1"
                )
                if (
                    is_candidate_constructor
                    and lifecycle is not None
                    and governed_value(lifecycle, value_aliases)
                ):
                    sites.append(f"{path}:{qualifier}:{node.lineno}")
    return sites


def _writes_candidate_lifecycle_sql(source: str) -> bool:
    return any(
        target == "evolution_candidates" and "lifecycle" in re.findall(r"[a-z_]+", sql.lower())
        for sql, target in _sql_writes(source)
    )


def _sql_writes(source: str) -> list[tuple[str, str]]:
    """Return concrete SQL write statements and their exact target table."""

    writes: list[tuple[str, str]] = []
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
        match = re.search(
            r"\b(?:insert(?:\s+or\s+\w+)?\s+into|update|delete\s+from)\s+"
            r"(?:[a-z_][a-z0-9_]*\.)?[`\"\[]?([a-z_][a-z0-9_]*)",
            sql,
            flags=re.IGNORECASE,
        )
        if match is not None:
            writes.append((sql, match.group(1).lower()))
    return writes


def test_lifecycle_writer_scanner_detects_aliased_model_copy_save_bypass() -> None:
    bypass = """
def bypass(repository, connection, candidate):
    updates = {"lifecycle": CandidateLifecycle.CANARY}
    changed = candidate.model_copy(update=updates)
    return repository.save_candidate(connection, changed, expected_version=candidate.version)
"""
    assert _governed_lifecycle_write_sites(bypass, path="synthetic.py") == ["synthetic.py:bypass:4"]


def test_lifecycle_writer_scanner_detects_constructor_and_enum_call_variants() -> None:
    bypass = """
def enum_call(candidate):
    lifecycle = CandidateLifecycle("canary")
    return candidate.model_copy(update={"lifecycle": lifecycle})

def qualified_constructor(models):
    return models.EvolutionCandidateV1(lifecycle=CandidateLifecycle.PROMOTED)
"""
    assert _governed_lifecycle_write_sites(bypass, path="variants.py") == [
        "variants.py:enum_call:4",
        "variants.py:qualified_constructor:7",
    ]


def test_lifecycle_writer_scanner_ignores_read_only_precondition_keywords() -> None:
    source = """
def validate(candidate):
    return require_candidate(candidate, lifecycle=CandidateLifecycle.CANARY)
"""
    assert _governed_lifecycle_write_sites(source, path="precondition.py") == []


def test_sql_writer_scanner_normalizes_tokens_and_whitespace() -> None:
    bypass = '''connection.execute("""UPDATE
        evolution_candidates
        SET
            lifecycle = ?
        WHERE candidate_id = ?""")'''
    assert _writes_candidate_lifecycle_sql(bypass) is True


def test_sql_writer_scanner_does_not_join_unrelated_statements() -> None:
    source = """
ROUTING_READ = "SELECT * FROM evolution_routing_allocations"
OTHER_WRITE = "UPDATE evolution_candidates SET updated_at=? WHERE candidate_id=?"
"""
    assert not any(target == "evolution_routing_allocations" for _, target in _sql_writes(source))


def test_sql_writer_scanner_detects_raw_aliased_and_formatted_routing_writes() -> None:
    variants = (
        'SQL = r"INSERT INTO evolution_routing_allocations(candidate_id) VALUES (?)"',
        'statement = "UPDATE main.evolution_routing_allocations AS routing SET version=?"',
        'statement = f"DELETE FROM evolution_routing_allocations WHERE candidate_id={value}"',
    )
    assert all(
        any(target == "evolution_routing_allocations" for _, target in _sql_writes(source))
        for source in variants
    )


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
        "src/tianshu/evolution/promotion.py:PromotionService._complete_executor_canary",
        "src/tianshu/evolution/promotion.py:PromotionService.promote",
        "src/tianshu/evolution/promotion.py:PromotionService.rollback",
    }

    sql_writers = []
    for source_path in (ROOT / "src/tianshu").rglob("*.py"):
        source = source_path.read_text(encoding="utf-8")
        if _writes_candidate_lifecycle_sql(source):
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
        if path.name != "migrations.py" and any(
            target == "evolution_routing_allocations" for _, target in _sql_writes(source)
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
