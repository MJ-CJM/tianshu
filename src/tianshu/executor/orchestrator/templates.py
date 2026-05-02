"""Prompt 模板渲染辅助。

模板文件位于 src/tianshu/executor/templates/edict/{name}.md。
模板使用 Python `str.format` 替换 `{key}` 占位符。
"""
from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "edict"


class TemplateName(str, Enum):
    CONTINUATION = "continuation"
    COMPLETION_AUDIT = "completion_audit"
    WIND_DOWN = "wind_down"


# 极简兜底——模板文件读取失败时使用，仍包裹 untrusted_objective
TEMPLATE_FALLBACK: dict[TemplateName, str] = {
    TemplateName.CONTINUATION: (
        "继续推进当前任务。\n\n"
        "{objective_block}\n\n"
        "选择下一步具体动作；不要重复已完成工作；"
        "不要把'努力过/部分完成/计划完整'当作完成证据。"
    ),
    TemplateName.COMPLETION_AUDIT: (
        "对照目标进行完成审计。\n\n"
        "{objective_block}\n\n"
        "请逐条贴出真实证据（文件路径 / 命令输出 / 测试结果）。"
        "任何一条缺证据 / 弱证据 / 不确定，视为未完成。"
        "输出 JSON: {{\"passed\": bool, \"gaps\": [...]}}。"
    ),
    TemplateName.WIND_DOWN: (
        "当前任务接近预算上限，进入收尾阶段。\n\n"
        "{objective_block}\n\n"
        "不要开启新工作；汇总已完成；列出剩余；"
        "给出可继续的下一步建议；本轮起禁止调用副作用工具。"
    ),
}


def wrap_untrusted_objective(objective: str) -> str:
    """用 <untrusted_objective> 标签包裹 goal。

    剥离内部已存在的闭合标签，避免 prompt-injection 通过提前闭合逃逸。
    """
    sanitized = objective.replace("</untrusted_objective>", "[/]")
    return f"<untrusted_objective>\n{sanitized}\n</untrusted_objective>"


def _load_template_file(name: TemplateName) -> str | None:
    path = _TEMPLATES_DIR / f"{name.value}.md"
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as e:
        logger.warning("template file unavailable, using fallback: %s (%s)", path, e)
        return None


def _format_audit_gaps(gaps: str | None) -> str:
    if not gaps:
        return ""
    return f"<audit_feedback>\n{gaps}\n</audit_feedback>"


def _format_critic_feedback(fb: str | None) -> str:
    if not fb:
        return ""
    return f"<critic_feedback>\n{fb}\n</critic_feedback>"


def _format_checks_list(checks: list[dict] | None) -> str:
    if not checks:
        return "（本任务无 acceptance.checks 配置；请直接对照 objective 自审）"
    lines = []
    for i, c in enumerate(checks, 1):
        kind = c.get("kind", "bash")
        spec = c.get("command") or c.get("rubric") or ""
        lines.append(f"{i}. **{c['name']}** (kind={kind}): {spec}")
    return "\n".join(lines)


def _apply_fallback(
    name: TemplateName,
    objective_block: str,
    critic_feedback: str | None,
    audit_gaps: str | None,
    checks: list[dict] | None,
) -> str:
    """fallback 路径：在基础文本后追加可选块，保证字段存在性断言可通过。"""
    base = TEMPLATE_FALLBACK[name].format(objective_block=objective_block)
    extras: list[str] = []
    fb = _format_critic_feedback(critic_feedback)
    if fb:
        extras.append(fb)
    ag = _format_audit_gaps(audit_gaps)
    if ag:
        extras.append(ag)
    cl = _format_checks_list(checks)
    if checks is not None:
        extras.append(cl)
    if extras:
        return base + "\n\n" + "\n\n".join(extras)
    return base


def render_template(
    name: TemplateName,
    *,
    objective: str,
    critic_feedback: str | None = None,
    audit_gaps: str | None = None,
    checks: list[dict] | None = None,
) -> str:
    """渲染指定模板，缺失字段被替换为空串；模板文件读取失败回退到 TEMPLATE_FALLBACK。"""
    objective_block = wrap_untrusted_objective(objective)
    raw = _load_template_file(name)
    if raw is None:
        return _apply_fallback(name, objective_block, critic_feedback, audit_gaps, checks)
    try:
        return raw.format(
            objective_block=objective_block,
            critic_feedback_block=_format_critic_feedback(critic_feedback),
            audit_feedback_block=_format_audit_gaps(audit_gaps),
            checks_list=_format_checks_list(checks),
        )
    except KeyError as e:
        logger.warning("template format error %s in %s, using fallback", e, name)
        return _apply_fallback(name, objective_block, critic_feedback, audit_gaps, checks)
