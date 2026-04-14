"""BashSafetyRule — T3 shell 类工具的黑白名单。

黑名单永远生效（profile 也不能覆盖）。白名单来自 profile.allowed_bash_prefixes。
未命中白名单 → 进入默认审批流（返回 require_approval）。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.tools.policy import PolicyContext, PolicyDecision

BASH_TOOL_NAMES = {"shell_exec", "bash"}

# 黑名单 — 一旦子串匹配即 deny（大小写不敏感）
BASH_DENYLIST_SUBSTRINGS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "dd of=/dev",
    ":(){:|:&};:",             # fork bomb
    "sudo ",
    "curl | sh",
    "curl|sh",
    "wget | sh",
    "wget|sh",
    "git push --force",
    "git push -f ",
    "chmod 777 /",
    "> /dev/sda",
)


@dataclass
class BashSafetyRule:
    rule_id: str = "bash_safety"
    priority: int = 80

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        if ctx.tool_name not in BASH_TOOL_NAMES:
            return None

        command = (ctx.args.get("command") or "").strip()
        if not command:
            return None

        lowered = command.lower()

        # 1. 黑名单（最高优先，即使 profile 白名单也不能覆盖）
        for needle in BASH_DENYLIST_SUBSTRINGS:
            if needle in lowered:
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"command matched deny substring {needle!r}",
                    metadata={"matched_pattern": needle},
                )

        # 2. 白名单（来自 profile）
        allowed_prefixes = self._resolve_profile_prefixes(ctx)
        if allowed_prefixes and any(command.startswith(p) for p in allowed_prefixes):
            return PolicyDecision(
                verdict="allow",
                rule_id=self.rule_id,
                reason="command matched profile bash prefix allow-list",
                metadata={"matched_prefix": next(p for p in allowed_prefixes if command.startswith(p))},
            )

        # 3. 未命中 → T3 默认审批
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"bash command requires approval: {command[:80]}",
            metadata={"command_preview": command[:200]},
        )

    @staticmethod
    def _resolve_profile_prefixes(ctx: PolicyContext) -> tuple[str, ...]:
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        if profile is None:
            return ()
        return tuple(getattr(profile, "allowed_bash_prefixes", ()) or ())
