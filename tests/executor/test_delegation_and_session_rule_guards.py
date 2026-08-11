"""两处权限缺口的补漏（issue #48 / #49）。

都是 #40 那轮 17-agent 对抗审查确认、但不由 #45 引入的既有缺陷：

- #48 session rule 缓存无 persona 维度 —— 越级奏请可被他人历史授权湮灭
- #49 可见性过滤不是执行墙 —— 执行官员幻觉出颁敕工具即可洗掉自己的职权契约
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.executor.agent import Agent
from tianshu.executor.policy_hook import PolicyHook
from tianshu.kernel.hooks import HookRegistry, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.persona.model import AgentPersona
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.policy import PolicyContext, PolicyDecision, PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolTier, ok_result


def _persona(pid: str = "smg", **kw) -> AgentPersona:
    base = {
        "id": pid,
        "name": pid,
        "department": "wenyuan",
        "soul_path": "/tmp/p/SOUL.md",
        "role_path": "/tmp/p/ROLE.md",
        "memory_path": "/tmp/p/MEMORY.md",
    }
    return AgentPersona(**{**base, **kw})


class _RecordingRuleStore:
    """总是命中的 session rule store —— 用来证明缓存到底有没有被查。"""

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def find_match(self, tool_name: str, args: dict, edict_id: str | None):
        self.queries.append(tool_name)
        return SimpleNamespace(rule_id="rule-1", scope="edict", source="approval")


class TestSessionRuleSkipsPersonaTier:
    """#48：越级奏请不吃 session rule 缓存。"""

    def _hook(self, store, engine=None):
        return PolicyHook(
            engine=engine or PolicyEngine(rules=[]),
            workspace_root=Path("/tmp/ws"),
            storage=MagicMock(),
            tool_registry=MagicMock(get_definition=MagicMock(return_value=None)),
            session_rule_store=store,
        )

    async def _run(self, hook, verdict_rule_id: str):
        """让引擎固定返回指定 rule_id 的 require_approval。"""

        class _FixedRule:
            rule_id = verdict_rule_id
            priority = 100

            async def evaluate(self, ctx):
                return PolicyDecision(
                    verdict="require_approval", rule_id=verdict_rule_id, reason="x"
                )

        hook._engine = PolicyEngine(rules=[_FixedRule()])
        return await hook.on_before_tool_call(
            tool_name="shell_exec",
            tool_args={"command": "ls"},
            edict=Edict(goal="x"),
            memorial=None,
            iteration=0,
        )

    @pytest.mark.asyncio
    async def test_persona_tier_never_queries_session_rule(self):
        store = _RecordingRuleStore()
        hook = self._hook(store)
        await self._run(hook, "persona_tier")
        assert store.queries == [], "越级奏请查了 session rule——他人历史授权能湮灭它"

    @pytest.mark.asyncio
    async def test_other_rules_still_use_session_rule(self):
        """安全底线：只豁免 persona_tier，其余审批的缓存语义不变。"""
        store = _RecordingRuleStore()
        hook = self._hook(store)
        result = await self._run(hook, "bash_safety")
        assert store.queries == ["shell_exec"]
        assert result is not None and not result.block, "命中缓存应转 allow"


