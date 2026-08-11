"""官员工具 ACL 从声明变为强制（issue #40）。

`persona/match.py` 的语义（deny 优先 → allow 白名单 → tier 兜底）此前只被
skills_api 用于可见性过滤，executor 一侧从未接入——SOUL.md 里写
``tools_allowed: ["read_file"]`` 的官员照样能调 shell_exec。本模块把同一份
语义接进 PolicyEngine，拆成**两条不同优先级**的规则：

- ``PersonaToolAclRule`` (priority=110)：只裁 denied / not_allowed 两个
  **名单条款**，硬 deny。职权契约先于一切，deny 短路无害（本就该拒）。
- ``PersonaTierRule`` (priority=15)：只裁 tier_exceeded，require_approval。

两条**必须分开**：越级奏请是 require_approval，而 PolicyEngine 遇
require_approval 即短路。若 tier 条款也排在 110，一个 tier_max=2 的官员调
shell_exec 执行黑名单命令（sudo rm -rf /）会先在 110 拿到 require_approval，
BashSafetyRule(80) 的黑名单硬 deny 从此不再评估——无条件硬拒的命令变成
一键可批。故 tier 越级必须排在所有安全 deny 规则（workspace 90 / bash 80 /
network 75 / approval-list 70）**之后**，只在它们都放行时才兜底奏请。

persona 取自 ambient ContextVar（agent.py 以 ``bind_persona`` 包住执行循环
与 hook chain，与 memory_tools / safe_path 同一取值模式）；无 persona
上下文（助手分支、CLI 直调）→ 弃权，行为与本机制引入前逐字节一致。

注意 T0 工具在 agent 层走快路径绕过 hook chain，本规则罩不到——
deny/allow 名单条款在 registry.execute 另有一道执行层兜底（两道墙须同源，
见 tests/tools/test_persona_acl_enforcement.py）。tier 条款不在 registry
兜底：越级须走本模块的奏请审批，registry 不越权硬拒。
"""

from __future__ import annotations

from dataclasses import dataclass

from tianshu.kernel.ambient import get_current_persona
from tianshu.persona.match import persona_tool_verdict
from tianshu.tools.policy import PolicyContext, PolicyDecision


def _clause(ctx: PolicyContext) -> tuple[object, str | None]:
    """返回 (persona, 条款)；persona 为 None 表示无上下文。"""
    persona = get_current_persona()
    if persona is None:
        return None, None
    return persona, persona_tool_verdict(persona, ctx.tool_name, int(ctx.tool_tier))


def _meta(persona: object, clause: str) -> dict:
    return {
        "persona_id": getattr(persona, "id", None),
        "clause": clause,
        "tool_tier_max": getattr(persona, "tool_tier_max", None),
    }


@dataclass
class PersonaToolAclRule:
    """官员工具名单契约——硬 deny，先于一切审批通道。"""

    rule_id: str = "persona_tool_acl"
    priority: int = 110

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        persona, clause = _clause(ctx)
        if clause not in ("denied", "not_allowed"):
            return None  # tier_exceeded 交给低优先级的 PersonaTierRule；None 放行
        detail = (
            f"工具 {ctx.tool_name} 在官员 {persona.id} 的 tools_denied 名单中"
            if clause == "denied"
            else f"工具 {ctx.tool_name} 不在官员 {persona.id} 的 tools_allowed 名单中"
        )
        return PolicyDecision(
            verdict="deny", rule_id=self.rule_id, reason=detail, metadata=_meta(persona, clause)
        )


@dataclass
class PersonaTierRule:
    """官员职权 tier 越级——奏请审批。排在所有安全 deny 规则之后。"""

    rule_id: str = "persona_tier"
    priority: int = 15

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        persona, clause = _clause(ctx)
        if clause != "tier_exceeded":
            return None
        return PolicyDecision(
            verdict="require_approval",
            rule_id=self.rule_id,
            reason=(
                f"官员 {persona.id} 职权上限 T{persona.tool_tier_max}，"
                f"调用 {ctx.tool_name}（{ctx.tool_tier.name}）属越级，须奏请批准"
            ),
            metadata=_meta(persona, clause),
        )
