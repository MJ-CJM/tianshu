"""Tests for Agent execution — mock LLM."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.executor.agent import Agent
from tianshu.executor.exit_reason import ExitReason
from tianshu.executor.hooks import HookRegistry
from tianshu.models import Edict, TaskStatus, UsageSummary
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolRegistry


class TestAgent:
    @pytest.fixture
    def tools(self):
        return ToolRegistry()

    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=1000)

    @pytest.fixture
    def agent(self, config_manager, tools, skills):
        return Agent(
            config_manager=config_manager,
            tools=tools,
            skills=skills,
        )

    async def test_execute_simple(self, agent, config_manager):
        edict = Edict(goal="say hello")

        mock_response = MagicMock(
            content="Hello!",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        )

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm

            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.result == "Hello!"
        assert result.usage.total_tokens == 10

    async def test_execute_disabled_llm(self, config_manager, tools, skills):
        config_manager.update(enabled=False)
        agent = Agent(config_manager=config_manager, tools=tools, skills=skills)
        edict = Edict(goal="test")
        result = await agent.execute(edict)
        assert result.status == TaskStatus.FAILED
        assert "disabled" in result.error

    async def test_execute_with_hooks(self, config_manager, tools, skills):
        hooks = HookRegistry()
        agent = Agent(
            config_manager=config_manager,
            tools=tools,
            skills=skills,
            hook_registry=hooks,
        )
        edict = Edict(goal="test")

        mock_response = MagicMock(
            content="Done",
            tool_calls=None,
            usage=UsageSummary(),
        )

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm

            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED


class TestAgentNewLoop:
    """Tests for the redesigned agent loop."""

    @pytest.fixture
    def tools(self):
        return ToolRegistry()

    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=1000)

    @pytest.fixture
    def agent(self, config_manager, tools, skills):
        return Agent(
            config_manager=config_manager,
            tools=tools,
            skills=skills,
        )

    async def test_exit_reason_completed(self, agent):
        edict = Edict(goal="say hello")
        mock_response = MagicMock(
            content="Hello!",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.iteration_count == 0

    async def test_exit_reason_max_iterations(self, agent, config_manager):
        """Agent loops until max_iterations when LLM always returns tool calls."""
        edict = Edict(goal="loop forever")
        edict.runtime.max_iterations = 2

        tool_response = MagicMock(
            content="thinking",
            tool_calls=[{"id": "tc1", "name": "nonexistent", "args": "{}"}],
            usage=UsageSummary(),
            finish_reason="tool_calls",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = tool_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.MAX_ITERATIONS
        assert result.iteration_count == 2

    async def test_exit_reason_cancelled(self, agent):
        edict = Edict(goal="test cancel")
        agent.request_shutdown()

        mock_response = MagicMock(
            content=None,
            tool_calls=[{"id": "tc1", "name": "grep", "args": "{}"}],
            usage=UsageSummary(),
            finish_reason="tool_calls",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.exit_reason == ExitReason.CANCELLED

    async def test_output_truncation_recovery(self, agent):
        """When finish_reason='length', agent injects continuation and retries."""
        edict = Edict(goal="long output")
        truncated = MagicMock(
            content="partial...",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            finish_reason="length",
        )
        complete = MagicMock(
            content="...rest of answer",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=15, completion_tokens=15, total_tokens=30),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = [truncated, complete]
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.recovery_attempts.get("output_continuation", 0) >= 1

    async def test_compact_count_tracked(self, agent):
        edict = Edict(goal="test")
        mock_response = MagicMock(
            content="Done",
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.compact_count == 0
        assert isinstance(result.recovery_attempts, dict)


class TestAgentIntegration:
    """Integration tests for the full loop with tools + compaction."""

    @pytest.fixture
    def tools(self):
        registry = ToolRegistry()
        from tianshu.tools.registry import ToolDefinition
        from tianshu.tools.types import ok_result

        async def mock_grep(**kwargs):
            return ok_result("line1: match\nline2: match\n" + "x" * 500)

        registry.register(
            "grep",
            mock_grep,
            ToolDefinition(
                name="grep",
                description="Search",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                },
            ),
        )
        return registry

    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=0)

    @pytest.fixture
    def agent(self, config_manager, tools, skills):
        return Agent(config_manager=config_manager, tools=tools, skills=skills)

    async def test_multi_turn_with_tool_calls(self, agent):
        """Test multi-turn agent loop with tool calls."""
        edict = Edict(goal="find bugs")
        edict.runtime.max_iterations = 5

        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return MagicMock(
                    content=f"thinking step {call_count}",
                    tool_calls=[
                        {
                            "id": f"tc{call_count}",
                            "name": "grep",
                            "args": '{"pattern": "bug"}',
                        }
                    ],
                    usage=UsageSummary(
                        prompt_tokens=50, completion_tokens=50, total_tokens=100
                    ),
                    finish_reason="tool_calls",
                )
            return MagicMock(
                content="Found 2 bugs in main.py",
                tool_calls=None,
                usage=UsageSummary(
                    prompt_tokens=100, completion_tokens=100, total_tokens=200
                ),
                finish_reason="stop",
            )

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = lambda *a, **kw: make_response()
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.iteration_count == 2
        assert "2 bugs" in result.summary
