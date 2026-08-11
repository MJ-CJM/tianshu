"""schedule_edict tool 单元测试（用 FakeScheduler 隔离工具分发逻辑）。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.application.edicts import EdictApplicationService
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.schedule_edict import register_schedule_edict


class FakeScheduler:
    def __init__(self) -> None:
        self.scheduled: list = []
        self.calls: list = []
        # 真实 Scheduler.cancel() 返回 bool（job 不存在/已终结时 False）。替身此前
        # 不返回值，与真实签名不符，是「取消假成功」长期没被测出来的原因之一。
        self.cancel_result = True

    async def schedule(self, edict, memorial_id=None):
        self.scheduled.append(edict)
        return "job-" + edict.id[:6]

    async def list_jobs(self):
        return [
            {
                "job_id": "submitted-abc123",
                "edict_id": "01KZ7SF9KTVHFDM36423MTDFNS",
                "schedule_type": "interval",
                "status": "active",
                "next_run": "2026-08-06T01:44:10+00:00",
                "cron_expr": None,
                "interval_seconds": 1800,
                "title": "每 30 分钟伸展提醒",
            }
        ]

    async def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        return self.cancel_result

    async def pause(self, job_id):
        self.calls.append(("pause", job_id))
        return True

    async def resume(self, job_id):
        self.calls.append(("resume", job_id))
        return True

    async def run_now(self, job_id, *, idempotency_key=None):
        self.calls.append(("run_now", job_id, idempotency_key))
        return True


@pytest.fixture
def setup(storage):
    sched = FakeScheduler()
    registry = ToolRegistry()
    register_schedule_edict(
        registry,
        storage=storage,
        scheduler=sched,
        edict_application_service=EdictApplicationService(storage),
    )
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
async def test_create_rejects_recurring_long_running_profile(setup):
    func, storage, _ = setup

    result = await func(
        action="create",
        goal="长时间周期巡检",
        schedule="every 2h",
        execution_profile="background",
    )

    assert result.is_error is True
    assert "周期任务当前仅支持 foreground" in result.content
    assert storage.list_edicts()[1] == 0


@pytest.mark.asyncio
async def test_create_allows_once_long_running_profile(setup):
    func, storage, _ = setup

    result = await func(
        action="create",
        goal="半小时后执行长任务",
        schedule="30m",
        execution_profile="background",
    )

    assert result.is_error is False
    edict = storage.get_edict(result.details["edict_id"])
    assert edict.schedule.type == "once"
    assert edict.execution_profile == "background"


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
    from tianshu.kernel.ambient import bind_tool_invocation_id

    func, _, sched = setup
    await func(action="cancel", job_id="j9")
    await func(action="pause", job_id="j9")
    await func(action="resume", job_id="j9")
    with bind_tool_invocation_id("tool-call-9"):
        await func(action="run_now", job_id="j9")
    assert ("cancel", "j9") in sched.calls
    assert ("pause", "j9") in sched.calls
    assert ("resume", "j9") in sched.calls
    assert ("run_now", "j9", "tool:tool-call-9") in sched.calls


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
        edict_application_service=EdictApplicationService(storage),
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
    register_schedule_edict(
        registry,
        storage=MagicMock(),
        scheduler=FakeScheduler(),
        edict_application_service=MagicMock(),
    )
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


def test_edict_tools_declare_idempotent_side_effect(storage):
    """两个敕令提交入口都必须声明 PROVIDER_IDEMPOTENT，语义不该分家。

    回归（2026-08-05）：schedule_edict 漏声明时，managed_tools 会兜底成
    OPAQUE_CLI（`semantics or OPAQUE_CLI`），转去走"挂起转人工审批"路径，
    助手在对话里一调就报 `side-effect RunState cannot be suspended`，
    整个 execution 失败——用户侧表现为「❌ 执行失败：Managed execution failed」。
    schedule_edict 全部 action 都自带幂等键（create/run_now 传 idempotency_key，
    cancel/pause/resume 置状态，list 只读），本就该按 PROVIDER_IDEMPOTENT 直接执行。
    """
    from tianshu.bus.event_bus import EventBus
    from tianshu.models.side_effect import SideEffectSemantics
    from tianshu.tools.submit_edict import register_submit_edict

    registry = ToolRegistry()
    register_schedule_edict(
        registry,
        storage=storage,
        scheduler=FakeScheduler(),
        edict_application_service=EdictApplicationService(storage),
    )
    register_submit_edict(
        registry,
        storage=storage,
        event_bus=EventBus(),
        edict_application_service=EdictApplicationService(storage),
    )
    for name in ("schedule_edict", "submit_edict"):
        defn = registry.get_definition(name)
        assert defn.side_effect is True, name
        assert defn.managed_effect_semantics is SideEffectSemantics.PROVIDER_IDEMPOTENT, name


async def test_cancel_failure_is_reported_not_swallowed(setup):
    """cancel 失败必须如实报错，不得假成功。

    回归（2026-08-06）：工具原先丢弃 `scheduler.cancel()` 的返回值，无条件回
    「已取消 ✅」。而调用方常误传敕令 ID（对话里可见的是它，job_id 藏在 details
    里而 managed 路径会丢 details），于是 cancel 静默失灵、定时任务照常每 30
    分钟推送，用户以为已经停了——假成功比失败更糟。
    pause/resume/run_now 一直都检查返回值，唯独 cancel 漏了。
    """
    func, _, sched = setup
    sched.cancel_result = False  # job 不存在（例如误传了敕令 ID）
    result = await func(action="cancel", job_id="01KZ7SF9KTVHFDM36423MTDFNS")

    assert result.is_error is True
    assert "无法取消" in result.content
    assert "job_id" in result.content  # 指出正确取法
    assert ("cancel", "01KZ7SF9KTVHFDM36423MTDFNS") in sched.calls


async def test_cancel_success_still_reports_ok(setup):
    func, _, sched = setup
    sched.cancel_result = True
    result = await func(action="cancel", job_id="submitted-abc123")
    assert result.is_error is False
    assert "已取消" in result.content


async def test_list_exposes_job_id_in_content(setup):
    """job_id 必须出现在 content 里，不能只放 details。

    本工具声明了 managed 副作用语义，走 managed 路径时 ToolResult 由 receipt
    重建，只保留 content/is_error——details 会丢失。job_id 只放 details 等于
    对调用方不可见，它就只能拿敕令 ID 去猜。
    """
    func, _, _ = setup
    result = await func(action="list")

    assert result.is_error is False
    assert "submitted-abc123" in result.content
    assert "每 30 分钟伸展提醒" in result.content
    assert "不是敕令 ID" in result.content  # 明确警示，降低误传概率
    assert result.details["jobs"][0]["job_id"] == "submitted-abc123"


async def test_list_empty_is_explicit(setup):
    func, _, sched = setup
    sched.list_jobs = lambda: _empty_jobs()
    result = await func(action="list")
    assert result.is_error is False
    assert "没有定时" in result.content


async def _empty_jobs():
    return []


# --- 派官收口（issue #49）---------------------------------------------------


def _persona(pid: str):
    from tianshu.persona.model import AgentPersona

    return AgentPersona(
        id=pid,
        name=pid,
        department="wenyuan",
        soul_path="/tmp/p/SOUL.md",
        role_path="/tmp/p/ROLE.md",
        memory_path="/tmp/p/MEMORY.md",
    )


def _setup_with_assistant(storage, assistant_id: str | None):
    """本工具刻意对所有官员开放，故派官限制做在工具内部而非 ASSISTANT_ONLY_TOOLS。"""
    registry = ToolRegistry()
    loader = MagicMock()
    loader.get = MagicMock(side_effect=lambda pid: _persona(pid))
    register_schedule_edict(
        registry,
        storage=storage,
        scheduler=FakeScheduler(),
        persona_loader=loader,
        edict_application_service=EdictApplicationService(storage),
        assistant_persona_id_provider=lambda: assistant_id,
    )
    _, func = registry._tools["schedule_edict"]
    return func


@pytest.mark.asyncio
async def test_official_may_schedule_for_self(storage):
    """人人可给自己排差事——本工具对所有官员开放的原意不得被破坏。"""
    from tianshu.kernel.ambient import bind_persona

    func = _setup_with_assistant(storage, "qb")
    with bind_persona(_persona("smg")):
        result = await func(
            action="create", goal="每日自检", schedule="0 9 * * *", assigned_persona_id="smg"
        )
    assert result.is_error is False, result.content


@pytest.mark.asyncio
async def test_official_cannot_schedule_for_another_official(storage):
    """派给他人会洗掉自己的职权契约（#40）：定时敕令按被指派者的宽 ACL 跑。"""
    from tianshu.kernel.ambient import bind_persona

    func = _setup_with_assistant(storage, "qb")
    with bind_persona(_persona("smg")):
        result = await func(
            action="create", goal="替我干活", schedule="30m", assigned_persona_id="bingbu"
        )
    assert result.is_error is True
    assert "只能为自己排定差事" in result.content


