"""Static guardrails for the governed-apply authority path."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).parents[2]


def _tree(relative: str) -> ast.Module:
    return ast.parse((_ROOT / relative).read_text(encoding="utf-8"))


def test_workspace_gateway_passes_auth_context_without_principal_downgrade() -> None:
    source = (_ROOT / "src/tianshu/gateway/workspace_api.py").read_text(encoding="utf-8")

    assert "get_auth_context(request).principal" not in source
    assert "context = get_auth_context(request)" in source
    assert "run_id,\n            context," in source


def test_workspace_service_receives_decision_service_and_registers_projection_consumer() -> None:
    service = (_ROOT / "src/tianshu/executor/workspace_service.py").read_text(encoding="utf-8")
    wiring = (_ROOT / "src/tianshu/bootstrap/wiring_storage.py").read_text(encoding="utf-8")

    assert "decision_service: DecisionService" in service
    assert "DecisionKind.GOVERNED_APPLY" in service
    assert "workspace_service.governed_apply_projection.v1" in wiring
    assert "handle_decision_resolved" in wiring


def test_projection_consumer_never_calls_apply_or_inspects_git() -> None:
    tree = _tree("src/tianshu/executor/workspace_service.py")
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "handle_decision_resolved"
    )
    called = {
        node.func.attr
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "apply" not in called
    assert "inspect_repository" not in called
    assert "capture_change_set" not in called
