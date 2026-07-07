"""Notification renderer — convert Memorial/events to user-facing messages."""

from __future__ import annotations

from tianshu.models.memorial import Memorial


def render_status(memorial: Memorial) -> dict:
    """Render a user-friendly status update from a memorial."""
    base = {
        "memorial_id": memorial.id,
        "edict_id": memorial.edict_id,
        "status": memorial.status.value,
    }

    if memorial.summary:
        base["summary"] = memorial.summary[:500]

    if memorial.error:
        base["error"] = memorial.error[:500]

    if memorial.usage.total_tokens > 0:
        base["tokens_used"] = memorial.usage.total_tokens

    if memorial.audit:
        base["audit_verdict"] = memorial.audit.verdict

    if memorial.review_status != "not_required":
        base["review_status"] = memorial.review_status

    return base


def render_feishu(memorial: Memorial) -> str:
    """Render memorial as Feishu markdown card content."""
    status_emoji = {"completed": "OK", "failed": "FAIL", "cancelled": "CANCEL"}.get(
        memorial.status.value, memorial.status.value
    )
    lines = [
        f"**Status:** {status_emoji}",
        f"**Edict:** {memorial.edict_id}",
    ]
    if memorial.summary:
        lines.append(f"**Summary:** {memorial.summary[:300]}")
    if memorial.error:
        lines.append(f"**Error:** {memorial.error[:300]}")
    if memorial.usage.total_tokens > 0:
        lines.append(f"**Tokens:** {memorial.usage.total_tokens}")
    return "\n".join(lines)


def render_dingtalk(memorial: Memorial) -> str:
    """Render memorial as DingTalk markdown."""
    lines = [
        "## Tianshu Report",
        f"- **Status:** {memorial.status.value}",
        f"- **Edict:** {memorial.edict_id}",
    ]
    if memorial.summary:
        lines.append(f"- **Summary:** {memorial.summary[:300]}")
    if memorial.error:
        lines.append(f"- **Error:** {memorial.error[:300]}")
    return "\n".join(lines)


def render_email(memorial: Memorial) -> str:
    """Render memorial as plain text for email."""
    lines = [
        "Tianshu Execution Report",
        "========================",
        f"Status: {memorial.status.value}",
        f"Edict: {memorial.edict_id}",
        f"Memorial: {memorial.id}",
    ]
    if memorial.summary:
        lines.append(f"Summary: {memorial.summary[:500]}")
    if memorial.error:
        lines.append(f"Error: {memorial.error[:500]}")
    if memorial.usage.total_tokens > 0:
        lines.append(f"Tokens used: {memorial.usage.total_tokens}")
    return "\n".join(lines)
