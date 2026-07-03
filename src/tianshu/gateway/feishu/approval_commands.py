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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from tianshu.gateway.core.approval import ApprovalCommandHandler as _CoreApprovalCommandHandler

if TYPE_CHECKING:
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.storage import Storage

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
    target_prefix: str | None  # memorial_id 前缀，≥6 字符；None 表示不指定


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
        suffix = head[len("/准") :]
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


class ApprovalCommandHandler(_CoreApprovalCommandHandler):
    """根据已解析的 ApprovalCommand 调用 ApprovalManager 执行审批（飞书数据访问）。

    命令语义（prefix 匹配 / 降级提示 / 回复文案）已上移
    core.approval.ApprovalCommandHandler；本类只保留飞书侧的 pending 反查
    实现 + actor 前缀。
    """

    def __init__(
        self,
        *,
        storage: Storage,
        approval_manager: ApprovalManager,
        instance_id: str = "feishu-default",
    ) -> None:
        self._storage = storage
        self._instance_id = instance_id
        super().__init__(
            approval_manager=approval_manager,
            list_pending=self._list_pending_for_chat,
            actor_prefix="feishu",
        )

    def _list_pending_for_chat(self, chat_id: str) -> list[str]:
        """从 feishu_pending_cards 反查该 chat 下尚未响应的 memorial_id。"""
        rows = self._storage._conn.execute(
            "SELECT approval_id FROM feishu_pending_cards "
            "WHERE chat_id = ? AND kind = 'tool.approval_required' "
            "AND instance_id = ? "
            "ORDER BY created_at ASC",
            (chat_id, self._instance_id),
        ).fetchall()
        return [r[0] for r in rows]


__all__ = [
    "ApprovalCommand",
    "ApprovalCommandHandler",
    "parse_approval_command",
]
