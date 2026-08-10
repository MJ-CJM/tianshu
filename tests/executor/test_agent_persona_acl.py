"""官员工具 ACL 在真实 agent 循环中生效（issue #40 的集成层）。

单测 PersonaToolRule 时手动 bind_persona 会掩盖一个真实缺陷：agent.py 曾只在
tools.execute 周围绑定 persona，hook chain 在绑定之外运行——规则在判定层拿到
的 persona 恒为 None、永远弃权。本文件用 mock LLM 驱动**真实 Agent 循环 +
真实 PolicyHook + 默认规则链**，专门钉住这条链路。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from tianshu.executor.agent import Agent
from tianshu.executor.policy_hook import PolicyHook
from tianshu.kernel.ambient import get_current_persona
from tianshu.kernel.hooks import HookRegistry, HookResult, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.persona.model import AgentPersona
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.policy import PolicyEngine
from tianshu.tools.policy_rules import build_default_rules
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ok_result


def _persona(**kw) -> AgentPersona:
    base = {
        "id": "smg",
        "name": "司马光",
        "department": "wenyuan",
        "soul_path": "/tmp/p/SOUL.md",
        "role_path": "/tmp/p/ROLE.md",
        "memory_path": "/tmp/p/MEMORY.md",
    }
    return AgentPersona(**{**base, **kw})


def _tools_with(effect) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        "write_file",
        effect,
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            tier=1,
            side_effect=True,
        ),
    )
    return tools


def _responses():
    return [
        MagicMock(
            content="attempt the tool",
            reasoning_content=None,
            tool_calls=[
                {"id": "call-1", "name": "write_file", "args": json.dumps({"path": "a.md"})}
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


async def test_before_tool_hook_sees_bound_persona(config_manager, tmp_path) -> None:
    """回归钉：hook chain 运行期间 ambient persona 必须已绑定。"""
    seen: dict[str, object] = {}
    hooks = HookRegistry()

    async def probe(**context: object) -> HookResult:
        seen["persona"] = get_current_persona()
        return HookResult(block=True, reason="probe done")

    hooks.register(HookType.BEFORE_TOOL_CALL, probe)
    agent = Agent(
        config_manager=config_manager,
        tools=_tools_with(AsyncMock(return_value=ok_result("x"))),
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    edict = Edict(goal="probe persona binding")
    edict.runtime.max_iterations = 2

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=_responses()))
        await agent.execute(edict, persona=_persona())

    assert seen["persona"] is not None, "hook chain 里取不到 persona——ACL 判定层是死代码"
    assert seen["persona"].id == "smg"


async def test_denied_tool_blocked_by_real_policy_hook(config_manager, tmp_path, storage) -> None:
    """全链：mock LLM → Agent → PolicyHook(默认规则链) → persona_tool_acl deny。"""
    effect = AsyncMock(return_value=ok_result("must not run"))
    tools = _tools_with(effect)
    engine = PolicyEngine(rules=build_default_rules())
    policy_hook = PolicyHook(
        engine=engine,
        workspace_root=tmp_path,
        storage=storage,
        tool_registry=tools,
    )
    hooks = HookRegistry()
    hooks.register(HookType.BEFORE_TOOL_CALL, policy_hook.on_before_tool_call)
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    edict = Edict(goal="denied tool must not run")
    edict.runtime.max_iterations = 2
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=_responses()))
        result = await agent.execute(
            edict, memorial=memorial, persona=_persona(tools_denied=["write_file"])
        )

    assert result.status is TaskStatus.COMPLETED
    effect.assert_not_awaited()
    events = storage._conn.execute(  # noqa: SLF001
        "SELECT payload_json FROM events WHERE event_type = 'policy.decision'"
    ).fetchall()
    decisions = [json.loads(row[0]) for row in events]
    assert any(
        d["rule_id"] == "persona_tool_acl" and d["verdict"] == "deny" for d in decisions
    ), decisions


async def test_unconstrained_persona_tool_executes(config_manager, tmp_path, storage) -> None:
    """安全底线：迁移后的默认官员（tier_max=4、空名单）不被本机制拦截。"""
    effect = AsyncMock(return_value=ok_result("ran"))
    tools = _tools_with(effect)
    policy_hook = PolicyHook(
        engine=PolicyEngine(rules=build_default_rules()),
        workspace_root=tmp_path,
        storage=storage,
        tool_registry=tools,
    )
    hooks = HookRegistry()
    hooks.register(HookType.BEFORE_TOOL_CALL, policy_hook.on_before_tool_call)
    agent = Agent(
        config_manager=config_manager,
        tools=tools,
        skills=SkillsLoader(builtin_dir=tmp_path, char_budget=0),
        hook_registry=hooks,
    )
    edict = Edict(goal="unconstrained persona keeps working")
    edict.runtime.max_iterations = 2
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)

    with patch("tianshu.executor.agent.LLMClient") as mock_client:
        mock_client.return_value = AsyncMock(chat=AsyncMock(side_effect=_responses()))
        await agent.execute(edict, memorial=memorial, persona=_persona())

    effect.assert_awaited_once()
