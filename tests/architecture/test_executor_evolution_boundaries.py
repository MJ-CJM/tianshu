"""Static boundaries for governed executor evolution and manifest-only plugins."""

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
_AUTHORITY_TABLES = {
    "executor_generation_authorities",
    "executor_generation_authority_journal",
}
_CANDIDATE_WRITE_TABLES = {
    "evolution_candidates",
    "evolution_candidate_lifecycle_journal",
    "evolution_routing_allocations",
}
_EXACT_GENERATION_EFFECTS = {
    "stage_exact",
    "warm_or_resume",
    "activate_exact",
    "fail_pre_active_exact",
    "rollback_exact",
}
_DYNAMIC_EXECUTION_CALLS = {
    "__import__",
    "eval",
    "exec",
    "exec_module",
    "load_module",
    "module_from_spec",
    "spec_from_file_location",
}


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


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
        targets.update(
            match.group(1).lower()
            for match in re.finditer(
                r"\b(?:insert(?:\s+or\s+\w+)?\s+into|update|delete\s+from)\s+"
                r"(?:[a-z_][a-z0-9_]*\.)?[`\"\[]?([a-z_][a-z0-9_]*)",
                sql,
                flags=re.IGNORECASE,
            )
        )
    return targets


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _method(
    tree: ast.Module,
    class_name: str,
    method_name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    owner = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return tuple(reversed(parts))


def _local_function_closure(
    tree: ast.Module,
    entrypoint: str,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
    functions = _functions(tree)
    pending = [entrypoint]
    selected: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        function = functions[name]
        selected[name] = function
        pending.extend(
            call.func.id
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in functions
        )
    return tuple(selected[name] for name in sorted(selected))


def _literal_dict(node: ast.AST) -> dict[str, object]:
    if not isinstance(node, ast.Dict):
        return {}
    return {
        key.value: value.value
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant)
        and isinstance(key.value, str)
        and isinstance(value, ast.Constant)
    }


def _raised_http_exception(function: ast.AsyncFunctionDef) -> tuple[int, str]:
    body = [
        statement
        for statement in function.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    assert len(body) == 1 and isinstance(body[0], ast.Raise)
    exception = body[0].exc
    assert isinstance(exception, ast.Call)
    assert isinstance(exception.func, ast.Name) and exception.func.id == "HTTPException"
    keywords = {keyword.arg: keyword.value for keyword in exception.keywords}
    status = keywords["status_code"]
    assert isinstance(status, ast.Constant) and type(status.value) is int
    detail = _literal_dict(keywords["detail"])
    assert isinstance(detail.get("code"), str)
    return status.value, detail["code"]


def test_executor_promotion_delegates_generation_effects_without_candidate_writes() -> None:
    relative = "src/tianshu/evolution/adapters/executor_promotion.py"
    source = _source(relative)
    tree = ast.parse(source, filename=relative)

    assert not (_sql_write_targets(source) & (_GENERATION_TABLES | _CANDIDATE_WRITE_TABLES))
    controller_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and "_generation_controller" in ast.unparse(node.func.value)
    }
    assert controller_calls == _EXACT_GENERATION_EFFECTS
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "save_candidate"
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
        and any(
            keyword.arg == "update"
            and isinstance(keyword.value, ast.Dict)
            and any(
                isinstance(key, ast.Constant) and key.value == "lifecycle"
                for key in keyword.value.keys
            )
            for keyword in node.keywords
        )
        for node in ast.walk(tree)
    )


def test_executor_wiring_keeps_rollback_adapter_when_forward_evolution_is_disabled() -> None:
    relative = "src/tianshu/bootstrap/wiring_executor.py"
    tree = _tree(relative)
    function = _functions(tree)["wire_executor"]
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ExecutorPromotionAdapter"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert ast.unparse(keywords["evolution_enabled"]) == "settings.executor_generation_enabled"
    assert not any(
        any(call is nested for nested in ast.walk(conditional))
        for conditional in ast.walk(function)
        if isinstance(conditional, ast.If)
        for call in calls
    )


def test_executor_authority_tables_have_one_non_migration_writer() -> None:
    writers: dict[str, set[str]] = {}
    for path in (ROOT / "src/tianshu").rglob("*.py"):
        if path.name == "migrations.py":
            continue
        targets = _sql_write_targets(path.read_text(encoding="utf-8")) & _AUTHORITY_TABLES
        if targets:
            writers[path.relative_to(ROOT).as_posix()] = targets

    assert writers == {
        "src/tianshu/storage/executor_generation_authority_repo.py": _AUTHORITY_TABLES,
    }


