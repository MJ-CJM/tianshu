"""Tests for Agent execution — mock LLM."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.config_manager import LLMConfigState
from tianshu.executor.agent import Agent
from tianshu.kernel.exit_reason import ExitReason
from tianshu.kernel.hooks import HookRegistry, HookResult, HookType
from tianshu.models import Edict, TaskStatus, UsageSummary
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, error_result, ok_result


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

    async def test_execute_uses_governed_model_override(self, agent):
        edict = Edict(goal="use governed model")
        response = MagicMock(
            content="done",
            tool_calls=None,
            usage=UsageSummary(total_tokens=1),
        )

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            llm = AsyncMock()
            llm.chat.return_value = response
            mock_client.return_value = llm
            result = await agent.execute(edict, model_override="governed-model")

        assert result.status == TaskStatus.COMPLETED
        assert mock_client.call_args.kwargs["model"] == "governed-model"

    async def test_provider_manager_receives_governed_model_override(
        self,
        config_manager,
        tools,
        skills,
    ):
        response = MagicMock(
            content="done",
            tool_calls=None,
            usage=UsageSummary(total_tokens=1),
        )
        llm = AsyncMock()
        llm.chat.return_value = response
        provider_manager = MagicMock()
        provider_manager.get_client.return_value = llm
        agent = Agent(
            config_manager=config_manager,
            tools=tools,
            skills=skills,
            provider_manager=provider_manager,
        )

        result = await agent.execute(Edict(goal="pin model"), model_override="governed-model")

        assert result.status == TaskStatus.COMPLETED
        provider_manager.get_client.assert_called_once_with(
            config_name_override=None,
            model_override="governed-model",
        )

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
                    usage=UsageSummary(prompt_tokens=50, completion_tokens=50, total_tokens=100),
                    finish_reason="tool_calls",
                )
            return MagicMock(
                content="Found 2 bugs in main.py",
                tool_calls=None,
                usage=UsageSummary(prompt_tokens=100, completion_tokens=100, total_tokens=200),
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

    async def test_agent_propagates_llm_tool_call_id_to_registry(
        self,
        config_manager,
        skills,
    ):
        from tianshu.kernel.ambient import get_current_tool_invocation_id

        seen: list[str | None] = []
        tools = ToolRegistry()

        async def capture() -> ToolResult:
            seen.append(get_current_tool_invocation_id())
            return ok_result("captured")

        tools.register(
            "capture",
            capture,
            ToolDefinition(
                name="capture",
                description="capture",
                parameters={"type": "object", "properties": {}},
            ),
        )
        agent = Agent(config_manager=config_manager, tools=tools, skills=skills)
        responses = [
            MagicMock(
                content="calling",
                tool_calls=[{"id": "llm-call-7", "name": "capture", "args": "{}"}],
                usage=UsageSummary(),
                finish_reason="tool_calls",
            ),
            MagicMock(
                content="done",
                tool_calls=None,
                usage=UsageSummary(),
                finish_reason="stop",
            ),
        ]
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            MockLLM.return_value = AsyncMock(chat=AsyncMock(side_effect=responses))
            await agent.execute(Edict(goal="capture id"))

        assert seen == ["llm-call-7"]


class TestAgentHighRiskPaths:
    """B4-T1 审查 Important 项：execute() 高风险恢复/熔断/拦截路径的常设回归。"""

    @pytest.fixture
    def tools(self):
        return ToolRegistry()

    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=1000)

    @pytest.fixture
    def agent(self, config_manager, tools, skills):
        return Agent(config_manager=config_manager, tools=tools, skills=skills)

    # --- context-overflow: reactive compact 恢复成功 / 恢复失败 ---

    async def test_context_overflow_reactive_recovery_succeeds_and_retries(self, agent):
        edict = Edict(goal="huge context task")
        success_response = MagicMock(
            content="done after recompact",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            finish_reason="stop",
        )

        async def _recover(state, llm, context_limit):
            return state.with_recovery("reactive_test", list(state.messages))

        with (
            patch("tianshu.executor.agent.LLMClient") as MockLLM,
            patch("tianshu.executor.agent.reactive_compact") as mock_reactive,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = [Exception("context_length_exceeded"), success_response]
            MockLLM.return_value = mock_llm
            mock_reactive.side_effect = _recover

            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.recovery_attempts.get("context_overflow") == 1
        assert mock_llm.chat.call_count == 2

    async def test_context_overflow_reactive_recovery_fails_exits_context_overflow(self, agent):
        edict = Edict(goal="huge context task")

        with (
            patch("tianshu.executor.agent.LLMClient") as MockLLM,
            patch("tianshu.executor.agent.reactive_compact") as mock_reactive,
        ):
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = Exception("context_length_exceeded")
            MockLLM.return_value = mock_llm
            mock_reactive.return_value = None  # 两步恢复均失败

            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.CONTEXT_OVERFLOW
        assert mock_llm.chat.call_count == 1

    # --- fallback 三态 ---

    async def test_fallback_success_continues_after_primary_failure(self, agent, config_manager):
        config_manager.add_config(
            LLMConfigState(
                name="fallback", model="fb-model", api_key="fb-key", api_base="http://fb"
            )
        )
        config_manager.update_agent_config(fallback_llm_config_name="fallback")
        edict = Edict(goal="test fallback success")
        success_response = MagicMock(
            content="fallback answer",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            finish_reason="stop",
        )
        primary_llm = AsyncMock()
        primary_llm.chat.side_effect = RuntimeError("primary down")
        fallback_llm = AsyncMock()
        fallback_llm.chat.return_value = success_response

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            MockLLM.side_effect = [primary_llm, fallback_llm]
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.result == "fallback answer"
        assert result.recovery_attempts.get("fallback") == "fallback"
        assert MockLLM.call_count == 2

    async def test_fallback_also_fails_exits_llm_error(self, agent, config_manager):
        config_manager.add_config(
            LLMConfigState(
                name="fallback", model="fb-model", api_key="fb-key", api_base="http://fb"
            )
        )
        config_manager.update_agent_config(fallback_llm_config_name="fallback")
        edict = Edict(goal="test fallback failure")
        primary_llm = AsyncMock()
        primary_llm.chat.side_effect = RuntimeError("primary down")
        fallback_llm = AsyncMock()
        fallback_llm.chat.side_effect = RuntimeError("fallback down")

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            MockLLM.side_effect = [primary_llm, fallback_llm]
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.LLM_ERROR
        assert "primary down" in result.error
        assert "fallback down" in result.error

    async def test_no_fallback_configured_exits_llm_error(self, agent):
        """agent_config.fallback_llm_config_name 未配置（fixture 默认 None）—— 直接收工。"""
        edict = Edict(goal="test no fallback")

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = RuntimeError("primary down")
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.LLM_ERROR
        assert "fallback" not in result.recovery_attempts
        assert MockLLM.call_count == 1

    # --- 连续同错熔断 ---

    async def test_repeated_same_tool_failure_triggers_circuit_breaker(
        self, config_manager, skills
    ):
        tools = ToolRegistry()

        async def boom_tool(**kwargs):
            return error_result("boom: same failure every time")

        tools.register(
            "boom",
            boom_tool,
            ToolDefinition(
                name="boom",
                description="always fails the same way",
                parameters={"type": "object", "properties": {}},
            ),
        )
        agent = Agent(config_manager=config_manager, tools=tools, skills=skills)
        edict = Edict(goal="trigger circuit breaker")

        # 单条 LLM 响应里连续 3 个同名工具调用，错误签名相同 → 应在第 3 次时熔断
        tool_calls = [{"id": f"tc{i}", "name": "boom", "args": "{}"} for i in range(3)]
        mock_response = MagicMock(
            content="calling boom repeatedly",
            tool_calls=tool_calls,
            usage=UsageSummary(),
            finish_reason="tool_calls",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.REPEATED_TOOL_FAILURE
        assert "boom" in result.error

    # --- before-iteration hook 拦截 ---

    async def test_before_iteration_hook_blocks_execution(self, config_manager, tools, skills):
        hooks = HookRegistry()

        async def _deny(**context):
            return HookResult(block=True, reason="预算已耗尽")

        hooks.register(HookType.BEFORE_ITERATION, _deny)
        agent = Agent(
            config_manager=config_manager, tools=tools, skills=skills, hook_registry=hooks
        )
        edict = Edict(goal="should be blocked")

        with patch("tianshu.executor.agent.LLMClient"):
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.HOOK_BLOCKED
        assert "预算已耗尽" in result.error
        assert result.iteration_count == 0
