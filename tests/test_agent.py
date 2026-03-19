"""Tests for Agent execution — mock LLM."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.executor.agent import Agent, AgentResult
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
