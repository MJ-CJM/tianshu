"""BashSafetyRule — T3 shell 类工具的分段风险分级(迭代 3「深防御」升级)。

黑名单永远生效（profile 也不能覆盖）。白名单来自 profile.allowed_bash_prefixes。
未命中白名单 → 进入默认审批流（返回 require_approval）。

迭代 3 修复朴素子串/前缀匹配的绕过洞:`git log; rm -rf /` 以白名单前缀
`git ` 开头,旧逻辑 startswith 直接放行。改为 quote-aware 分段后**逐段**判定:
黑名单任一段命中即 deny;白名单须**每段**命中前缀才放行;命令替换/重定向/
后台 & 等结构绕过一律不放行(升级审批)。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.security.bash_analysis import analyze_command
from tianshu.tools.policy import PolicyContext, PolicyDecision

BASH_TOOL_NAMES = {"shell_exec", "bash"}

# 黑名单 — 一旦子串匹配即 deny（大小写不敏感）
BASH_DENYLIST_SUBSTRINGS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=",
    "dd of=/dev",
    ":(){:|:&};:",  # fork bomb
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

        analysis = analyze_command(command)
        # 空分段(纯分隔符/引号)→ 交默认审批,不空放
        segments = analysis.segments or (command,)

        # 1. 黑名单:整串 + 逐段都查(最高优先,profile 白名单也不能覆盖)。
        #    逐段查堵住 `echo ok; rm -rf /` 这类把危险命令藏在后段的绕过。
        for candidate in (command, *segments):
            lowered = candidate.lower()
            for needle in BASH_DENYLIST_SUBSTRINGS:
                if needle in lowered:
                    return PolicyDecision(
                        verdict="deny",
                        rule_id=self.rule_id,
                        reason=f"command segment matched deny substring {needle!r}",
                        metadata={"matched_pattern": needle, "segment": candidate[:120]},
                    )

        # 2. 结构绕过检测:命令替换 / 重定向 / 后台 & 会引入白名单看不见的
        #    隐藏子命令,一律不放行(升级审批)。
        if analysis.has_structural_risk:
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason=f"bash command has structural risk ({', '.join(analysis.structural_notes)})",
                metadata={
                    "command_preview": command[:200],
                    "structural_notes": analysis.structural_notes,
                },
            )

        # 3. 白名单:必须【每一段】都命中前缀才放行(全体达标才安全)。
        allowed_prefixes = self._resolve_profile_prefixes(ctx)
        if allowed_prefixes and all(
            any(seg.startswith(p) for p in allowed_prefixes) for seg in segments
        ):
            return PolicyDecision(
                verdict="allow",
                rule_id=self.rule_id,
                reason="all command segments matched profile bash prefix allow-list",
                metadata={"segments": list(segments)},
            )

        # 4. 未命中 → T3 默认审批
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=f"bash command requires approval: {command[:80]}",
            metadata={"command_preview": command[:200], "segments": list(segments)},
        )

    @staticmethod
    def _resolve_profile_prefixes(ctx: PolicyContext) -> tuple[str, ...]:
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        if profile is None:
            return ()
        return tuple(getattr(profile, "allowed_bash_prefixes", ()) or ())