def test_keqing_status_closure_has_only_read_authorities_and_no_drift_proposal() -> None:
    relative = "src/tianshu/gateway/keqing_api.py"
    tree = _tree(relative)
    handler = _functions(tree)["get_keqing_status"]
    assert any(
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
        and decorator.args[0].value == "/keqing/status"
        for decorator in handler.decorator_list
    )

    closure = _local_function_closure(tree, "get_keqing_status")
    closure_source = "\n".join(ast.unparse(function) for function in closure)
    assert _sql_write_targets(closure_source) == set()

    forbidden_calls = {"propose", "propose_candidate", "scan", "scan_once", "scan_and_propose"}
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in forbidden_calls)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls)
        )
        for function in closure
        for node in ast.walk(function)
    )
    forbidden_imports = {"ExecutorDriftScanner", "CandidateService"}
    assert not any(
        (
            isinstance(node, ast.ImportFrom)
            and (
                "executor_drift" in (node.module or "")
                or any(alias.name in forbidden_imports for alias in node.names)
            )
        )
        or (
            isinstance(node, ast.Import)
            and any("executor_drift" in alias.name for alias in node.names)
        )
        for function in closure
        for node in ast.walk(function)
    )

    state_refs: set[str] = set()
    for function in closure:
        for node in ast.walk(function):
            path = _attribute_path(node)
            if path is not None and len(path) >= 4 and path[-3:-1] == ("app", "state"):
                state_refs.add(path[-1])
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and _attribute_path(node.args[0]) is not None
                and _attribute_path(node.args[0])[-2:] == ("app", "state")
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                state_refs.add(node.args[1].value)
    assert state_refs <= {"generation_controller", "storage"}


def test_public_plugin_mutations_remain_unconditional_501_and_catalog_manifest_only() -> None:
    tree = _tree("src/tianshu/gateway/providers_api.py")
    functions = _functions(tree)
    install = functions["install_plugin"]
    activate = functions["update_plugin_status"]
    assert isinstance(install, ast.AsyncFunctionDef)
    assert isinstance(activate, ast.AsyncFunctionDef)
    assert _raised_http_exception(install) == (501, "plugin_install_not_supported")
    assert _raised_http_exception(activate) == (501, "plugin_activation_not_supported")

    projection = functions["_manifest_only_plugin"]
    returns = [node for node in projection.body if isinstance(node, ast.Return)]
    assert len(returns) == 1
    projected = _literal_dict(returns[0].value)
    assert projected == {
        "status": "manifest_only",
        "capability_status": "manifest_only",
        "loaded": False,
    }


def test_third_party_plugin_discovery_never_imports_or_executes_entry_points() -> None:
    loader_tree = _tree("src/tianshu/plugins/loader.py")
    loader = next(
        node
        for node in loader_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "PluginLoader"
    )
    methods = {
        node.name
        for node in loader.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"__init__", "discover", "load_manifest"}

    register = _method(_tree("src/tianshu/plugins/api.py"), "PluginApi", "register_plugin")
    save_calls = [
        node
        for node in ast.walk(register)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "save_plugin"
    ]
    assert len(save_calls) == 1 and save_calls[0].args
    assert _literal_dict(save_calls[0].args[0]).get("status") == "manifest_only"

    wiring = _functions(_tree("src/tianshu/bootstrap/wiring_scheduler.py"))["wire_plugins"]
    loader_calls = {
        node.func.attr
        for node in ast.walk(wiring)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "plugin_loader"
    }
    assert loader_calls == {"discover"}

    guarded_nodes: tuple[ast.AST, ...] = (loader_tree, register, wiring)
    assert not any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in _DYNAMIC_EXECUTION_CALLS)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in _DYNAMIC_EXECUTION_CALLS)
        )
        for guarded in guarded_nodes
        for node in ast.walk(guarded)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "entry_point"
        for guarded in guarded_nodes
        for node in ast.walk(guarded)
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            (
                isinstance(node, ast.Import)
                and any(alias.name.startswith("importlib") for alias in node.names)
            )
            or (isinstance(node, ast.ImportFrom) and (node.module or "").startswith("importlib"))
        )
        for guarded in guarded_nodes
        for node in ast.walk(guarded)
    )
