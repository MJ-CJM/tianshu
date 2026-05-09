"""submit_edict tool —— 让助手 LLM 在对话中"颁敕"。

走与 cli ``tianshu edict submit`` / web ``POST /api/edicts`` 同一执行路径：
``save_edict`` → ``save_memorial`` → fire ``edict.submitted``，由 scheduler /
executor 接管后续执行。

权限：
- 需要 persona 把 ``submit_edict`` 加入 ``tools_allowed`` 才能调用；
- 通政司 ``enable_edict_submission`` toggle 控制启动时是否注入到助手 persona。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from croniter import croniter

from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict, EdictSchedule
from tianshu.models.events import make_event
from tianshu.models.memorial import Memorial
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.persona.loader import PersonaLoader
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_VALID_PRIORITIES = ("urgent", "normal", "low")
_VALID_PROFILES = ("foreground", "checkpointed", "background")
_VALID_SCHEDULES = ("immediate", "once", "cron")
_VALID_REVIEW_POLICIES = ("never", "on_failure", "on_flag", "always")


def _parse_run_at(raw: str) -> datetime | None:
    """解析 ISO 8601 字符串到带时区的 datetime；失败返回 None。

    支持形式：
    - ``2026-05-08T11:00:00+08:00``
    - ``2026-05-08T03:00:00Z`` （Python 3.11+ fromisoformat 已支持）
    - ``2026-05-08T11:00:00`` （视作 UTC）
    """
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def register_submit_edict(
    registry: ToolRegistry,
    *,
    storage: "Storage",
    event_bus: "EventBus",
    persona_loader: "PersonaLoader | None" = None,
) -> None:
    """注册 submit_edict tool 到 ToolRegistry。"""

    async def submit_edict(
        goal: str,
        context: str | None = None,
        priority: str = "normal",
        assigned_persona_id: str | None = None,
        title: str | None = None,
        execution_profile: str = "foreground",
        schedule_type: str = "immediate",
        cron_expr: str | None = None,
        run_at: str | None = None,
        timezone: str = "UTC",
        review_policy: str = "never",
        output_format: str | None = None,
        acceptance_rubric: str | None = None,
    ) -> ToolResult:
        if not goal or not goal.strip():
            return error_result("submit_edict: goal 不能为空")
        if priority not in _VALID_PRIORITIES:
            return error_result(
                f"submit_edict: priority 必须是 {'|'.join(_VALID_PRIORITIES)}（实得 {priority}）",
            )
        if execution_profile not in _VALID_PROFILES:
            return error_result(
                f"submit_edict: execution_profile 必须是 "
                f"{'|'.join(_VALID_PROFILES)}（实得 {execution_profile}）",
            )
        if schedule_type not in _VALID_SCHEDULES:
            return error_result(
                f"submit_edict: schedule_type 必须是 "
                f"{'|'.join(_VALID_SCHEDULES)}（实得 {schedule_type}）",
            )
        if review_policy not in _VALID_REVIEW_POLICIES:
            return error_result(
                f"submit_edict: review_policy 必须是 "
                f"{'|'.join(_VALID_REVIEW_POLICIES)}（实得 {review_policy}）",
            )
        if (
            assigned_persona_id
            and persona_loader is not None
            and persona_loader.get(assigned_persona_id) is None
        ):
            return error_result(
                f"submit_edict: persona '{assigned_persona_id}' 不存在",
            )

        schedule = EdictSchedule(type="immediate", timezone=timezone)
        if schedule_type == "cron":
            if not cron_expr or not cron_expr.strip():
                return error_result(
                    "submit_edict: schedule_type=cron 时必须提供 cron_expr "
                    "（标准 5 段 cron 表达式，如 '0 11 * * *' 表每日 11 点）",
                )
            if not croniter.is_valid(cron_expr):
                return error_result(
                    f"submit_edict: cron_expr 不是合法 cron 表达式（实得 {cron_expr!r}）",
                )
            schedule = EdictSchedule(
                type="cron", cron=cron_expr, timezone=timezone,
            )
        elif schedule_type == "once":
            if not run_at:
                return error_result(
                    "submit_edict: schedule_type=once 时必须提供 run_at "
                    "（ISO 8601 datetime，如 '2026-05-09T11:00:00+08:00'）",
                )
            parsed = _parse_run_at(run_at)
            if parsed is None:
                return error_result(
                    f"submit_edict: run_at 不是合法 ISO 8601 datetime（实得 {run_at!r}）",
                )
            schedule = EdictSchedule(
                type="once", at=parsed, timezone=timezone,
            )

        acceptance: AcceptanceCriteria | None = None
        if acceptance_rubric and acceptance_rubric.strip():
            acceptance = AcceptanceCriteria(
                checks=[
                    CheckSpec(
                        kind="rubric",
                        name="assistant_rubric",
                        rubric=acceptance_rubric.strip(),
                    ),
                ],
            )

        edict_title = title or (goal[:20] + "…" if len(goal) > 20 else goal)
        edict_kwargs: dict = {
            "title": edict_title,
            "goal": goal,
            "context": context,
            "submitter": "assistant",
            "priority": priority,
            "execution_profile": execution_profile,
            "schedule": schedule,
            "review_policy": review_policy,
        }
        if output_format and output_format.strip():
            edict_kwargs["output_format"] = output_format.strip()
        if acceptance is not None:
            edict_kwargs["acceptance"] = acceptance
        if assigned_persona_id:
            edict_kwargs["assigned_persona_id"] = assigned_persona_id
        edict = Edict(**edict_kwargs)
        storage.save_edict(edict)

        memorial = Memorial(
            edict_id=edict.id, instruction=edict.goal,
            status=TaskStatus.SUBMITTED,
        )
        storage.save_memorial(memorial)

        event_bus.fire(make_event(
            "edict.submitted",
            edict_id=edict.id,
            memorial_id=memorial.id,
            producer="submit_edict_tool",
            payload={
                "goal": edict.goal,
                "priority": edict.priority,
                "assigned_persona_id": edict.assigned_persona_id,
                "execution_profile": edict.execution_profile,
                "schedule_type": schedule.type,
                "via": "assistant_tool",
            },
        ))
        logger.info(
            "[tools/submit_edict] new edict=%s goal=%.60s assigned=%s "
            "priority=%s profile=%s schedule=%s",
            edict.id, edict.goal, edict.assigned_persona_id,
            edict.priority, edict.execution_profile, schedule.type,
        )

        profile_hint = {
            "foreground": "短任务",
            "checkpointed": "中等任务（带检查点）",
            "background": "长任务（后台）",
        }.get(execution_profile, execution_profile)
        if schedule.type == "cron":
            schedule_hint = f"，cron={schedule.cron} {schedule.timezone}"
        elif schedule.type == "once":
            schedule_hint = f"，定于 {schedule.at.isoformat()}"
        else:
            schedule_hint = ""
        return ok_result(
            f"已颁敕 #{edict.id[:8]}「{edict_title}」（{profile_hint}{schedule_hint}）",
            details={
                "edict_id": edict.id,
                "title": edict_title,
                "priority": priority,
                "execution_profile": execution_profile,
                "assigned_persona_id": edict.assigned_persona_id,
                "schedule_type": schedule.type,
                "cron_expr": schedule.cron,
                "run_at": schedule.at.isoformat() if schedule.at else None,
                "timezone": schedule.timezone,
                "review_policy": review_policy,
                "output_format": edict.output_format,
                "acceptance_rubric": acceptance_rubric.strip() if acceptance_rubric else None,
            },
        )

    registry.register(
        "submit_edict",
        submit_edict,
        ToolDefinition(
            name="submit_edict",
            description=(
                "颁布一道新敕令（即在系统主任务流中创建一个独立的业务任务）。"
                "用户明确表达'帮我下发/颁布/新建一个敕令/任务'时调用此工具。"
                "敕令进入 scheduler/executor 后会自动经历规划→执行→审计的完整生命周期，"
                "与当前对话独立运行。"
                "若用户要求'每天/每周/定时/到某时再做'，使用 schedule_type=cron 或 once；"
                "默认 schedule_type=immediate（立刻执行一次）。"
                "返回敕令短 ID 给用户做后续追踪。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "敕令目标（一句话清晰说明要做什么）",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选额外上下文（背景、约束、参考资料）",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(_VALID_PRIORITIES),
                        "description": "优先级，默认 normal",
                    },
                    "assigned_persona_id": {
                        "type": "string",
                        "description": (
                            "可选：指派执行人格 id（如 bingbu/wenyuan/ducha）；"
                            "不填由 selector 自动选。"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "可选：自定义标题；不填默认用 goal 前 20 字",
                    },
                    "execution_profile": {
                        "type": "string",
                        "enum": list(_VALID_PROFILES),
                        "description": (
                            "执行模式：foreground=短任务（一次性返回，秒级到分钟级，默认）；"
                            "checkpointed=中等任务（带检查点，可断点续跑）；"
                            "background=长任务（后台执行，分钟级到小时级，例如批量爬取/大型分析）。"
                            "用户说'长任务/后台跑'时选 background，'快速答一下'时选 foreground。"
                        ),
                    },
                    "schedule_type": {
                        "type": "string",
                        "enum": list(_VALID_SCHEDULES),
                        "description": (
                            "调度类型：immediate=立即执行一次（默认）；"
                            "once=未来某个具体时间点执行一次（必须配合 run_at）；"
                            "cron=按 cron 表达式周期重复执行（必须配合 cron_expr）。"
                            "用户说'每天/每周/定时'选 cron；'明天 9 点/下周一'选 once。"
                        ),
                    },
                    "cron_expr": {
                        "type": "string",
                        "description": (
                            "cron 表达式（schedule_type=cron 时必填）。"
                            "标准 5 段格式：'<分> <时> <日> <月> <周>'。"
                            "示例：'0 11 * * *'=每天 11 点；'*/30 * * * *'=每 30 分钟；"
                            "'0 9 * * 1'=每周一 9 点。"
                        ),
                    },
                    "run_at": {
                        "type": "string",
                        "description": (
                            "首次执行时间（schedule_type=once 时必填），ISO 8601 datetime，"
                            "建议带时区，如 '2026-05-09T11:00:00+08:00'；"
                            "不带时区时按 UTC 处理。"
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "调度时区，默认 'UTC'。常用：'Asia/Shanghai'、'UTC'。"
                            "影响 cron 表达式的解释（执行器以此时区计算下次触发时刻）。"
                        ),
                    },
                    "review_policy": {
                        "type": "string",
                        "enum": list(_VALID_REVIEW_POLICIES),
                        "description": (
                            "审阅策略：never=完成自动归档（默认）；"
                            "on_failure=失败才需要人审；"
                            "on_flag=执行人主动 flag 时需人审；"
                            "always=每次完成都等用户批复。"
                            "用户说'跑完给我看一眼/要审一下'选 always；"
                            "'失败再喊我'选 on_failure。"
                        ),
                    },
                    "output_format": {
                        "type": "string",
                        "description": (
                            "可选：成果交付格式描述。"
                            "用户提了'用 markdown 表格 / 用 json / 写成报告'等具体形式时填入；"
                            "会作为 hint 透传给规划器。"
                        ),
                    },
                    "acceptance_rubric": {
                        "type": "string",
                        "description": (
                            "可选：验收标准（一段自然语言 rubric）。"
                            "用户说'必须包含 X / 至少要 Y / 不能出现 Z'等可衡量条件时填入；"
                            "落库为单条 rubric 检查，由监督官按此 rubric 判定通过与否。"
                            "不是简单偏好/语气要求 —— 那种用 context 即可。"
                        ),
                    },
                },
                "required": ["goal"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=1024,
            side_effect=True,
        ),
    )


__all__ = ["register_submit_edict"]
