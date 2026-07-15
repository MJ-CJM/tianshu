"""Agent tool hooks receive the exact pre-effect continuation boundary."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.agent import Agent
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.hooks import HookRegistry, HookResult, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.decision import ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ok_result


async def test_before_tool_call_receives_stable_invocation_and_pre_effect_state(
    config_manager,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}
    hooks = HookRegistry()

    async def capture(**context: object) -> HookResult:
        captured.update(context)
        return HookResult(block=True, reason="suspended for review")

    hooks.register(HookType.BEFORE_TOOL_CALL, capture)
    tools = ToolRegistry()
    tools.register(
        "write_file",
        AsyncMock(return_value=ok_result("must not execute")),
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            tier=1,
            side_effect=True,
        ),
    )
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    edict = Edict(goal="persist before effect")
    edict.runtime.max_iterations = 2
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
    first_usage = UsageSummary(
        prompt_tokens=7,
        completion_tokens=3,
        total_tokens=10,
        cache_read_tokens=2,
        cost_cny=0.1,
        actual_model="reasoner-v1",
        upstream_provider="test-provider",
    )
    responses = [
        MagicMock(
            content="I will write the file",
            reasoning_content="reasoning that must round-trip",
            tool_calls=[
                {
                    "id": "tool-call-stable-7",
                    "name": "write_file",
                    "args": json.dumps({"path": "README.md"}),
                }
            ],
            usage=first_usage,
            finish_reason="tool_calls",
        ),
        MagicMock(
            content="stopped after suspension",
            reasoning_content=None,
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        ),
    ]

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=responses))
        result = await agent.execute(edict, memorial=memorial)

    assert result.status is TaskStatus.COMPLETED
    assert captured["invocation_id"] == "tool-call-stable-7"
    assert captured["iteration"] == 0
    assert captured["usage"] == first_usage
    assert captured["tool_args"] == {"path": "README.md"}
    messages = captured["messages"]
    assert isinstance(messages, list)
    assistant = messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["reasoning_content"] == "reasoning that must round-trip"
    assert assistant["tool_calls"] == [
        {
            "id": "tool-call-stable-7",
            "type": "function",
            "function": {"name": "write_file", "arguments": {"path": "README.md"}},
        }
    ]


@pytest.mark.parametrize(
    ("tier", "raw_arguments"),
    (
        (0, "not-json"),
        (0, "[]"),
        (1, "not-json"),
        (1, "[]"),
        pytest.param(1, "9" * 5000, id="oversized-integer"),
        pytest.param(1, "[" * 10000 + "]" * 10000, id="recursive-json"),
    ),
)
async def test_invalid_string_tool_arguments_fail_closed_before_hook_or_effect(
    config_manager,
    tmp_path,
    monkeypatch,
    storage,
    tier: int,
    raw_arguments: str,
) -> None:
    hook_calls = 0

    async def hook(**context: object) -> HookResult:
        nonlocal hook_calls
        del context
        hook_calls += 1
        return HookResult()

    hooks = HookRegistry()
    hooks.register(HookType.BEFORE_TOOL_CALL, hook)
    effect = AsyncMock(return_value=ok_result("must not execute"))
    tools = ToolRegistry()
    tools.register(
        "write_file",
        effect,
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            tier=tier,
            side_effect=True,
        ),
    )
    registry_execute = AsyncMock(wraps=tools.execute)
    monkeypatch.setattr(tools, "execute", registry_execute)
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    edict = Edict(goal="reject malformed arguments")
    edict.runtime.max_iterations = 2
    responses = [
        MagicMock(
            content="invalid tool call",
            reasoning_content=None,
            tool_calls=[
                {
                    "id": "tool-call-invalid-args",
                    "name": "write_file",
                    "args": raw_arguments,
                }
            ],
            usage=UsageSummary(),
            finish_reason="tool_calls",
        ),
        MagicMock(
            content="stopped",
            reasoning_content=None,
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        ),
    ]

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=responses))
        await agent.execute(edict)

    assert hook_calls == 0
    registry_execute.assert_not_awaited()
    effect.assert_not_awaited()
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001


@pytest.mark.parametrize("contains_secret", (False, True))
async def test_json_string_arguments_share_policy_execution_and_durable_mapping(
    config_manager,
    tmp_path,
    storage,
    contains_secret: bool,
) -> None:
    secret = "sk-real-json-string-secret"
    arguments = {"path": "README.md"}
    arguments["api_key" if contains_secret else "content"] = secret if contains_secret else "hello"
    edict = Edict(id=f"edict-json-args-{contains_secret}", goal="execute reviewed mapping")
    edict.runtime.max_iterations = 2
    memorial = Memorial(
        id=f"memorial-json-args-{contains_secret}",
        edict_id=edict.id,
        status=TaskStatus.RUNNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    service = DecisionService(storage)
    manager = ApprovalManager(EventBus(), storage, decision_service=service)
    hooks = HookRegistry()
    captured_policy_args: dict[str, object] = {}
    requested_ids: list[str] = []

    async def approve(**context: object) -> HookResult:
        tool_args = context["tool_args"]
        assert isinstance(tool_args, dict)
        captured_policy_args.update(tool_args)
        request = manager.request_tool_decision(
            edict=edict,
            memorial=memorial,
            invocation_id=str(context["invocation_id"]),
            tool_name=str(context["tool_name"]),
            tool_args=tool_args,
            tool_tier="T1_WORKSPACE",
            policy_rule_id="test-review",
            messages=context["messages"],  # type: ignore[arg-type]
            iteration=int(context["iteration"]),
            usage=context["usage"],  # type: ignore[arg-type]
        )
        requested_ids.append(request.decision_request_id)
        service.resolve(
            request.decision_request_id,
            ResolveDecisionCommand(
                action="approve",
                reason="approved",
                payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
                expected_version=1,
            ),
            auth=AuthContext(
                principal=Principal(
                    id="user:reviewer",
                    kind="human",
                    display_name="Reviewer",
                    scopes=frozenset({"api"}),
                ),
                source="bearer",
                client_kind="api",
                correlation_id="json-string-review",
            ),
        )
        resolution = await manager.wait_for_tool_decision(
            request.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        assert resolution is not None
        return HookResult(authorization_source="policy-engine")

    hooks.register(HookType.BEFORE_TOOL_CALL, approve)
    executed: dict[str, object] = {}

    async def effect(**kwargs: object):
        executed.update(kwargs)
        return ok_result("executed")

    tools = ToolRegistry()
    tools.register(
        "write_file",
        effect,
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={
                "type": "object",
                "properties": {key: {"type": "string"} for key in arguments},
                "required": list(arguments),
            },
            tier=1,
            side_effect=True,
        ),
    )
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    responses = [
        MagicMock(
            content="review this call",
            reasoning_content="preserve reasoning",
            tool_calls=[
                {
                    "id": "real-json-string-call",
                    "name": "write_file",
                    "args": json.dumps(arguments),
                }
            ],
            usage=UsageSummary(total_tokens=3),
            finish_reason="tool_calls",
        ),
        MagicMock(
            content="done",
            reasoning_content=None,
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        ),
    ]

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=responses))
        await agent.execute(edict, memorial=memorial)

    assert captured_policy_args == executed == arguments
    record = service.get(requested_ids[0])
    assert record is not None
    durable_arguments = record.request.payload["arguments"]
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.continuation.pending_tool is not None
    assert state.continuation.pending_tool.arguments == durable_arguments
    assistant = state.continuation.messages[-1]
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0]["function"]["arguments"] == durable_arguments
    if contains_secret:
        assert durable_arguments["api_key"] == "[REDACTED]"
        assert secret not in "".join(storage._conn.iterdump())  # noqa: SLF001
    else:
        assert durable_arguments == executed
