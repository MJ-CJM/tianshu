"""任务级监督报告生成 — outer loop 终态后由 critic persona 总评。

输出 4 章节：观察到的问题 / 做得好 / 做得不够 / 建议。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from tianshu.executor.orchestrator.critic import _extract_json, _read_persona_text
from tianshu.executor.orchestrator.state import OuterLoopState
from tianshu.llm import LLMClient, LLMUsageContext
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.supervision import SupervisionReport
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


_SUPERVISION_PROMPT_TEMPLATE = """你是 {persona_name}（{persona_dept}），刚刚完成对一项长任务的全程监督。请基于你监督全程的观察，给出一份**结构化复盘报告**。

# 任务目标
{goal}

# 迭代历史（共 {iter_count} 轮）
{iterations_summary}

# 终态
{final_status}（总成本 ¥{total_cost:.4f}）

# 输出要求
严格输出 JSON（不要前后加任何解释文字）：
{{
  "issues_observed": ["..."],
  "well_done": ["..."],
  "poorly_done": ["..."],
  "recommendation": "..."
}}

要求：
- issues_observed：你监督全程观察到的问题清单（具体到哪几轮、问题在哪）
- well_done：actor 做得好的地方（具体到第几轮、做对了什么）
- poorly_done：actor 做得不够的地方（具体到第几轮、问题在哪）
- recommendation：给后续类似任务的可操作建议（一段简短文字即可）

每个 list 至少 1 项；recommendation 必填。
"""


def _truncate(s: str | None, limit: int = 500) -> str:
    if not s:
        return ""
    return s if len(s) <= limit else s[:limit] + "...(truncated)"


def _format_iterations_from_state(state: OuterLoopState) -> str:
    """优先用 state.history（runtime 完整数据）格式化迭代摘要。"""
    if not state.history:
        return "(no history in state)"
    lines: list[str] = []
    for r in state.history:
        actor_snippet = _truncate(r.actor_output, 800)
        critic_part = ""
        if r.critic_result:
            verdict = r.critic_result.verdict
            issue = r.critic_result.issue_class or ""
            feedback = _truncate(r.critic_result.feedback, 300)
            critic_part = f" | critic: {verdict} [{issue}] {feedback}"
        lines.append(f"## 第 {r.iteration} 轮 [{r.level}]\nactor: {actor_snippet}{critic_part}")
    return "\n\n".join(lines)


def _format_iterations_from_db(rows: list[dict]) -> str:
    """resume 后 state.history=() 时回查 outer_loop_iterations 表。"""
    if not rows:
        return "(no history in db)"
    lines: list[str] = []
    for r in rows:
        actor = _truncate(r.get("actor_output"), 800)
        critic_summary = ""
        critic_raw = r.get("critic_result")
        if critic_raw:
            try:
                cd = json.loads(critic_raw)
                critic_summary = (
                    f" | critic: {cd.get('verdict')} "
                    f"[{cd.get('issue_class') or ''}] "
                    f"{_truncate(cd.get('feedback'), 300)}"
                )
            except (ValueError, json.JSONDecodeError):
                pass
        lines.append(f"## 第 {r['iteration']} 轮 [{r['level']}]\nactor: {actor}{critic_summary}")
    return "\n\n".join(lines)


async def generate_supervision_report(
    edict: Edict,
    state: OuterLoopState,
    final_status: TaskStatus,
    persona: object,
    llm: LLMClient,
    storage: Storage | None = None,
    *,
    memorial_id: str = "",
) -> SupervisionReport:
    """调监督官 LLM 生成结构化报告。失败时仍返回 SupervisionReport（章节空 + raw_feedback 兜底）。"""
    iter_summary = _format_iterations_from_state(state)
    # resume 后 history 不重建，回查 DB
    if (state.history == () or not state.history) and storage is not None:
        rows = storage.get_outer_loop_iterations(edict.id)
        iter_summary = _format_iterations_from_db(rows)

    persona_name = getattr(persona, "name", "未知监督官")
    persona_dept = getattr(persona, "department", "")
    persona_id = getattr(persona, "id", "")

    prompt = _SUPERVISION_PROMPT_TEMPLATE.format(
        persona_name=persona_name,
        persona_dept=persona_dept,
        goal=edict.goal,
        iter_count=state.iteration,
        iterations_summary=iter_summary,
        final_status=final_status.value,
        total_cost=state.total_cost_cny,
    )

    persona_context = _read_persona_text(persona)
    system_msg = (
        f"# 你的角色：{persona_name}（{persona_dept}）\n\n{persona_context}"
        if persona_context
        else f"# 你的角色：{persona_name}（{persona_dept}）"
    )
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt},
    ]

    raw_text = ""
    issues: list[str] = []
    well: list[str] = []
    poor: list[str] = []
    recommendation: str | None = None

    try:
        if memorial_id:
            resp = await llm.chat(
                messages=messages,
                usage_context=LLMUsageContext(
                    edict_id=edict.id,
                    memorial_id=memorial_id,
                    operation="supervision_report",
                ),
            )
        else:
            resp = await llm.chat(messages=messages)
        raw_text = resp.content or ""
        try:
            data = _extract_json(raw_text)
            issues = list(data.get("issues_observed") or [])
            well = list(data.get("well_done") or [])
            poor = list(data.get("poorly_done") or [])
            rec = data.get("recommendation")
            recommendation = str(rec) if rec else None
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(
                "supervision report JSON parse failed for edict %s: %s",
                edict.id,
                e,
            )
            # raw_feedback 已有，4 章节空
    except Exception as e:
        logger.exception(
            "supervision report LLM call failed for edict %s: %s",
            edict.id,
            e,
        )
        raw_text = f"(LLM 调用失败: {e})"

    return SupervisionReport(
        edict_id=edict.id,
        memorial_id=memorial_id,
        persona_id=persona_id,
        persona_name=persona_name,
        final_status=final_status,
        iterations_count=state.iteration,
        total_cost_cny=state.total_cost_cny,
        issues_observed=issues,
        well_done=well,
        poorly_done=poor,
        recommendation=recommendation,
        raw_feedback=raw_text,
        created_at=datetime.now(UTC),
    )
