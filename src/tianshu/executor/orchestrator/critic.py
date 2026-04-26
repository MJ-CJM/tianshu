"""Critic agent — 独立 LLM 调用，结构化输出 verdict + issue_class。"""

from __future__ import annotations

import json
import logging
import re
from typing import Literal

from tianshu.executor.orchestrator.state import CriticResult
from tianshu.llm import LLMClient
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict

logger = logging.getLogger(__name__)

# v1 内置 issue_class 集合 —— critic system prompt 强约束在内
ISSUE_CLASSES: tuple[str, ...] = (
    "factual_error",         # 事实性错误
    "tone_mismatch",         # 语气/风格与目标不符
    "incomplete_coverage",   # 覆盖不全
    "structure_mismatch",    # 结构与要求不符
    "formatting_violation",  # 格式问题
    "checks_failed",         # 指标层失败（不进 critic）
    "other",                 # 未分类
)

_SYSTEM_PROMPT = """你是天枢的 critic agent。基于 edict 的 acceptance criteria，
判定 actor 的输出是否合格。

输出严格 JSON（不要前后加任何解释文字）:
{
  "verdict": "pass" | "fail",
  "issue_class": <one of: %s>,
  "feedback": "...",
  "suggested_fix": "..." (optional)
}

规则:
- 如果合格 → verdict=pass, issue_class 可留空
- 如果不合格 → verdict=fail, issue_class 必填且必须从给定集合选
- feedback 给 actor 看，要具体可执行
""" % ", ".join(ISSUE_CLASSES)


class CriticUnavailable(Exception):
    """critic LLM 调用全部失败（包括 fallback）。调用方按 on_critic_unavailable 决策。"""


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取第一个 JSON object。"""
    if not text:
        raise ValueError("empty LLM output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object found: {text[:200]!r}")
    return json.loads(match.group(0))


def _parse(raw: str) -> CriticResult:
    data = _extract_json(raw)
    verdict = data.get("verdict")
    if verdict not in ("pass", "fail"):
        raise ValueError(f"verdict 非法: {verdict!r}")
    issue_class = data.get("issue_class")
    if verdict == "fail":
        if issue_class not in ISSUE_CLASSES:
            issue_class = "other"
    else:
        issue_class = None
    return CriticResult(
        verdict=verdict,
        issue_class=issue_class,
        feedback=str(data.get("feedback", "")),
        suggested_fix=data.get("suggested_fix"),
    )


async def review(
    actor_output: str,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    llm: LLMClient,
    *,
    fallback_llm: LLMClient | None = None,
    max_retries: int = 2,
) -> CriticResult:
    """独立调用 critic LLM。

    重试 max_retries 次；仍失败时尝试 fallback_llm；都不行则抛 CriticUnavailable。
    """
    user_msg = (
        f"# Edict goal\n{edict.goal}\n\n"
        f"# Acceptance criteria summary\n"
        f"max_outer_iterations: {acceptance.max_outer_iterations}\n"
        f"checks: {[c.name for c in acceptance.checks]}\n\n"
        f"# Actor output\n{actor_output}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = await llm.chat(messages=messages)
            return _parse(resp.content or "")
        except Exception as e:
            logger.warning("critic LLM attempt %d failed: %s", attempt, e)
            last_err = e

    if fallback_llm is not None:
        try:
            resp = await fallback_llm.chat(messages=messages)
            return _parse(resp.content or "")
        except Exception as e:
            logger.error("critic fallback also failed: %s", e)
            last_err = e

    raise CriticUnavailable(f"critic 全部尝试失败: {last_err}")