@pytest.mark.asyncio
async def test_assistant_may_schedule_for_others(storage):
    """助手代用户派官是正常路径，不得误伤。"""
    from tianshu.kernel.ambient import bind_persona

    func = _setup_with_assistant(storage, "qb")
    with bind_persona(_persona("qb")):
        result = await func(
            action="create", goal="交办司马光", schedule="30m", assigned_persona_id="smg"
        )
    assert result.is_error is False, result.content


@pytest.mark.asyncio
async def test_no_persona_context_unrestricted(storage):
    """无 persona（CLI/API 直调）时不受限——与本机制引入前一致。"""
    func = _setup_with_assistant(storage, "qb")
    result = await func(
        action="create", goal="外部下发", schedule="30m", assigned_persona_id="smg"
    )
    assert result.is_error is False, result.content


@pytest.mark.asyncio
async def test_provider_unavailable_denies_cross_assignment(storage):
    """provider 拿不到助手身份时按最小权限收口——派给他人一律拒绝。

    与 agent 侧执行墙的 fail-open 取向相反：那里放开只是回到"无过滤"的既有
    状态；这里若放开，等于任何官员都能派官，正是本 issue 要堵的洞。
    """
    from tianshu.kernel.ambient import bind_persona

    func = _setup_with_assistant(storage, None)
    with bind_persona(_persona("smg")):
        result = await func(
            action="create", goal="替我干活", schedule="30m", assigned_persona_id="bingbu"
        )
    assert result.is_error is True
