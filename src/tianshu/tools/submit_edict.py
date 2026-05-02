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
from typing import TYPE_CHECKING

from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
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
        if (
            assigned_persona_id
            and persona_loader is not None
            and persona_loader.get(assigned_persona_id) is None
        ):
            return error_result(
                f"submit_edict: persona '{assigned_persona_id}' 不存在",
            )

        edict_title = title or (goal[:20] + "…" if len(goal) > 20 else goal)
        edict_kwargs: dict = {
            "title": edict_title,
            "goal": goal,
            "context": context,
            "submitter": "assistant",
            "priority": priority,
            "execution_profile": execution_profile,
        }
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
                "via": "assistant_tool",
            },
        ))
        logger.info(
            "[tools/submit_edict] new edict=%s goal=%.60s assigned=%s priority=%s profile=%s",
            edict.id, edict.goal, edict.assigned_persona_id,
            edict.priority, edict.execution_profile,
        )

        profile_hint = {
            "foreground": "短任务",
            "checkpointed": "中等任务（带检查点）",
            "background": "长任务（后台）",
        }.get(execution_profile, execution_profile)
        return ok_result(
            f"已颁敕 #{edict.id[:8]}「{edict_title}」（{profile_hint}）",
            details={
                "edict_id": edict.id,
                "title": edict_title,
                "priority": priority,
                "execution_profile": execution_profile,
                "assigned_persona_id": edict.assigned_persona_id,
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
                "与当前对话独立运行。返回敕令短 ID 给用户做后续追踪。"
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
                },
                "required": ["goal"],
            },
            tier=ToolTier.T2_NETWORK.value,
            max_result_chars=1024,
            side_effect=True,
        ),
    )


__all__ = ["register_submit_edict"]
