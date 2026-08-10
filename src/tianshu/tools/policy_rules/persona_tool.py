"""PersonaToolRule — 官员工具 ACL 从声明变为强制（issue #40）。

`persona/match.py` 的语义（deny 优先 → allow 白名单 → tier 兜底）此前只被
skills_api 用于可见性过滤，executor 一侧从未接入——SOUL.md 里写
``tools_allowed: ["read_file"]`` 的官员照样能调 shell_exec。本规则把同一份
语义接进 PolicyEngine。

条款处置（persona_tool_verdict）：

- ``denied`` / ``not_allowed`` —— 官员职权契约，硬 deny。不给审批机会：
  审批按钮是诱导攻击面（与 WorkspaceBoundaryRule 拒走审批同理），且职权
  不足应改派更高职权的官员，而不是替这一次点放行。
- ``tier_exceeded`` —— 越级奏请，require_approval。与 DefaultTierRule /
  bash_safety 的既有审批 UX 一致；佳民批一次放一次。

priority=110，压过 TierEscalationRule(100)：引擎按 priority 短路，若排在
tier 规则之后，被禁工具会先拿到 require_approval 而绕过硬 deny。

persona 取自 ambient ContextVar（agent.py 以 ``bind_persona`` 包住执行循环，
与 memory_tools / safe_path 同一取值模式）；无 persona 上下文（助手分支、
CLI 直调）→ 弃权，行为与本规则引入前逐字节一致。

注意 T0 工具在 agent 层就走快路径绕过 hook chain，本规则罩不到——
deny/allow 条款在 registry.execute 另有一道执行层兜底（两道墙须同源，
见 tests/tools/test_persona_acl_enforcement.py）。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.kernel.ambient import get_current_persona
from tianshu.persona.match import persona_tool_verdict
from tianshu.tools.policy import PolicyContext, PolicyDecision


@dataclass
class PersonaToolRule:
    rule_id: str = "persona_tool_acl"
    priority: int = 110

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        persona = get_current_persona()
        if persona is None:
            return None  # 无官员上下文 → 本维度无约束

        clause = persona_tool_verdict(persona, ctx.tool_name, int(ctx.tool_tier))
        if clause is None:
            return None  # 放行 ≠ allow 短路：工作区/网络等其他规则仍须评估

        meta = {
            "persona_id": persona.id,
            "clause": clause,
            "tool_tier_max": persona.tool_tier_max,
        }
        if clause == "tier_exceeded":
            return PolicyDecision(
                verdict="require_approval",
                rule_id=self.rule_id,
                reason=(
                    f"官员 {persona.id} 职权上限 T{persona.tool_tier_max}，"
                    f"调用 {ctx.tool_name}（{ctx.tool_tier.name}）属越级，须奏请批准"
                ),
                metadata=meta,
            )
        detail = (
            f"工具 {ctx.tool_name} 在官员 {persona.id} 的 tools_denied 名单中"
            if clause == "denied"
            else f"工具 {ctx.tool_name} 不在官员 {persona.id} 的 tools_allowed 名单中"
        )
        return PolicyDecision(verdict="deny", rule_id=self.rule_id, reason=detail, metadata=meta)
