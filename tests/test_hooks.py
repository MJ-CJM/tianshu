"""Tests for HookRegistry."""

import pytest

from tianshu.executor.hooks import HookRegistry, HookResult, HookType


class TestHookRegistry:
    @pytest.fixture
    def registry(self):
        return HookRegistry()

    async def test_register_and_run(self, registry):
        async def handler(**ctx):
            return HookResult()

        registry.register(HookType.BEFORE_AGENT_START, handler)
        result = await registry.run(HookType.BEFORE_AGENT_START, edict="test")
        assert result.block is False

    async def test_blocking_hook(self, registry):
        async def blocker(**ctx):
            return HookResult(block=True, reason="Not allowed")

        registry.register(HookType.BEFORE_TOOL_CALL, blocker)
        result = await registry.run(HookType.BEFORE_TOOL_CALL, tool_name="shell")
        assert result.block is True
        assert result.reason == "Not allowed"

    async def test_priority_ordering(self, registry):
        order = []

        async def h1(**ctx):
            order.append(1)

        async def h2(**ctx):
            order.append(2)

        registry.register(HookType.AFTER_TOOL_CALL, h2, priority=200)
        registry.register(HookType.AFTER_TOOL_CALL, h1, priority=100)

        await registry.run(HookType.AFTER_TOOL_CALL)
        assert order == [1, 2]

    async def test_exception_isolated(self, registry):
        async def bad(**ctx):
            raise ValueError("boom")

        async def good(**ctx):
            return HookResult()

        registry.register(HookType.LLM_OUTPUT, bad, priority=1)
        registry.register(HookType.LLM_OUTPUT, good, priority=2)

        result = await registry.run(HookType.LLM_OUTPUT)
        assert result.block is False

    async def test_unregister(self, registry):
        call_count = 0

        async def handler(**ctx):
            nonlocal call_count
            call_count += 1

        registry.register(HookType.AGENT_END, handler)
        await registry.run(HookType.AGENT_END)
        assert call_count == 1

        registry.unregister(HookType.AGENT_END, handler)
        await registry.run(HookType.AGENT_END)
        assert call_count == 1

    async def test_blocking_stops_chain(self, registry):
        async def blocker(**ctx):
            return HookResult(block=True, reason="stop")

        async def after(**ctx):
            return HookResult(modified_args={"x": 1})

        registry.register(HookType.BEFORE_TOOL_CALL, blocker, priority=1)
        registry.register(HookType.BEFORE_TOOL_CALL, after, priority=2)

        result = await registry.run(HookType.BEFORE_TOOL_CALL)
        assert result.block is True
        assert result.modified_args is None

    async def test_no_hooks_registered(self, registry):
        result = await registry.run(HookType.BEFORE_AGENT_START)
        assert result.block is False
