"""Public compatibility routes cannot reconstruct or bypass durable authority."""

from __future__ import annotations

import inspect

from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.edicts_api import _resolve_plan_review, approve_plan, reject_plan
from tianshu.gateway.execution_api import create_decree, submit_tool_decision


def test_http_decision_mutations_do_not_call_legacy_authority_methods() -> None:
    decree_source = inspect.getsource(create_decree)
    plan_source = inspect.getsource(approve_plan) + inspect.getsource(reject_plan)
    tool_source = inspect.getsource(submit_tool_decision)

    assert "submit_decree" not in decree_source
    assert "submit_plan_review_decision" not in plan_source
    assert "list_pending" not in plan_source
    assert "pending_tool_decision_id_for_memorial" not in tool_source
    assert "resolve_tool_decision(" not in tool_source


def test_http_decision_mutations_use_client_bound_id_and_version() -> None:
    for route in (create_decree, _resolve_plan_review, submit_tool_decision):
        source = inspect.getsource(route)
        assert "decision_request_id" in source
        assert "expected_version" in source

    strict_tool_source = inspect.getsource(ApprovalManager.resolve_tool_decision_strict)
    assert "expected_version=expected_version" in strict_tool_source
    assert "existing.request.version" not in strict_tool_source
    assert "except DecisionConflict" not in strict_tool_source