class TestAssistantOnlyExecutionWall:
    """#49：颁敕工具的执行墙，不只是可见性过滤。"""

    def _agent(self, config_manager, tmp_path, effect, assistant_id="qb"):
        tools = ToolRegistry()
        tools.register(
            "submit_edict",
            effect,
            ToolDefinition(
                name="submit_edict",
                description="颁敕",
                parameters={"type": "object", "properties": {"goal": {"type": "string"}}},
                tier=2,
            ),
        )
        return Agent(
            config_manager=config_manager,
            tools=tools,
            skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
            assistant_persona_id_provider=lambda: assistant_id,
        )

    def _responses(self):
        return [
            MagicMock(
                content="颁一道敕令",
                reasoning_content=None,
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "submit_edict",
                        "args": json.dumps({"goal": "帮我做事"}),
                    }
                ],
                usage=UsageSummary(),
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

    @pytest.mark.asyncio
    async def test_execution_persona_cannot_call_hallucinated_submit_edict(
        self, config_manager, tmp_path
    ):
        """执行官员即便"幻觉"出未提供的颁敕工具，也不得真正执行。"""
        effect = AsyncMock(return_value=ok_result("颁敕成功"))
        agent = self._agent(config_manager, tmp_path, effect)
        edict = Edict(goal="业务任务")
        edict.runtime.max_iterations = 2

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=self._responses()))
            await agent.execute(edict, persona=_persona("smg"))

        effect.assert_not_awaited(), "执行官员的颁敕调用必须被拦"

    @pytest.mark.asyncio
    async def test_assistant_can_still_submit_edict(self, config_manager, tmp_path):
        """安全底线：助手是颁敕的正常路径，不得误伤。"""
        effect = AsyncMock(return_value=ok_result("颁敕成功"))
        agent = self._agent(config_manager, tmp_path, effect)
        edict = Edict(goal="助手转派")
        edict.runtime.max_iterations = 2

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=self._responses()))
            await agent.execute(edict, persona=_persona("qb"))

        effect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_provider_keeps_current_behaviour(self, config_manager, tmp_path):
        """未装 provider（CLI/测试）时不拦——与本机制引入前逐字节一致。"""
        effect = AsyncMock(return_value=ok_result("ok"))
        tools = ToolRegistry()
        tools.register(
            "submit_edict",
            effect,
            ToolDefinition(
                name="submit_edict",
                description="颁敕",
                parameters={"type": "object", "properties": {"goal": {"type": "string"}}},
                tier=2,
            ),
        )
        agent = Agent(
            config_manager=config_manager,
            tools=tools,
            skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        )
        edict = Edict(goal="x")
        edict.runtime.max_iterations = 2

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=self._responses()))
            await agent.execute(edict, persona=_persona("smg"))

        effect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_failure_fails_open(self, config_manager, tmp_path):
        """provider 抛错时 fail-open，与可见性过滤的既有语义一致。"""
        effect = AsyncMock(return_value=ok_result("ok"))

        def _boom():
            raise RuntimeError("provider down")

        agent = self._agent(config_manager, tmp_path, effect, assistant_id=None)
        agent._assistant_persona_id_provider = _boom
        edict = Edict(goal="x")
        edict.runtime.max_iterations = 2

        with patch("tianshu.executor.agent.LLMClient") as mock_client:
            mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=self._responses()))
            await agent.execute(edict, persona=_persona("smg"))

        effect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wall_precedes_t0_fast_path(self, config_manager, tmp_path):
        """T0 颁敕辅助工具（list_edicts）同样被拦——快路径会绕过 hook chain。"""
        effect = AsyncMock(return_value=ok_result("edicts"))
        tools = ToolRegistry()
        tools.register(
            "list_edicts",
            effect,
            ToolDefinition(
                name="list_edicts",
                description="列敕令",
                parameters={"type": "object", "properties": {}},
                tier=0,
            ),
        )
        agent = Agent(
            config_manager=config_manager,
            tools=tools,
            skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
            assistant_persona_id_provider=lambda: "qb",
        )
        edict = Edict(goal="x")
        edict.runtime.max_iterations = 2
        responses = [
            MagicMock(
                content="看看敕令",
                reasoning_content=None,
                tool_calls=[{"id": "c1", "name": "list_edicts", "args": "{}"}],
                usage=UsageSummary(),
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
            await agent.execute(edict, persona=_persona("smg"))

        effect.assert_not_awaited(), "T0 快路径绕过 hook chain，执行墙必须先于它"


class TestPersonaTierStillEnforced:
    """确认 #48 的豁免没有把越级奏请本身弄丢。"""

    @pytest.mark.asyncio
    async def test_tier_exceeded_still_requires_approval(self):
        from tianshu.kernel.ambient import bind_persona

        engine = PolicyEngine(rules=build_default_rules())
        ctx = PolicyContext(
            tool_name="mcp_custom_tool",
            tool_tier=ToolTier.T3_WRITE,
            args={},
            edict=Edict(goal="x"),
            memorial=None,
            workspace_root=Path("/tmp/ws"),
            iteration=0,
        )
        with bind_persona(_persona(tool_tier_max=1)):
            decision = await engine.evaluate(ctx)
        assert decision.verdict == "require_approval"
        assert decision.rule_id == "persona_tier"


@pytest.fixture
def _unused_memorial():
    return Memorial(edict_id="e", status=TaskStatus.RUNNING)
