"""submit_edict tool —— 让助手 LLM 在对话中"颁敕"（立即执行）。

走与 cli ``tianshu edict submit`` / web ``POST /api/edicts`` 相同的 durable
application service，由 outbox 驱动 scheduler / executor 后续执行。

颁发即立即执行一次；**需要定时/周期请改用 ``schedule_edict`` 工具**。

权限：
- 需要 persona 把 ``submit_edict`` 加入 ``tools_allowed`` 才能调用；
- 通政司 ``enable_edict_submission`` toggle 控制启动时是否注入到助手 persona。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.application.ingress import (
    make_ingress_auth_context,
    requested_contract_for_edict,
)
from tianshu.kernel.ambient import get_current_edict, get_current_tool_invocation_id
from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec
from tianshu.models.common import VALID_EXECUTION_PROFILES, VALID_PRIORITIES
from tianshu.models.edict import Edict, EdictRuntime, title_from_goal
from tianshu.models.principal import (
    AuthenticationSource,
    ClientKind,
    PrincipalKind,
)
from tianshu.models.side_effect import SideEffectSemantics
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
    edict_application_service: EdictApplicationService | None = None,
) -> None:
    """注册 submit_edict tool 到 ToolRegistry。"""
    del event_bus
    if edict_application_service is None:
        raise ValueError("edict_application_service is required")
    edict_application = edict_application_service

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

        # 发起会话上下文：既决定成果往哪回禀，也决定这道敕令是一次性还是多轮。
        # 只取路由三件套：整份拷贝会带上 assistant_chat 标记，反被 /list 当聊天
        # 敕令隐藏。
        caller = get_current_edict()
        routing: dict = {}
        if caller and caller.metadata:
            routing = {
                k: caller.metadata[k]
                for k in ("channel", "instance_id", "chat_id")
                if caller.metadata.get(k)
            }
        # 渠道路由让各渠道 outbound 在 execution.completed 时按 metadata.chat_id
        # 把成果投递回原对话（与 schedule_edict 的 deliver=origin 同口径）。缺了
        # 这三件套，敕令会"人间蒸发"——办完无处回禀，/list 按 instance_id 也查不到。
        #
        # 意图来源决定生命周期（判据是"有没有人在对话那头"，不是"谁调用了 API"）：
        # 人在渠道对话里让助手代颁 → 与 /new 同语义，敕令过审后保持 OPEN，
        # /select 切过去可继续批示，结案权在人；无会话上下文（cron / 自主 agent）
        # → 一次性闭环，办成即结案。见 EdictRuntime.conversation（2026-07-29 拍板）。
        interactive = bool(routing.get("chat_id"))

        edict_title = title_from_goal(goal, title)
        edict_kwargs: dict = {
            "title": edict_title,
            "goal": goal,
            "context": context,
            "submitter": "assistant",
            "priority": priority,
            "execution_profile": execution_profile,
            "review_policy": review_policy,
            "runtime": EdictRuntime(conversation=interactive),
        }
        if routing:
            edict_kwargs["metadata"] = routing
        if output_format and output_format.strip():
            edict_kwargs["output_format"] = output_format.strip()
        if acceptance is not None:
            edict_kwargs["acceptance"] = acceptance
        if assigned_persona_id:
            edict_kwargs["assigned_persona_id"] = assigned_persona_id
        edict = Edict(**edict_kwargs)
        invocation_id = get_current_tool_invocation_id() or edict.id
        correlation_id = f"tool:{invocation_id}"
        command = SubmitEdictCommand(
            edict=edict,
            idempotency_key=correlation_id,
            requested_contract=requested_contract_for_edict(edict),
            extra_payload={
                "priority": edict.priority,
                "assigned_persona_id": edict.assigned_persona_id,
                "execution_profile": edict.execution_profile,
                "via": "assistant_tool",
            },
        )
        submission = edict_application.submit(
            command,
            auth=make_ingress_auth_context(
                principal_id=f"tool:{caller.submitter if caller and caller.submitter else 'assistant'}",
                principal_kind=PrincipalKind.SERVICE,
                source=AuthenticationSource.TRUSTED_LOCAL,
                client_kind=ClientKind.SYSTEM,
                correlation_id=correlation_id,
            ),
            producer="submit_edict_tool",
            correlation_id=correlation_id,
        )
        persisted_edict = submission.edict
        logger.info(
            "[tools/submit_edict] new edict=%s goal=%.60s assigned=%s priority=%s profile=%s",
            persisted_edict.id,
            persisted_edict.goal,
            persisted_edict.assigned_persona_id,
            persisted_edict.priority,
            persisted_edict.execution_profile,
        )

        profile_hint = {
            "foreground": "短任务",
            "checkpointed": "中等任务（带检查点）",
            "background": "长任务（后台）",
        }.get(execution_profile, execution_profile)
        # 继承到 chat_id 才有回禀通道；否则如实告知需去 web/CLI 查看，不许诺空头追踪。
        tail = "，办讫自动回禀本对话" if interactive else "，成果请在 Web 端查看"
        return ok_result(
            f"已颁敕 #{persisted_edict.id[:8]}「{edict_title}」（{profile_hint}，立即执行{tail}）",
            details={
                "edict_id": persisted_edict.id,
                "memorial_id": submission.memorial.id,
                "title": edict_title,
                "priority": priority,
                "execution_profile": execution_profile,
                "assigned_persona_id": persisted_edict.assigned_persona_id,
                "review_policy": review_policy,
                "output_format": persisted_edict.output_format,
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
                "**颁敕后的回话规矩**：本工具返回的短 ID 只作存档标识告知用户即可；"
                "敕令办讫后成果由系统自动回禀本对话，**不要**反问"
                "「需要我帮你查看结果吗」，也不要引导用户去轮询/追问进度——"
                "如实说明已下发、稍候自动回禀即可。"
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
            managed_effect_semantics=SideEffectSemantics.PROVIDER_IDEMPOTENT,
        ),
    )


__all__ = ["register_submit_edict"]
