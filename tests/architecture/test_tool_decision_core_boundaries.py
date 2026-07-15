"""Architecture guards for the 3C1 durable tool-decision core boundary."""

import inspect

from tianshu.executor.agent import Agent
from tianshu.executor.policy_hook import PolicyHook


def test_agent_and_policy_core_use_durable_tool_invocation_boundary() -> None:
    agent_source = inspect.getsource(Agent._handle_llm_response)
    policy_source = inspect.getsource(PolicyHook._request_approval)

    for context_key in ("invocation_id=", "messages=", "usage="):
        assert context_key in agent_source
    assert "request_tool_decision" in policy_source
    assert "wait_for_tool_decision" in policy_source
    assert "wait_for_approval" not in policy_source
    assert "._pending" not in policy_source
    assert "._results" not in policy_source


def test_wiring_injects_one_decision_service_and_registers_projection_consumer() -> None:
    from tianshu.bootstrap import wiring_executor

    source = inspect.getsource(wiring_executor)
    assert "decision_service=app.state.decision_service" in source
    assert "approval_manager.tool_decree_projection.v1" in source
    assert 'event_bus.on(\n        "decision.resolved"' in source
