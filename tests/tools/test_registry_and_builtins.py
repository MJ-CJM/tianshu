"""tool_registry 兜底 + read_file 切片 + agent 同错熔断的回归测试。

覆盖 2026-05-09 提交：
- registry.execute 调函数前过滤 schema 未声明的 kwargs（防 LLM 幻觉额外参数 crash）
- read_file 支持 offset/limit 行切片
- agent 连续同错 break 提前终止
"""

from __future__ import annotations

import pytest

from tianshu.models.side_effect import SideEffectSemantics
from tianshu.tools.builtins import register_builtins
from tianshu.tools.registry import ToolDefinition, ToolRegistry, ToolRegistryConflict
from tianshu.tools.types import ok_result


async def _noop_tool() -> object:
    return ok_result("ok")


# ── tool_registry：过滤 schema 外 kwargs ────────────────────────────────────


@pytest.fixture
def workspace_with_file(tmp_path):
    f = tmp_path / "demo.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")
    return tmp_path


@pytest.fixture
def registry(workspace_with_file):
    r = ToolRegistry()
    register_builtins(r, workspace_dir=str(workspace_with_file))
    return r


def test_write_file_declares_replay_safe_managed_effect_semantics(registry) -> None:
    definition = registry.get_definition("write_file")

    assert definition is not None
    assert definition.managed_effect_semantics is SideEffectSemantics.PROVIDER_IDEMPOTENT


