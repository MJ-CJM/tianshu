"""Agent tool hooks receive the exact pre-effect continuation boundary."""

from unittest.mock import AsyncMock, MagicMock, patch

from tianshu.executor.agent import Agent
from tianshu.kernel.hooks import HookRegistry, HookResult, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
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
                    "args": {"path": "README.md"},
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
