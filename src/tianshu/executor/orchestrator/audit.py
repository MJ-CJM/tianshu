"""Completion audit 执行器。

Audit 是 critic pass 后的「覆盖审」：核实 acceptance 每条要求都有具体证据。
执行者优先复用 critic LLM；critic 不在场时由 caller 传入 actor LLM 自审。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from tianshu.executor.orchestrator.templates import (
    TemplateName,
    render_template,
)
from tianshu.llm import LLMClient, LLMUsageContext
from tianshu.models.acceptance import AcceptanceCriteria

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditGap:
    check_name: str
    requirement: str
    evidence_status: str  # missing | weak | uncertain | ok
    suggested_action: str


@dataclass(frozen=True)
class AuditResult:
    passed: bool
    gaps: tuple[AuditGap, ...]


# 非贪婪：在最近的 } 加 ``` 处停下；如 LLM 在 JSON 内部夹了 ``` 导致截断，
# 后续 json.loads 会抛、走 _meta retry 兜底。
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_LOOSE = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> str | None:
    m = _JSON_FENCE.search(text)
    if m:
        return m.group(1)
    m = _JSON_LOOSE.search(text)
    if m:
        return m.group(0)
    return None


def parse_audit_response(raw: str) -> AuditResult:
    """解析 LLM 输出的 audit JSON；解析失败返回 passed=false + 提示性 gap（不抛）。"""
    candidate = _extract_json(raw or "")
    if not candidate:
        return AuditResult(
            passed=False,
            gaps=(
                AuditGap(
                    check_name="_meta",
                    requirement="审计 JSON 解析失败",
                    evidence_status="missing",
                    suggested_action="重新执行审计并严格按 schema 输出 JSON",
                ),
            ),
        )
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.warning("audit json parse failed: %s; raw=%r", e, candidate[:200])
        return AuditResult(
            passed=False,
            gaps=(
                AuditGap(
                    check_name="_meta",
                    requirement="审计 JSON parse 失败",
                    evidence_status="missing",
                    suggested_action="重新执行审计并严格按 schema 输出 JSON",
                ),
            ),
        )
    passed = bool(data.get("passed", False))
    gaps_raw = data.get("gaps") or []
    gaps = tuple(
        AuditGap(
            check_name=str(g.get("check_name", "?")),
            requirement=str(g.get("requirement", "?")),
            evidence_status=str(g.get("evidence_status", "uncertain")),
            suggested_action=str(g.get("suggested_action", "继续完善证据")),
        )
        for g in gaps_raw
        if isinstance(g, dict)
    )
    return AuditResult(passed=passed, gaps=gaps)


def format_gaps_for_continuation(gaps: tuple[AuditGap, ...]) -> str:
    """把 gaps 渲染成续转 prompt 中可读的 audit_feedback 块。"""
    if not gaps:
        return ""
    return "\n".join(
        f"- **{g.check_name}** [{g.evidence_status}] {g.requirement} → {g.suggested_action}"
        for g in gaps
    )


def _checks_to_dicts(acceptance: AcceptanceCriteria) -> list[dict]:
    return [
        {
            "name": c.name,
            "kind": c.kind,
            "command": c.command,
            "rubric": c.rubric,
            "pass_threshold": c.pass_threshold,
        }
        for c in acceptance.checks
    ]


async def run_completion_audit(
    *,
    actor_output: str,
    objective: str,
    acceptance: AcceptanceCriteria,
    llm: LLMClient,
    usage_context: LLMUsageContext | None = None,
) -> AuditResult:
    """跑一次完成审计；JSON 解析失败时重试 1 次。"""
    audit_prompt = render_template(
        TemplateName.COMPLETION_AUDIT,
        objective=objective,
        checks=_checks_to_dicts(acceptance),
    )
    messages = [
        {"role": "system", "content": audit_prompt},
        {"role": "user", "content": f"## 待审产出\n\n{actor_output}"},
    ]
    if usage_context is None:
        response = await llm.chat(messages)
    else:
        response = await llm.chat(messages, usage_context=usage_context)
    result = parse_audit_response(response.content or "")
    if result.gaps and result.gaps[0].check_name == "_meta":
        logger.info("audit JSON 解析失败，重试 1 次")
        if usage_context is None:
            response = await llm.chat(messages)
        else:
            response = await llm.chat(messages, usage_context=usage_context)
        result = parse_audit_response(response.content or "")
    return result