@pytest.mark.asyncio
async def test_registry_drops_extra_kwargs_silently(registry, caplog):
    """LLM 传 schema 未声明的额外字段（如 read_file 的 limit/offset 幻觉）
    应该被静默过滤 + warn，而不是抛 TypeError 让 LLM 死循环。"""
    import logging

    caplog.set_level(logging.WARNING)
    # 注意：read_file 现在真的支持 offset/limit 了，所以用一个明确不存在的字段
    result = await registry.execute(
        "read_file",
        {"path": "demo.txt", "totally_made_up_param": "garbage"},
    )
    assert result.is_error is False
    assert "line 1" in result.content
    # 应该 log warning 把 LLM 在乱传什么字段透出来
    assert any("totally_made_up_param" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_registry_keeps_declared_kwargs(registry):
    """schema 声明的字段必须正常透传到函数。"""
    result = await registry.execute(
        "read_file",
        {"path": "demo.txt", "offset": 5, "limit": 3},
    )
    assert result.is_error is False
    assert "line 5" in result.content
    assert "line 7" in result.content
    assert "line 8" not in result.content  # 第 8 行超出 limit


@pytest.mark.asyncio
async def test_registry_binds_and_resets_stable_tool_invocation_id() -> None:
    from tianshu.kernel.ambient import get_current_tool_invocation_id

    seen: list[str | None] = []
    registry = ToolRegistry()

    async def capture() -> object:
        seen.append(get_current_tool_invocation_id())
        return ok_result("captured")

    registry.register(
        "capture_invocation",
        capture,
        ToolDefinition(
            name="capture_invocation",
            description="capture",
            parameters={"type": "object", "properties": {}},
        ),
    )

    result = await registry.execute("capture_invocation", {}, invocation_id="tool-call-42")

    assert result.is_error is False
    assert seen == ["tool-call-42"]
    assert get_current_tool_invocation_id() is None


# ── read_file 切片行为 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_restores_nested_invocation_id_after_tool_error() -> None:
    from tianshu.kernel.ambient import get_current_tool_invocation_id

    seen: list[str | None] = []
    registry = ToolRegistry()

    async def inner() -> object:
        seen.append(get_current_tool_invocation_id())
        return ok_result("inner")

    async def outer() -> object:
        seen.append(get_current_tool_invocation_id())
        await registry.execute("inner", {}, invocation_id="inner-call")
        seen.append(get_current_tool_invocation_id())
        raise RuntimeError("outer failed")

    schema = {"type": "object", "properties": {}}
    registry.register(
        "inner",
        inner,
        ToolDefinition(name="inner", description="inner", parameters=schema),
    )
    registry.register(
        "outer",
        outer,
        ToolDefinition(name="outer", description="outer", parameters=schema),
    )

    result = await registry.execute("outer", {}, invocation_id="outer-call")

    assert result.is_error is True
    assert seen == ["outer-call", "inner-call", "outer-call"]
    assert get_current_tool_invocation_id() is None


def test_registry_keeps_legacy_replace_default_and_tracks_owner() -> None:
    registry = ToolRegistry()

    async def first() -> object:
        return ok_result("first")

    async def second() -> object:
        return ok_result("second")

    definition = ToolDefinition(name="replace", description="replace", parameters={})
    registry.register("replace", first, definition)
    registry.register("replace", second, definition, owner="wiring:replacement")

    assert registry._tools["replace"][1] is second
    assert registry._owners["replace"] == "wiring:replacement"


def test_registry_conflict_is_structured() -> None:
    registry = ToolRegistry()
    definition = ToolDefinition(name="conflict", description="conflict", parameters={})
    registry.register("conflict", _noop_tool, definition, owner="kernel:one")

    with pytest.raises(ToolRegistryConflict) as raised:
        registry.register(
            "conflict",
            _noop_tool,
            definition,
            owner="plugin:two",
            on_conflict="error",
        )

    assert raised.value.name == "conflict"
    assert raised.value.existing_owner == "kernel:one"


@pytest.mark.asyncio
async def test_unregister_clears_tool_owner_disabled_and_receipt_lookup() -> None:
    registry = ToolRegistry()
    definition = ToolDefinition(name="gone", description="gone", parameters={})
    registry.register("gone", _noop_tool, definition, owner="plugin:gone")

    async def receipt_lookup(_provider_key: str) -> object | None:
        return None

    registry.set_managed_receipt_lookup("gone", receipt_lookup)
    registry.disable("gone")

    assert registry.unregister("gone") is True
    assert registry.get_definition("gone") is None
    assert "gone" not in registry._owners
    assert "gone" not in registry._managed_receipt_lookups
    assert registry.is_disabled("gone") is False
    assert registry.unregister("gone") is False
    result = await registry.execute("gone", {})
    assert result.is_error is True
    assert "not found" in result.content


def test_unregister_requires_owner_and_target_identity_independently() -> None:
    registry = ToolRegistry()
    definition = ToolDefinition(name="owned", description="owned", parameters={})
    registry.register("owned", _noop_tool, definition, owner="plugin:owner")

    assert registry.unregister("owned", owner="plugin:other", target=_noop_tool) is False
    assert registry.unregister("owned", owner="plugin:owner", target=object()) is False
    assert registry.is_registered("owned", owner="plugin:owner", target=_noop_tool) is True
    assert registry.unregister("owned", owner="plugin:owner", target=_noop_tool) is True


@pytest.mark.asyncio
async def test_read_file_default_reads_all(registry):
    result = await registry.execute("read_file", {"path": "demo.txt"})
    assert result.is_error is False
    assert "line 1" in result.content
    assert "line 20" in result.content


@pytest.mark.asyncio
async def test_read_file_with_offset_only(registry):
    """offset 单独使用：从某行读到结尾。"""
    result = await registry.execute(
        "read_file",
        {"path": "demo.txt", "offset": 18},
    )
    assert "line 18" in result.content
    assert "line 20" in result.content
    assert "line 17" not in result.content
    assert result.details["lines_returned"] == 3
    assert result.details["total_lines"] == 20


@pytest.mark.asyncio
async def test_read_file_with_limit_only(registry):
    """limit 单独使用：从第 1 行读 N 行。"""
    result = await registry.execute(
        "read_file",
        {"path": "demo.txt", "limit": 2},
    )
    assert "line 1" in result.content
    assert "line 2" in result.content
    assert "line 3" not in result.content


@pytest.mark.asyncio
async def test_read_file_offset_past_end(registry):
    """offset 超过总行数 → 返回空（不报错）。"""
    result = await registry.execute(
        "read_file",
        {"path": "demo.txt", "offset": 999},
    )
    assert result.is_error is False
    assert result.details["lines_returned"] == 0


@pytest.mark.asyncio
async def test_read_file_nonexistent(registry):
    result = await registry.execute("read_file", {"path": "no-such.txt"})
    assert result.is_error is True
    assert "does not exist" in result.content


# ── agent 同错熔断 ──────────────────────────────────────────────────────────


def test_exit_reason_repeated_tool_failure_exists():
    """ExitReason 枚举里应该有 REPEATED_TOOL_FAILURE，供熔断退出使用。"""
    from tianshu.kernel.exit_reason import ExitReason

    assert ExitReason.REPEATED_TOOL_FAILURE == "repeated_tool_failure"
    # 应该是与 LLM_ERROR 区分开的独立 reason
    assert ExitReason.REPEATED_TOOL_FAILURE != ExitReason.LLM_ERROR


@pytest.mark.asyncio
async def test_repeated_tool_failure_triggers_break_logic():
    """直接验证 dict 计数策略 —— 任一签名连续 3 次 → 应触发 break。

    这是熔断机制的核心算法单测，避开 Agent 完整 fixture 的复杂依赖。
    """
    state = {"failures": {}}
    LIMIT = 3

    def record_and_check(tool: str, err: str, is_error: bool) -> bool:
        if is_error:
            key = (tool, err)
            state["failures"][key] = state["failures"].get(key, 0) + 1
            return state["failures"][key] >= LIMIT
        # 成功：清掉该 tool 所有签名
        state["failures"] = {k: v for k, v in state["failures"].items() if k[0] != tool}
        return False

    # 同 tool 同错连续 3 次 → 第 3 次触发
    assert record_and_check("read_file", "TypeError: limit", True) is False
    assert record_and_check("read_file", "TypeError: limit", True) is False
    assert record_and_check("read_file", "TypeError: limit", True) is True


@pytest.mark.asyncio
async def test_success_resets_failure_counter():
    """中间任何一次成功 → 清零计数，避免误杀偶发故障。"""
    state = {"failures": {}}
    LIMIT = 3

    def record(tool: str, err: str, is_error: bool) -> bool:
        if is_error:
            key = (tool, err)
            state["failures"][key] = state["failures"].get(key, 0) + 1
            return state["failures"][key] >= LIMIT
        state["failures"] = {k: v for k, v in state["failures"].items() if k[0] != tool}
        return False

    assert record("read_file", "err A", True) is False
    assert record("read_file", "err A", True) is False
    # 中间一次成功
    assert record("read_file", "", False) is False
    # 计数已清，再连 2 次同错不该触发
    assert record("read_file", "err A", True) is False
    assert record("read_file", "err A", True) is False
    # 第 3 次才触发
    assert record("read_file", "err A", True) is True


def test_agent_accepts_provider_callable():
    """Agent.__init__ 应接受 assistant_persona_id_provider 参数（callable，可返回 None）。"""
    from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
    from tianshu.executor.agent import Agent

    cm = ConfigManager(
        initial=LLMConfigState(name="x", model="x", api_key="x", api_base=""),
        agent_config=AgentConfigState(),
    )
    provider_calls = []

    def provider() -> str | None:
        provider_calls.append(1)
        return "wym"

    agent = Agent(
        config_manager=cm,
        tools=ToolRegistry(),
        skills=None,
        assistant_persona_id_provider=provider,
    )
    # 字段持久化
    assert agent._assistant_persona_id_provider is provider
    # 调用 provider 应正常工作
    assert agent._assistant_persona_id_provider() == "wym"
    assert len(provider_calls) == 1


def test_agent_accepts_no_provider_default_none():
    """provider 参数可选，老调用方不传也能创建 Agent（向后兼容）。"""
    from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
    from tianshu.executor.agent import Agent

    cm = ConfigManager(
        initial=LLMConfigState(name="x", model="x", api_key="x", api_base=""),
        agent_config=AgentConfigState(),
    )
    agent = Agent(config_manager=cm, tools=ToolRegistry(), skills=None)
    assert agent._assistant_persona_id_provider is None


def test_assistant_only_tools_constant_includes_edict_group():
    """ASSISTANT_ONLY_TOOLS 常量必须含 4 个颁敕辅助工具，防执行 persona 递归颁敕。"""
    from tianshu.executor.agent import ASSISTANT_ONLY_TOOLS

    assert "submit_edict" in ASSISTANT_ONLY_TOOLS
    assert "list_edicts" in ASSISTANT_ONLY_TOOLS
    assert "get_edict_status" in ASSISTANT_ONLY_TOOLS
    assert "list_personas" in ASSISTANT_ONLY_TOOLS
    # 必须是 frozenset / 不可变（防运行时被意外修改）
    import collections.abc

    assert not isinstance(ASSISTANT_ONLY_TOOLS, collections.abc.MutableSet)


def _filter_assistant_only(
    tools: list[dict], persona_id: str | None, assistant_id: str | None
) -> list[dict]:
    """复制 agent.execute() 里的过滤逻辑核心 —— 单测算法不要全 fixture。"""
    from tianshu.executor.agent import ASSISTANT_ONLY_TOOLS

    if not assistant_id or persona_id == assistant_id:
        return tools
    return [t for t in tools if t.get("function", {}).get("name") not in ASSISTANT_ONLY_TOOLS]


def test_filter_keeps_all_for_assistant_persona():
    """助手 persona 应看到全部工具，包括 submit_edict 等。"""
    tools = [
        {"function": {"name": "submit_edict"}},
        {"function": {"name": "list_personas"}},
        {"function": {"name": "read_file"}},
    ]
    out = _filter_assistant_only(tools, "wym", "wym")
    assert len(out) == 3


def test_filter_strips_edict_tools_from_executor_persona():
    """非助手 persona（被指派执行的 tbh/wy/...）不该看到颁敕工具。"""
    tools = [
        {"function": {"name": "submit_edict"}},
        {"function": {"name": "list_personas"}},
        {"function": {"name": "list_edicts"}},
        {"function": {"name": "get_edict_status"}},
        {"function": {"name": "read_file"}},
        {"function": {"name": "shell_exec"}},
    ]
    out = _filter_assistant_only(tools, "tbh", "wym")
    names = [t["function"]["name"] for t in out]
    assert "submit_edict" not in names  # 防递归颁敕
    assert "list_personas" not in names
    assert "list_edicts" not in names
    assert "get_edict_status" not in names
    assert "read_file" in names  # 普通工具保留
    assert "shell_exec" in names


def test_filter_no_assistant_id_keeps_all_tools_backward_compat():
    """provider 返回 None（toggle 未配/老部署）→ 不过滤，保持向后兼容。"""
    tools = [
        {"function": {"name": "submit_edict"}},
        {"function": {"name": "read_file"}},
    ]
    out = _filter_assistant_only(tools, "wym", None)
    assert len(out) == 2  # 无主张时不动


@pytest.mark.asyncio
async def test_different_errors_dont_accumulate():
    """同 tool 但不同错误签名各自独立计数（避免误判"灵活探索"）。"""
    repeated_failures: dict[tuple[str, str], int] = {}
    LIMIT = 3

    def record(tool: str, err: str) -> bool:
        key = (tool, err)
        repeated_failures[key] = repeated_failures.get(key, 0) + 1
        return repeated_failures[key] >= LIMIT

    # 三次不同错误，各自计数 1，不该触发
    assert record("read_file", "err 1") is False
    assert record("read_file", "err 2") is False
    assert record("read_file", "err 3") is False
    # 同一错误第 3 次才触发
    assert record("read_file", "err 1") is False
    assert record("read_file", "err 1") is True
