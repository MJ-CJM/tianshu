"""submit_edict tool —— 让助手 LLM 在对话中"颁敕"（立即执行）。

走与 cli ``tianshu edict submit`` / web ``POST /api/edicts`` 同一执行路径：
``save_edict`` → ``save_memorial`` → fire ``edict.submitted``，由 scheduler /
executor 接管后续执行。

颁发即立即执行一次；**需要定时/周期请改用 ``schedule_edict`` 工具**。

权限：
- 需要 persona 把 ``submit_edict`` 加入 ``tools_allowed`` 才能调用；
- 通政司 ``enable_edict_submission`` toggle 控制启动时是否注入到助手 persona。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.edict_ops import submit_new_edict
from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec
from tianshu.models.common import VALID_EXECUTION_PROFILES, VALID_PRIORITIES
from tianshu.models.edict import Edict, title_from_goal
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.persona.loader import PersonaLoader
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_VALID_REVIEW_POLICIES = ("never", "on_failure", "on_flag", "always")


def register_submit_edict(
    registry: ToolRegistry,
    *,
    storage: Storage,
    event_bus: EventBus,
    persona_loader: PersonaLoader | None = None,
) -> None:
    """注册 submit_edict tool 到 ToolRegistry。"""

    async def submit_edict(
        goal: str,
        context: str | None = None,
        priority: str = "normal",
        assigned_persona_id: str | None = None,
        title: str | None = None,
        execution_profile: str = "foreground",
        review_policy: str = "never",
        output_format: str | None = None,
        acceptance_rubric: str | None = None,
    ) -> ToolResult:
        if not goal or not goal.strip():
            return error_result("submit_edict: goal 不能为空")
        if priority not in VALID_PRIORITIES:
            return error_result(
                f"submit_edict: priority 必须是 {'|'.join(VALID_PRIORITIES)}（实得 {priority}）",
            )
        if execution_profile not in VALID_EXECUTION_PROFILES:
            return error_result(
                f"submit_edict: execution_profile 必须是 "
                f"{'|'.join(VALID_EXECUTION_PROFILES)}（实得 {execution_profile}）",
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

        edict_title = title_from_goal(goal, title)
        edict_kwargs: dict = {
            "title": edict_title,
            "goal": goal,
            "context": context,
            "submitter": "assistant",
            "priority": priority,
            "execution_profile": execution_profile,
            "review_policy": review_policy,
        }
        if output_format and output_format.strip():
            edict_kwargs["output_format"] = output_format.strip()
        if acceptance is not None:
            edict_kwargs["acceptance"] = acceptance
        if assigned_persona_id:
            edict_kwargs["assigned_persona_id"] = assigned_persona_id
        edict = Edict(**edict_kwargs)
        submit_new_edict(
            storage,
            event_bus,
            edict,
            producer="submit_edict_tool",
            extra_payload={
                "priority": edict.priority,
                "assigned_persona_id": edict.assigned_persona_id,
                "execution_profile": edict.execution_profile,
                "via": "assistant_tool",
            },
        )
        logger.info(
            "[tools/submit_edict] new edict=%s goal=%.60s assigned=%s priority=%s profile=%s",
            edict.id,
            edict.goal,
            edict.assigned_persona_id,
            edict.priority,
            edict.execution_profile,
        )

        profile_hint = {
            "foreground": "短任务",
            "checkpointed": "中等任务（带检查点）",
            "background": "长任务（后台）",
        }.get(execution_profile, execution_profile)
        return ok_result(
            f"已颁敕 #{edict.id[:8]}「{edict_title}」（{profile_hint}，立即执行）",
            details={
                "edict_id": edict.id,
                "title": edict_title,
                "priority": priority,
                "execution_profile": execution_profile,
                "assigned_persona_id": edict.assigned_persona_id,
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
                "颁布一道新敕令并【立即下发执行】（在系统主任务流中创建一个独立的业务任务）。"
                "用户明确表达'帮我下发/颁布/新建一个敕令/任务'且要现在就做时调用此工具。"
                "敕令进入 scheduler/executor 后会自动经历规划→执行→审计的完整生命周期，"
                "与当前对话独立运行。"
                "**若用户要求'每天/每周/定时/到某时再做/隔一段时间重复'，改用 schedule_edict 工具，不要用本工具。**"
                "**指派人选规则**：若用户已点名指派（如'让唐伯虎/王阳明做'），"
                "直接传 assigned_persona_id；若用户未指定，**必须先调用 list_personas** "
                "拿到当前 DB 实际官员名册，按各人 department/title 与任务匹配后再选定 "
                "assigned_persona_id 颁敕；切勿凭空猜测某 persona 是否存在。"
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
                        "enum": list(VALID_PRIORITIES),
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
                        "enum": list(VALID_EXECUTION_PROFILES),
                        "description": (
                            "执行模式：foreground=短任务（一次性返回，秒级到分钟级，默认）；"
                            "checkpointed=中等任务（带检查点，可断点续跑）；"
                            "background=长任务（后台执行，分钟级到小时级，例如批量爬取/大型分析）。"
                            "用户说'长任务/后台跑'时选 background，'快速答一下'时选 foreground。"
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
