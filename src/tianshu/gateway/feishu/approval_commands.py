"""飞书审批双语命令解析与路由（中英对照）。

命令对照表：
  /approve            /准         单次允许（scope=once）
  /approve edict      /准敕       本敕令允许（scope=edict）
  /approve always     /准永       总是允许（scope=always）
  /reject             /驳         拒绝

多 pending 时需附带 memorial_id 前缀（≥6 字符）：
  /approve <ID>       /准 <ID>
  /approve <ID> edict /准敕 <ID>（也支持反序）
  /reject  <ID>       /驳  <ID>
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

ApprovalAction = Literal["approve", "reject"]
ApprovalScope = Literal["once", "edict", "always"]

# 中文 scope 后缀（拼接在 /准 后面，如 /准敕 / /准永）
_ZH_SCOPE_SUFFIX = {
    "敕": "edict",
    "永": "always",
}
# 英文 scope 第二 token
_EN_SCOPE_TOKEN = {
    "edict": "edict",
    "always": "always",
}

_MIN_PREFIX_LEN = 6


@dataclass(frozen=True)
class ApprovalCommand:
    action: ApprovalAction
    scope: ApprovalScope | None  # reject 时为 None
    target_prefix: str | None    # memorial_id 前缀，≥6 字符；None 表示不指定


def parse_approval_command(text: str) -> ApprovalCommand | None:
    """识别审批命令，返回结构化形式；非审批命令返 None。

    - approve 默认 scope=once；reject 不带 scope。
    - 任意位置（命令后 / 命令尾）允许出现 memorial_id 前缀（≥6 字符）。
    """
    if not text or not text.startswith("/"):
        return None
    parts = text.strip().split()
    if not parts:
        return None
    head = parts[0].lower()
    rest = parts[1:]

    # 拆解动作 + 内嵌 scope（中文形式 /准敕 /准永）
    action: ApprovalAction | None = None
    scope: ApprovalScope | None = None

    if head == "/reject" or head == "/驳":
        action = "reject"
    elif head == "/approve":
        action = "approve"
        scope = "once"
    elif head.startswith("/准"):
        action = "approve"
        suffix = head[len("/准"):]
        if suffix == "":
            scope = "once"
        elif suffix in _ZH_SCOPE_SUFFIX:
            scope = _ZH_SCOPE_SUFFIX[suffix]  # type: ignore[assignment]
        else:
            return None  # /准xxx 形式但 xxx 不识别 → 不当作审批命令
    else:
        return None

    # 在 rest 中找 scope（英文）和 prefix
    target_prefix: str | None = None
    for tok in rest:
        low = tok.lower()
        if action == "approve" and low in _EN_SCOPE_TOKEN:
            # 英文 scope：仅在 /approve 后允许；如果同时有 /准敕 + always 这种冲突，以最后一次为准
            scope = _EN_SCOPE_TOKEN[low]  # type: ignore[assignment]
        elif len(tok) >= _MIN_PREFIX_LEN and all(c.isalnum() or c in "-_" for c in tok):
            target_prefix = tok
        # 其他 token 忽略

    return ApprovalCommand(action=action, scope=scope, target_prefix=target_prefix)


class ApprovalCommandHandler:
    """根据已解析的 ApprovalCommand 调用 ApprovalManager 执行审批。

    返回字符串：要回给飞书用户的回复文本。
    """

    def __init__(
        self,
        *,
        storage: "Storage",
        approval_manager: "ApprovalManager",
    ) -> None:
        self._storage = storage
        self._approval = approval_manager

    async def handle(
        self,
        *,
        chat_id: str,
        sender_open_id: str,
        command: ApprovalCommand,
    ) -> str:
        pending = self._list_pending_for_chat(chat_id)
        if not pending:
            return "🛡️ 当前 chat 无待审批工具调用。"

        if command.target_prefix:
            matches = [p for p in pending if p.startswith(command.target_prefix)]
            if not matches:
                return f"未找到待审批 #{command.target_prefix}，输入命令查看 chat 内 pending"
            if len(matches) > 1:
                preview = ", ".join(f"#{m[:12]}" for m in matches[:5])
                return f"前缀 '{command.target_prefix}' 匹配多个：{preview}，请用更长前缀"
            memorial_id = matches[0]
        else:
            if len(pending) > 1:
                lines = [f"⚠️ chat 内有 {len(pending)} 个待审批，请指定短 ID："]
                for m in pending[:10]:
                    lines.append(f"  - `/approve {m[:8]}` 或 `/准 {m[:8]}`")
                return "\n".join(lines)
            memorial_id = pending[0]

        try:
            decree = await self._approval.submit_tool_decision(
                memorial_id=memorial_id,
                action=command.action,
                grant_scope=command.scope if command.action == "approve" else None,
                actor=f"feishu:{sender_open_id}",
            )
        except ValueError as exc:
            # pending 已被 web 端响应（幂等）
            logger.info("[feishu/approval] submit skipped: %s", exc)
            return f"敕令 #{memorial_id[:8]} 已被其他通道响应。"

        if command.action == "reject":
            return f"❌ 已拒绝 #{memorial_id[:8]}"
        # 用 decree 的实际 scope（可能被安全降级，如 shell_exec 的 always→once）
        actual_scope = decree.grant_scope or "once"
        scope_label = {"once": "单次", "edict": "本敕令", "always": "总是"}.get(
            actual_scope, "单次"
        )
        # 用户请求的 scope 与实际不一致（如 always→once）→ 显式提示降级，
        # 避免用户以为永久放行了，下次又遇到同一审批时困惑
        if command.scope and command.scope != actual_scope:
            requested_label = {
                "once": "单次", "edict": "本敕令", "always": "总是",
            }.get(command.scope, command.scope)
            return (
                f"✅ 已批准 #{memorial_id[:8]}（{scope_label}，"
                f"原请求 {requested_label} 因安全策略降级 —— "
                f"shell_exec 等高危工具不可永久放行）"
            )
        return f"✅ 已批准 #{memorial_id[:8]}（{scope_label}）"

    def _list_pending_for_chat(self, chat_id: str) -> list[str]:
        """从 feishu_pending_cards 反查该 chat 下尚未响应的 memorial_id。"""
        rows = self._storage._conn.execute(
            "SELECT approval_id FROM feishu_pending_cards "
            "WHERE chat_id = ? AND kind = 'tool.approval_required' "
            "ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
        return [r[0] for r in rows]


__all__ = [
    "ApprovalCommand",
    "ApprovalCommandHandler",
    "parse_approval_command",
]
