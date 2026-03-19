"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_db()
    yield s
    s.close()


@pytest.fixture
def config_manager():
    initial = LLMConfigState(
        name="test-model",
        model="test-model",
        api_key="test-key",
        api_base="http://localhost:9999",
    )
    agent_cfg = AgentConfigState(
        agent_max_iterations=5,
        agent_timeout_seconds=30,
        skills_char_budget=1000,
    )
    return ConfigManager(initial, agent_config=agent_cfg)


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient that returns a simple text response."""
    mock = AsyncMock()
    from tianshu.models import UsageSummary

    mock.chat.return_value = MagicMock(
        content="Task completed successfully.",
        tool_calls=None,
        usage=UsageSummary(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        reasoning_content=None,
    )
    return mock
