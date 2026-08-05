"""submit_edict tool 单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.application.edicts import EdictApplicationService
from tianshu.bus.event_bus import EventBus
from tianshu.kernel.ambient import bind_edict
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.submit_edict import register_submit_edict


@pytest.fixture
def setup(storage):
    bus = EventBus()
    registry = ToolRegistry()
    register_submit_edict(
        registry,
        storage=storage,
        event_bus=bus,
        edict_application_service=EdictApplicationService(storage),
    )
    return registry, storage, bus


@pytest.mark.asyncio
async def test_submit_edict_creates_edict_and_memorial(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="帮我整理飞书通知模板")
    assert result.is_error is False
    assert "已颁敕" in result.content
    edict_id = result.details["edict_id"]
    edict = storage.get_edict(edict_id)
    assert edict is not None
    assert edict.goal == "帮我整理飞书通知模板"
    assert edict.submitter == "assistant"
    # 颁发即即时执行
    assert edict.schedule.type == "immediate"
    memorials = storage.list_memorials_by_edict(edict_id)
    assert len(memorials) == 1
    assert memorials[0].status == TaskStatus.SUBMITTED


@pytest.mark.asyncio
async def test_submit_edict_enqueues_event(setup):
    registry, storage, bus = setup
    received: list = []

    async def handler(ev):
        received.append(ev)

    bus.on(
        "edict.submitted",
        handler,
        consumer_name="test.edict_submitted.v1",
        priority=200,
    )
    _, func = registry._tools["submit_edict"]
    res = await func(goal="x", priority="urgent")
    row = storage._conn.execute(  # noqa: SLF001 - durable boundary proof
        "SELECT status FROM outbox_events WHERE edict_id = ?",
        (res.details["edict_id"],),
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert received == []


@pytest.mark.asyncio
async def test_submit_edict_rejects_empty_goal(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="")
    assert result.is_error is True
    assert "goal 不能为空" in result.content


@pytest.mark.asyncio
async def test_submit_edict_rejects_invalid_priority(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", priority="critical")
    assert result.is_error is True
    assert "priority" in result.content


@pytest.mark.asyncio
async def test_submit_edict_validates_persona_when_loader_provided(storage):
    bus = EventBus()
    registry = ToolRegistry()
    loader = MagicMock()
    loader.get.return_value = None  # persona 不存在
    register_submit_edict(
        registry,
        storage=storage,
        event_bus=bus,
        persona_loader=loader,
        edict_application_service=EdictApplicationService(storage),
    )
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", assigned_persona_id="ghost_persona")
    assert result.is_error is True
    assert "ghost_persona" in result.content


@pytest.mark.asyncio
async def test_submit_edict_accepts_known_persona(storage):
    bus = EventBus()
    registry = ToolRegistry()
    loader = MagicMock()
    persona = MagicMock()
    persona.id = "bingbu"
    loader.get.return_value = persona
    register_submit_edict(
        registry,
        storage=storage,
        event_bus=bus,
        persona_loader=loader,
        edict_application_service=EdictApplicationService(storage),
    )
    _, func = registry._tools["submit_edict"]
    result = await func(goal="部署灰度", assigned_persona_id="bingbu")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.assigned_persona_id == "bingbu"


@pytest.mark.asyncio
async def test_submit_edict_short_goal_used_as_title(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="部署网关")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.title == "部署网关"  # < 20 字符不截断


@pytest.mark.asyncio
async def test_submit_edict_long_goal_truncated_in_title(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    long_goal = "x" * 50
    result = await func(goal=long_goal)
    edict = storage.get_edict(result.details["edict_id"])
    assert len(edict.title) <= 21  # 20 + ellipsis
    assert edict.title.endswith("…")


@pytest.mark.asyncio
async def test_submit_edict_in_definition(setup):
    """ToolDefinition 字段（schema、tier）正确。"""
    registry, _, _ = setup
    defn = registry.get_definition("submit_edict")
    assert defn is not None
    assert defn.name == "submit_edict"
    schema = defn.parameters
    assert "goal" in schema["properties"]
    assert "execution_profile" in schema["properties"]
    assert schema["required"] == ["goal"]
    assert defn.tier == 2  # T2_NETWORK


@pytest.mark.asyncio
async def test_submit_edict_description_guides_to_list_personas(setup):
    """description 应明确：未指定 assigned_persona_id 时先调 list_personas 决策。"""
    registry, _, _ = setup
    defn = registry.get_definition("submit_edict")
    assert "list_personas" in defn.description
    assert "未指定" in defn.description


@pytest.mark.asyncio
async def test_submit_edict_no_schedule_params(setup):
    """颁发即时化后，submit_edict 不再暴露调度参数（移到 schedule_edict）。"""
    registry, _, _ = setup
    defn = registry.get_definition("submit_edict")
    props = defn.parameters["properties"]
    for removed in ("schedule_type", "cron_expr", "run_at", "timezone"):
        assert removed not in props
    # 描述应引导改用 schedule_edict
    assert "schedule_edict" in defn.description


@pytest.mark.asyncio
async def test_submit_edict_default_profile_foreground(setup):
    """不传 execution_profile 时默认走短任务（foreground）。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "foreground"
    assert "短任务" in result.content


