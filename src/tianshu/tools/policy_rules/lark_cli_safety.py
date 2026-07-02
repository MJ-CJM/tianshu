"""LarkCliSafetyRule —— lark_cli 透传工具的读写门控。

判定（仅作用于 tool_name == "lark_cli"）：
- 交互 / 认证类命令（auth login/logout、config init/reset/delete）→ **deny**
  （防卡死浏览器授权 / 防改动主机凭证；与工具层双重拦截）。
- 命令含写动词（send/create/update/delete…）→ **require_approval**。
- 其余（读操作）→ 弃权（None），落 DefaultTierRule：lark_cli 基础 tier=T2 → 放行。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision

LARK_TOOL_NAMES = {"lark_cli"}

# 写动词：任一非 flag token 命中即需审批（大小写不敏感，去前导 +/-）。
WRITE_VERBS = frozenset(
    {
        "send",
        "reply",
        "create",
        "update",
        "delete",
        "remove",
        "add",
        "set",
        "edit",
        "patch",
        "post",
        "upload",
        "import",
        "complete",
        "cancel",
        "move",
        "copy",
        "share",
        "grant",
        "revoke",
        "archive",
        "rename",
        "invite",
        "kick",
        "transfer",
        "approve",
        "reject",
        "submit",
        "publish",
        "write",
        "insert",
        "append",
        "modify",
        "comment",
    }
)

# 交互 / 认证类命令前缀（按非 flag token 比对）→ deny。
BLOCKED_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("auth", "login"),
    ("auth", "logout"),
    ("config", "init"),
    ("config", "reset"),
    ("config", "delete"),
)


def _non_flag_tokens(args: list[str]) -> list[str]:
    return [a.lstrip("+").lower() for a in args if isinstance(a, str) and not a.startswith("-")]


@dataclass
class LarkCliSafetyRule:
    rule_id: str = "lark_cli_safety"
    priority: int = 80

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in LARK_TOOL_NAMES:
            return None
        raw = ctx.args.get("args")
        if not isinstance(raw, list):
            return None
        tokens = _non_flag_tokens(raw)
        if not tokens:
            return None

        head = tuple(tokens[:2])
        for prefix in BLOCKED_PREFIXES:
            if head[: len(prefix)] == prefix:
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"lark_cli interactive/auth command not allowed: {' '.join(tokens[:2])}",
                    metadata={"command": " ".join(tokens[:2])},
                )

        hit = next((t for t in tokens if t in WRITE_VERBS), None)
        if hit:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason=f"lark_cli write op '{hit}' requires approval",
                metadata={"verb": hit, "cmd_preview": " ".join(tokens[:6])},
            )

        return None  # 读操作 → 落默认 T2 放行
