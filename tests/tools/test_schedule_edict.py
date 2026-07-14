"""schedule_edict tool 单元测试（用 FakeScheduler 隔离工具分发逻辑）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.tools.registry import ToolRegistry
from tianshu.tools.schedule_edict import register_schedule_edict


class FakeScheduler:
    def __init__(self) -> None:
        self.scheduled: list = []
        self.calls: list = []

    async def schedule(self, edict, memorial_id=None):
        self.scheduled.append(edict)
        return "job-" + edict.id[:6]

    async def list_jobs(self):
        return [
            {
                "job_id": "j1",
                "edict_id": "e1",
                "schedule_type": "cron",
                "status": "active",
                "next_run": None,
            }
        ]

    async def cancel(self, job_id):
        self.calls.append(("cancel", job_id))

    async def pause(self, job_id):
        self.calls.append(("pause", job_id))
        return True

    async def resume(self, job_id):
        self.calls.append(("resume", job_id))
        return True

    async def run_now(self, job_id):
        self.calls.append(("run_now", job_id))
        return True


@pytest.fixture
def setup(storage):
    sched = FakeScheduler()
    registry = ToolRegistry()
    register_schedule_edict(registry, storage=storage, scheduler=sched)
    _, func = registry._tools["schedule_edict"]
    return func, storage, sched


@pytest.mark.asyncio
async def test_create_interval(setup):
    func, storage, sched = setup
    result = await func(action="create", goal="每两小时巡检", schedule="every 2h")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.schedule.type == "interval"
    assert edict.schedule.interval_seconds == 7200
    assert result.details["schedule_type"] == "interval"
    assert result.details["status"] == "queued"
    assert storage.get_scheduler_job(result.details["job_id"]) is None
    assert sched.scheduled == []


@pytest.mark.asyncio
async def test_create_cron(setup):
    func, storage, _ = setup
    result = await func(action="create", goal="每天9点", schedule="0 9 * * *")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.schedule.type == "cron"
    assert edict.schedule.cron == "0 9 * * *"


@pytest.mark.asyncio
async def test_create_relative_once(setup):
    func, storage, _ = setup
    result = await func(action="create", goal="半小时后提醒", schedule="30m")
    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.schedule.type == "once"
    assert edict.schedule.at is not None


@pytest.mark.asyncio
async def test_create_requires_goal_and_schedule(setup):
    func, _, _ = setup
    assert (await func(action="create", schedule="30m")).is_error is True
    assert (await func(action="create", goal="x")).is_error is True


@pytest.mark.asyncio
async def test_create_invalid_schedule(setup):
    func, _, _ = setup
    result = await func(action="create", goal="x", schedule="not-a-time")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_create_default_action_is_create(setup):
    func, storage, sched = setup
    # action 缺省即 create
    result = await func(goal="默认即创建", schedule="1h")
    assert result.is_error is False
    assert sched.scheduled == []


@pytest.mark.asyncio
async def test_list(setup):
    func, _, _ = setup
    result = await func(action="list")
    assert result.is_error is False
    assert len(result.details["jobs"]) == 1


@pytest.mark.asyncio
async def test_manage_actions_require_job_id(setup):
    func, _, _ = setup
    for act in ("cancel", "pause", "resume", "run_now"):
        assert (await func(action=act)).is_error is True


@pytest.mark.asyncio
async def test_manage_actions_delegate(setup):
    func, _, sched = setup
    await func(action="cancel", job_id="j9")
    await func(action="pause", job_id="j9")
    await func(action="resume", job_id="j9")
    await func(action="run_now", job_id="j9")
    assert ("cancel", "j9") in sched.calls
    assert ("pause", "j9") in sched.calls
    assert ("resume", "j9") in sched.calls
    assert ("run_now", "j9") in sched.calls


@pytest.mark.asyncio
async def test_unknown_action(setup):
    func, _, _ = setup
    result = await func(action="frobnicate")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_create_rejects_unknown_persona(storage):
    sched = FakeScheduler()
    registry = ToolRegistry()
    loader = MagicMock()
    loader.get.return_value = None
    register_schedule_edict(
        registry,
        storage=storage,
        scheduler=sched,
        persona_loader=loader,
    )
    _, func = registry._tools["schedule_edict"]
    result = await func(
        action="create",
        goal="x",
        schedule="30m",
        assigned_persona_id="ghost",
    )
    assert result.is_error is True
    assert "ghost" in result.content


@pytest.mark.asyncio
async def test_any_official_can_use_not_assistant_only(setup):
    """schedule_edict 应可被任何官员使用（不在 ASSISTANT_ONLY_TOOLS 内）；submit_edict 仍受限。"""
    from tianshu.executor.agent import ASSISTANT_ONLY_TOOLS

    assert "schedule_edict" not in ASSISTANT_ONLY_TOOLS
    assert "submit_edict" in ASSISTANT_ONLY_TOOLS


@pytest.mark.asyncio
async def test_inherits_origin_metadata_from_current_edict(setup):
    """create 时继承当前会话敕令的渠道元数据 → 到点结果按来源投递。"""
    from tianshu.kernel.ambient import bind_edict
    from tianshu.models.edict import Edict as _Edict

    func, storage, _ = setup
    chat_edict = _Edict(
        goal="持续对话上下文",
        metadata={
            "channel": "feishu",
            "instance_id": "feishu-default",
            "chat_id": "oc_origin_chat",
            "assistant_chat": True,
        },
    )
    with bind_edict(chat_edict):
        result = await func(action="create", goal="汇报项目状态", schedule="0 9 * * *")
    assert result.is_error is False
    new_edict = storage.get_edict(result.details["edict_id"])
    assert new_edict.metadata.get("chat_id") == "oc_origin_chat"
    assert new_edict.metadata.get("channel") == "feishu"
    assert new_edict.metadata.get("instance_id") == "feishu-default"
    # 不复制非渠道元数据（如 assistant_chat）
    assert "assistant_chat" not in new_edict.metadata


@pytest.mark.asyncio
async def test_no_origin_metadata_when_no_current_edict(setup):
    """无 ambient 敕令（如 web/api 直发）时不附渠道元数据 → 仅 Web/WS 呈现。"""
    func, storage, _ = setup
    result = await func(action="create", goal="x", schedule="30m")
    new_edict = storage.get_edict(result.details["edict_id"])
    assert new_edict.metadata == {}


def test_resolve_delivery_forms():
    """deliver 解析（参考 hermes cronjob 的 deliver 形式）。"""
    from tianshu.tools.schedule_edict import _resolve_delivery

    assert _resolve_delivery(None) == "origin"
    assert _resolve_delivery("origin") == "origin"
    assert _resolve_delivery("local") == {}
    assert _resolve_delivery("web") == {}
    assert _resolve_delivery("feishu") == {"channel": "feishu"}
    assert _resolve_delivery("feishu:oc_abc") == {"channel": "feishu", "chat_id": "oc_abc"}
    assert _resolve_delivery("oc_bare") == {"channel": "feishu", "chat_id": "oc_bare"}
    assert _resolve_delivery("telegram:-100123") == {
        "channel": "telegram",
        "chat_id": "-100123",
    }


@pytest.mark.asyncio
async def test_deliver_explicit_feishu_chat(setup):
    func, storage, _ = setup
    result = await func(
        action="create",
        goal="x",
        schedule="0 9 * * *",
        deliver="feishu:oc_target",
    )
    e = storage.get_edict(result.details["edict_id"])
    assert e.metadata.get("channel") == "feishu"
    assert e.metadata.get("chat_id") == "oc_target"
    assert result.details["deliver"] == "feishu:oc_target"


@pytest.mark.asyncio
async def test_deliver_local_no_push(setup):
    func, storage, _ = setup
    result = await func(
        action="create",
        goal="x",
        schedule="0 9 * * *",
        deliver="local",
    )
    e = storage.get_edict(result.details["edict_id"])
    assert e.metadata == {}
    assert result.details["deliver"] == "仅 Web"


@pytest.mark.asyncio
async def test_deliver_explicit_overrides_origin(setup):
    """显式 deliver 优先于来源继承。"""
    from tianshu.kernel.ambient import bind_edict
    from tianshu.models.edict import Edict as _Edict

    func, storage, _ = setup
    chat_edict = _Edict(goal="chat", metadata={"channel": "feishu", "chat_id": "oc_origin"})
    with bind_edict(chat_edict):
        result = await func(
            action="create",
            goal="x",
            schedule="0 9 * * *",
            deliver="feishu:oc_explicit",
        )
    e = storage.get_edict(result.details["edict_id"])
    assert e.metadata.get("chat_id") == "oc_explicit"


@pytest.mark.asyncio
async def test_schema_and_tier(setup):
    func, _, _ = setup
    registry = ToolRegistry()
    register_schedule_edict(registry, storage=MagicMock(), scheduler=FakeScheduler())
    defn = registry.get_definition("schedule_edict")
    assert defn.tier == 2  # T2_NETWORK
    assert defn.side_effect is True
    props = defn.parameters["properties"]
    assert set(props["action"]["enum"]) == {
        "create",
        "list",
        "cancel",
        "pause",
        "resume",
        "run_now",
    }
    assert defn.parameters["required"] == ["action"]
