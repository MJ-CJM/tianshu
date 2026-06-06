"""schedule_edict tool —— 在对话中安排「定时/周期」敕令（钦天监·择期）。

设计参考 hermes 的统一 ``cronjob`` 工具：一个工具用 ``action`` 分发，
覆盖 创建 / 列表 / 取消 / 暂停 / 恢复 / 立即触发。

与 ``submit_edict``（颁发即立即执行）分工明确：
- ``submit_edict``：立刻下发执行一次。
- ``schedule_edict``：到点（once / cron / interval）再下发，每次触发都走完整
  规划→执行→审计管线、并各自产生独立的 memorial（运行记录）。

调度后端复用现有 ``Scheduler``（持久化 / 重启恢复 / cron 循环 / 取消）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.executor.ambient import get_current_edict
from tianshu.models.edict import Edict
from tianshu.scheduler.schedule_spec import parse_spec
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.persona.loader import PersonaLoader
    from tianshu.scheduler.scheduler import Scheduler
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_VALID_PRIORITIES = ("urgent", "normal", "low")
_VALID_PROFILES = ("foreground", "checkpointed", "background")
_VALID_ACTIONS = ("create", "list", "cancel", "pause", "resume", "run_now")
_KNOWN_CHANNELS = ("feishu", "telegram", "dingtalk", "email", "wecom")
_NO_PUSH = ("local", "none", "web")


def _resolve_delivery(deliver: str | None) -> dict | str:
    """把 deliver 解析为新敕令的渠道 metadata（参考 hermes cronjob 的 deliver）。

    返回值：
    - "origin"            → 继承发起会话/敕令的来源渠道（deliver 省略或显式 'origin'）
    - {}                  → 不外发，仅 Web/WS 呈现（'local'/'none'/'web'）
    - {channel[, chat_id]}→ 显式渠道。形式：
        'feishu'                 → 飞书 home_channel（无 chat_id，由 outbound 兜底）
        'feishu:oc_xxx'          → 指定飞书会话
        'telegram:-100123:45'    → 指定 telegram chat（thread 暂存入 chat_id 之后）
        裸 'oc_xxx'              → 默认按飞书会话
    """
    spec = (deliver or "").strip()
    if not spec or spec.lower() == "origin":
        return "origin"
    if spec.lower() in _NO_PUSH:
        return {}
    if ":" in spec:
        ch, _, target = spec.partition(":")
        ch = ch.strip().lower()
        target = target.strip()
        meta: dict = {"channel": ch if ch in _KNOWN_CHANNELS else "feishu"}
        if target:
            meta["chat_id"] = target
        return meta
    if spec.lower() in _KNOWN_CHANNELS:
        return {"channel": spec.lower()}  # home_channel 兜底
    # 裸 id（如 oc_xxx）默认按飞书会话
    return {"channel": "feishu", "chat_id": spec}


def register_schedule_edict(
    registry: ToolRegistry,
    *,
    storage: "Storage",
    scheduler: "Scheduler",
    persona_loader: "PersonaLoader | None" = None,
) -> None:
    """注册 schedule_edict tool 到 ToolRegistry。"""

    async def _create(
        goal: str | None,
        schedule: str | None,
        context: str | None,
        priority: str,
        assigned_persona_id: str | None,
        title: str | None,
        execution_profile: str,
        timezone: str,
        deliver: str | None,
    ) -> ToolResult:
        if not goal or not goal.strip():
            return error_result("schedule_edict: create 需要 goal")
        if not schedule or not schedule.strip():
            return error_result(
                "schedule_edict: create 需要 schedule"
                "（如 '30m' / 'every 2h' / '0 9 * * *' / ISO 时间）",
            )
        if priority not in _VALID_PRIORITIES:
            return error_result(
                f"schedule_edict: priority 必须是 {'|'.join(_VALID_PRIORITIES)}（实得 {priority}）",
            )
        if execution_profile not in _VALID_PROFILES:
            return error_result(
                f"schedule_edict: execution_profile 必须是 "
                f"{'|'.join(_VALID_PROFILES)}（实得 {execution_profile}）",
            )
        if (
            assigned_persona_id
            and persona_loader is not None
            and persona_loader.get(assigned_persona_id) is None
        ):
            return error_result(
                f"schedule_edict: persona '{assigned_persona_id}' 不存在",
            )
        try:
            parsed = parse_spec(schedule, timezone)
        except ValueError as e:
            return error_result(f"schedule_edict: {e}")
        if parsed.type == "immediate":
            return error_result(
                "schedule_edict 仅用于定时/周期任务；要立即执行请改用 submit_edict",
            )

        edict_title = title or (goal[:20] + "…" if len(goal) > 20 else goal)
        edict_kwargs: dict = {
            "title": edict_title,
            "goal": goal,
            "context": context,
            "submitter": "assistant",
            "source": "scheduler",
            "priority": priority,
            "execution_profile": execution_profile,
            "schedule": parsed,
        }
        if assigned_persona_id:
            edict_kwargs["assigned_persona_id"] = assigned_persona_id
        # 送达渠道（deliver，参考 hermes cronjob）：到点 execution.completed 后，
        # 各渠道 outbound 按 edict.metadata.chat_id 把结果投递回去。
        # deliver 省略/origin → 继承发起会话来源；local/web → 仅 Web/WS；
        # 'feishu'/'feishu:oc_xxx' 等 → 指定渠道/会话。
        resolved = _resolve_delivery(deliver)
        if resolved == "origin":
            cur = get_current_edict()
            if cur and cur.metadata:
                resolved = {
                    k: cur.metadata[k]
                    for k in ("channel", "instance_id", "chat_id")
                    if cur.metadata.get(k)
                }
            else:
                resolved = {}
        if isinstance(resolved, dict) and resolved:
            edict_kwargs["metadata"] = resolved
        edict = Edict(**edict_kwargs)
        storage.save_edict(edict)

        # 不预建 memorial：每次到点触发由 executor 自动创建独立 memorial（运行记录）。
        job_id = await scheduler.schedule(edict)

        job_row = storage.get_scheduler_job(job_id)
        next_run = (
            job_row.get("next_run") if job_row
            else (parsed.at.isoformat() if parsed.at else None)
        )
        logger.info(
            "[tools/schedule_edict] scheduled edict=%s job=%s type=%s goal=%.50s",
            edict.id, job_id, parsed.type, goal,
        )
        type_label = {
            "once": "定时一次", "cron": "周期(cron)", "interval": "周期(间隔)",
        }.get(parsed.type, parsed.type)
        meta = edict_kwargs.get("metadata") or {}
        if meta.get("chat_id"):
            deliver_label = f"{meta.get('channel', 'feishu')}:{meta['chat_id']}"
        elif meta.get("channel"):
            deliver_label = f"{meta['channel']}(home_channel)"
        else:
            deliver_label = "仅 Web"
        return ok_result(
            f"已安排 {type_label}任务 #{edict.id[:8]}「{edict_title}」"
            f"（job={job_id[:8]}，下次 {next_run or '即将'}，回执→{deliver_label}）",
            details={
                "job_id": job_id,
                "edict_id": edict.id,
                "title": edict_title,
                "schedule_type": parsed.type,
                "next_run": next_run,
                "cron": parsed.cron,
                "interval_seconds": parsed.interval_seconds,
                "timezone": parsed.timezone,
                "assigned_persona_id": edict.assigned_persona_id,
                "deliver": deliver_label,
            },
        )

    async def schedule_edict(
        action: str = "create",
        goal: str | None = None,
        schedule: str | None = None,
        job_id: str | None = None,
        context: str | None = None,
        priority: str = "normal",
        assigned_persona_id: str | None = None,
        title: str | None = None,
        execution_profile: str = "foreground",
        timezone: str = "UTC",
        deliver: str | None = None,
    ) -> ToolResult:
        act = (action or "create").strip().lower()
        if act not in _VALID_ACTIONS:
            return error_result(
                f"schedule_edict: 未知 action={action!r}"
                f"（{'|'.join(_VALID_ACTIONS)}）",
            )

        if act == "list":
            jobs = await scheduler.list_jobs()
            return ok_result(
                f"当前有 {len(jobs)} 个定时/周期任务",
                details={"jobs": jobs},
            )

        if act in ("cancel", "pause", "resume", "run_now"):
            if not job_id:
                return error_result(f"schedule_edict: action={act} 需要 job_id")
            if act == "cancel":
                await scheduler.cancel(job_id)
                return ok_result(f"已取消任务 {job_id[:8]}", details={"job_id": job_id})
            if act == "pause":
                ok = await scheduler.pause(job_id)
                return (
                    ok_result(f"已暂停任务 {job_id[:8]}", details={"job_id": job_id})
                    if ok else error_result("无法暂停：任务不存在或非运行中")
                )
            if act == "resume":
                ok = await scheduler.resume(job_id)
                return (
                    ok_result(f"已恢复任务 {job_id[:8]}", details={"job_id": job_id})
                    if ok else error_result("无法恢复：任务不存在或非暂停态")
                )
            # run_now
            ok = await scheduler.run_now(job_id)
            return (
                ok_result(f"已立即触发任务 {job_id[:8]} 一次", details={"job_id": job_id})
                if ok else error_result("无法触发：任务/敕令不存在或已关闭")
            )

        return await _create(
            goal, schedule, context, priority,
            assigned_persona_id, title, execution_profile, timezone, deliver,
        )

    registry.register(
        "schedule_edict",
        schedule_edict,
        ToolDefinition(
            name="schedule_edict",
            description=(
                "安排一道【定时 / 周期】敕令（到点再下发执行，每次触发独立运行）。"
                "颁发后立即执行请用 submit_edict；本工具用于 '明天9点/每天/每周/每隔N分钟/N小时后' 这类延后或重复任务。"
                "用 action 分发：create=新建；list=列出现有定时任务；cancel/pause/resume/run_now=按 job_id 管理。"
                "**指派人选规则**：用户点名指派则传 assigned_persona_id；未指定时**先调用 list_personas** "
                "拿到实际官员名册再匹配，勿凭空猜测。返回 job_id 供后续管理。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": list(_VALID_ACTIONS),
                        "description": (
                            "操作：create=新建定时/周期任务（需 goal+schedule）；"
                            "list=列出全部；cancel/pause/resume/run_now=按 job_id 管理。默认 create。"
                        ),
                    },
                    "goal": {
                        "type": "string",
                        "description": "（create 必填）敕令目标，一句话说明要做什么。",
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "（create 必填）调度表达式，支持四种写法："
                            "相对一次 '30m'/'2h'/'1d'；周期间隔 'every 2h'/'every 30m'；"
                            "cron '0 9 * * *'（每天9点）；ISO 时间 '2026-06-10T09:00:00+08:00'。"
                        ),
                    },
                    "job_id": {
                        "type": "string",
                        "description": "（cancel/pause/resume/run_now 必填）目标任务的 job_id。",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选额外上下文（背景、约束、参考资料）。",
                    },
                    "priority": {
                        "type": "string",
                        "enum": list(_VALID_PRIORITIES),
                        "description": "优先级，默认 normal。",
                    },
                    "assigned_persona_id": {
                        "type": "string",
                        "description": "可选：指派执行人格 id；不填由 selector 自动选。",
                    },
                    "title": {
                        "type": "string",
                        "description": "可选：自定义标题；不填默认用 goal 前 20 字。",
                    },
                    "execution_profile": {
                        "type": "string",
                        "enum": list(_VALID_PROFILES),
                        "description": (
                            "执行模式：foreground=短任务（默认）；checkpointed=带检查点；"
                            "background=长任务后台执行。"
                        ),
                    },
                    "timezone": {
                        "type": "string",
                        "description": (
                            "调度时区，默认 'UTC'。常用 'Asia/Shanghai'。"
                            "影响 cron 解释与无时区 ISO 时间的归属。"
                        ),
                    },
                    "deliver": {
                        "type": "string",
                        "description": (
                            "（create 可选）到点执行完成后把结果回执发到哪里。"
                            "省略或 'origin'=发回当前会话的来源渠道（飞书会话→该 chat）；"
                            "'local'/'web'=不外发，仅在 Web 呈现；"
                            "'feishu'=飞书 home_channel；'feishu:oc_xxx'=指定飞书会话；"
                            "'telegram:<chat_id>' 同理。用户说'发到飞书/发到某群'时填对应值。"
                        ),
                    },
                },
                "required": ["action"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=2048,
            side_effect=True,
        ),
    )


__all__ = ["register_schedule_edict"]