@pytest.mark.asyncio
async def test_submit_edict_background_profile(setup):
    """长任务：execution_profile=background。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="爬取站点", execution_profile="background")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "background"
    assert "长任务" in result.content


@pytest.mark.asyncio
async def test_submit_edict_checkpointed_profile(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", execution_profile="checkpointed")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.execution_profile == "checkpointed"


@pytest.mark.asyncio
async def test_submit_edict_rejects_invalid_profile(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", execution_profile="async")
    assert result.is_error is True
    assert "execution_profile" in result.content


# ── review_policy / output_format / acceptance_rubric ────────────────────────


@pytest.mark.asyncio
async def test_submit_edict_default_review_policy_never(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.review_policy == "never"
    assert result.details["review_policy"] == "never"


@pytest.mark.asyncio
async def test_submit_edict_review_policy_always(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="跑完给我看一眼", review_policy="always")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.review_policy == "always"


@pytest.mark.asyncio
async def test_submit_edict_rejects_invalid_review_policy(setup):
    registry, _, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", review_policy="maybe")
    assert result.is_error is True
    assert "review_policy" in result.content


@pytest.mark.asyncio
async def test_submit_edict_output_format_passthrough(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="出个表", output_format="markdown 表格，列：项目|状态|备注")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.output_format == "markdown 表格，列：项目|状态|备注"


@pytest.mark.asyncio
async def test_submit_edict_blank_output_format_ignored(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x", output_format="   ")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.output_format is None


@pytest.mark.asyncio
async def test_submit_edict_acceptance_rubric_creates_criteria(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(
        goal="写一份周报",
        acceptance_rubric="必须包含本周完成事项、风险点、下周计划三个章节",
    )
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.acceptance is not None
    assert len(edict.acceptance.checks) == 1
    check = edict.acceptance.checks[0]
    assert check.kind == "rubric"
    assert "本周完成事项" in check.rubric


@pytest.mark.asyncio
async def test_submit_edict_no_acceptance_when_rubric_absent(setup):
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.acceptance is None


@pytest.mark.asyncio
async def test_submit_edict_schema_exposes_extended_fields(setup):
    registry, _, _ = setup
    defn = registry.get_definition("submit_edict")
    props = defn.parameters["properties"]
    assert "review_policy" in props
    assert set(props["review_policy"]["enum"]) == {
        "never",
        "on_failure",
        "on_flag",
        "always",
    }
    assert "output_format" in props
    assert "acceptance_rubric" in props


@pytest.mark.asyncio
async def test_submit_edict_inherits_channel_routing_from_caller(setup):
    """助手在渠道对话里颁的敕令须继承 channel/instance_id/chat_id。

    回归（2026-08-04）：缺了这三件套，敕令等于人间蒸发——outbound 按
    metadata.chat_id 反查投递目标，查不到就把成果烂在库里；助手模式 /list
    又按 instance_id 过滤，用户既收不到回禀也查不到这道敕令。
    """
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    caller = Edict(
        title="飞书助手对话",
        goal="持续对话上下文",
        metadata={
            "channel": "feishu",
            "instance_id": "feishu-01KZ3XEK",
            "chat_id": "oc_775068660",
            "assistant_chat": True,
            "feishu_user": "ou_abc",
        },
    )
    with bind_edict(caller):
        result = await func(goal="介绍下你自己")

    edict = storage.get_edict(result.details["edict_id"])
    assert edict.metadata["channel"] == "feishu"
    assert edict.metadata["instance_id"] == "feishu-01KZ3XEK"
    assert edict.metadata["chat_id"] == "oc_775068660"
    # 只继承路由三件套：整份拷贝会带上 assistant_chat，反被 /list 当聊天敕令隐藏
    assert "assistant_chat" not in edict.metadata
    assert "feishu_user" not in edict.metadata
    assert "办讫自动回禀本对话" in result.content


@pytest.mark.asyncio
async def test_submit_edict_without_caller_promises_no_report_back(setup):
    """无发起会话（cron/CLI）时不编造路由，也不许诺回禀。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="x")
    edict = storage.get_edict(result.details["edict_id"])
    assert not (edict.metadata or {}).get("chat_id")
    assert "Web 端查看" in result.content


@pytest.mark.asyncio
async def test_submit_edict_from_chat_is_conversational(setup):
    """人在渠道对话里让助手代颁 → 多轮敕令，过审后不自动结案。

    回归（2026-08-04）：原先硬编码 conversation=False，敕令办成即 auto-close，
    用户 /select 切过去再说话就被 edict_bridge 判为"已结案 → 自动新建"，
    表现为「每次都是新 session、没法继续交互」。
    """
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    caller = Edict(
        title="飞书助手对话",
        goal="持续对话上下文",
        metadata={"channel": "feishu", "instance_id": "feishu-x", "chat_id": "oc_1"},
    )
    with bind_edict(caller):
        result = await func(goal="介绍下你自己")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.runtime.conversation is True


@pytest.mark.asyncio
async def test_submit_edict_without_chat_stays_one_shot(setup):
    """无人在对话那头（cron / 自主 agent）→ 一次性闭环，办成即结案。"""
    registry, storage, _ = setup
    _, func = registry._tools["submit_edict"]
    result = await func(goal="每日巡检")
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.runtime.conversation is False
