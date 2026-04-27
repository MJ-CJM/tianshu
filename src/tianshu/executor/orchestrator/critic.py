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

_BASE_CRITIC_RULES = """你正在以监督官身份审议 actor 产出，请基于 edict 的 acceptance criteria，
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


def _read_persona_text(persona: object) -> str:
    """读 persona 的 soul + role 两个 .md 文件拼成 system prompt 上下文。"""
    parts: list[str] = []
    for attr in ("soul_path", "role_path"):
        path = getattr(persona, attr, None)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if text.strip():
                parts.append(text.strip())
        except OSError as e:
            logger.warning("read persona %s failed: %s", attr, e)
    return "\n\n".join(parts)


def build_critic_prompt(persona: object | None) -> str:
    """拼 critic 的 system prompt = persona 上下文 + ISSUE_CLASSES 强约束。

    persona=None 时降级到通用 critic（保留旧行为做兼容）。
    """
    if persona is None:
        return _BASE_CRITIC_RULES
    persona_name = getattr(persona, "name", "未知监督官")
    persona_dept = getattr(persona, "department", "")
    persona_text = _read_persona_text(persona)
    header = f"# 你的角色：{persona_name}（{persona_dept}）"
    parts = [header]
    if persona_text:
        parts.append(persona_text)
    parts.append(_BASE_CRITIC_RULES)
    return "\n\n".join(parts)


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


def _resolve_critic_llm(persona: object | None, ctx: object | None, fallback: LLMClient) -> LLMClient:
    """按 persona.llm_config_name 拿 critic 专用 LLM；找不到落 fallback。"""
    if persona is None or ctx is None:
        return fallback
    pm = getattr(ctx, "provider_manager", None)
    if pm is None:
        return fallback
    config_name = getattr(persona, "llm_config_name", None)
    if not config_name:
        return fallback
    try:
        return pm.get_client(config_name_override=config_name)
    except Exception as e:
        logger.warning("resolve persona LLM '%s' failed: %s", config_name, e)
        return fallback


async def _review_single(
    actor_output: str,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    persona: object | None,
    llm: LLMClient,
    *,
    fallback_llm: LLMClient | None = None,
    max_retries: int = 2,
) -> CriticResult:
    """单 persona 独立调一次 critic LLM。"""
    system_prompt = build_critic_prompt(persona)
    user_msg = (
        f"# Edict goal\n{edict.goal}\n\n"
        f"# Acceptance criteria summary\n"
        f"max_outer_iterations: {acceptance.max_outer_iterations}\n"
        f"checks: {[c.name for c in acceptance.checks]}\n\n"
        f"# Actor output\n{actor_output}"
    )
    messages = [
        {"role": "system", "content": system_prompt},
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


def _aggregate_results_all_must_pass(
    results: list[tuple[str, CriticResult]],
) -> CriticResult:
    """聚合规则：全过则过。
    - 全部 pass → verdict=pass, feedback 简要拼接（每位监督官的 feedback）
    - 任一 fail → verdict=fail, issue_class 取首个 fail 的, feedback 拼接所有反馈
    """
    if not results:
        # 没 critic（caller 应避免；保险返通过）
        return CriticResult(verdict="pass", feedback="(no critics)")

    fails = [(pid, r) for pid, r in results if r.verdict == "fail"]
    if not fails:
        # 全过
        feedback = "\n".join(
            f"[{pid}] pass: {r.feedback}" for pid, r in results if r.feedback
        )
        return CriticResult(verdict="pass", feedback=feedback or "all critics passed")

    # 任一 fail → 整体 fail
    issue_class = fails[0][1].issue_class or "other"
    feedback_parts: list[str] = []
    suggested_parts: list[str] = []
    for pid, r in results:
        verdict = r.verdict
        line = f"[{pid}] {verdict}: {r.feedback}"
        feedback_parts.append(line)
        if r.suggested_fix:
            suggested_parts.append(f"[{pid}] {r.suggested_fix}")
    return CriticResult(
        verdict="fail",
        issue_class=issue_class,
        feedback="\n".join(feedback_parts),
        suggested_fix="\n".join(suggested_parts) if suggested_parts else None,
    )


async def review(
    actor_output: str,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    llm: LLMClient,
    *,
    fallback_llm: LLMClient | None = None,
    max_retries: int = 2,
    ctx: object | None = None,
) -> CriticResult:
    """监督评审入口。多监督官时并发调用，按"全过则过"聚合。

    解析 acceptance.critic.effective_persona_ids():
    - 列表非空 → 多 persona 并发，返聚合结果
    - 列表空 → 降级到通用 critic prompt + 传入的 llm（兼容老调用）

    persona 不存在 → 立即抛 CriticUnavailable（不容忍幽灵 persona）
    """
    import asyncio

    persona_ids = acceptance.critic.effective_persona_ids()

    # 无 persona 配置：降级到通用 critic
    if not persona_ids or ctx is None:
        return await _review_single(
            actor_output, edict, acceptance, None, llm,
            fallback_llm=fallback_llm, max_retries=max_retries,
        )

    persona_loader = getattr(ctx, "persona_loader", None)
    if persona_loader is None:
        return await _review_single(
            actor_output, edict, acceptance, None, llm,
            fallback_llm=fallback_llm, max_retries=max_retries,
        )

    # 多 persona：并发调用
    personas: list[tuple[str, object]] = []
    for pid in persona_ids:
        p = persona_loader.get(pid)
        if p is None:
            raise CriticUnavailable(f"critic persona '{pid}' not found")
        personas.append((pid, p))

    async def _one(pid: str, persona: object) -> tuple[str, CriticResult]:
        actual_llm = _resolve_critic_llm(persona, ctx, llm)
        try:
            r = await _review_single(
                actor_output, edict, acceptance, persona, actual_llm,
                fallback_llm=fallback_llm, max_retries=max_retries,
            )
            return (pid, r)
        except CriticUnavailable as e:
            logger.warning("critic '%s' unavailable: %s", pid, e)
            # 单个 critic 失败 → 视为 fail（不让一个挂掉就整体 skip）
            return (pid, CriticResult(
                verdict="fail", issue_class="other",
                feedback=f"critic '{pid}' 不可用: {e}",
            ))

    results = await asyncio.gather(*[_one(pid, p) for pid, p in personas])
    return _aggregate_results_all_must_pass(results)
