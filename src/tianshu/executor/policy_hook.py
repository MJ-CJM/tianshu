"""PolicyHook — BEFORE_TOOL_CALL 的 priority=50 handler，内部委托 PolicyEngine。

设计要点：
- 不替换 ApprovalManager 的 handler（priority=10），两者共存于同一 hook 链。
- PolicyHook 先跑（pre-filter），ApprovalManager 的残留 UI 代码后跑（实际 Step 2.4 会把它砍成无操作）
  —— 所以 PolicyHook 用 priority=5，让它先触发。
- 决策事件通过 storage.append_event("policy.decision", ...) 写入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from tianshu.executor.workspace_context import WorkspaceBindingError, resolve_workspace_root
from tianshu.kernel.hooks import HookResult
from tianshu.security.sensitive_payload import redact_sensitive_mapping
from tianshu.tools.policy import PolicyContext, PolicyDecision, PolicyEngine
from tianshu.tools.types import ToolTier

logger = logging.getLogger(__name__)


class PolicyHook:
    """内嵌 PolicyEngine + SessionRuleStore 查询的 BEFORE_TOOL_CALL handler。"""

    def __init__(
        self,
        engine: PolicyEngine,
        workspace_root: Path,
        storage: object,
        tool_registry: object,
        session_rule_store: object | None = None,
        approval_manager: object | None = None,
        notifier: object | None = None,
        event_bus: object | None = None,
        silijian: object | None = None,
    ) -> None:
        self._engine = engine
        self._workspace_root = workspace_root.resolve()
        self._storage = storage
        self._tool_registry = tool_registry
        self._session_rule_store = session_rule_store
        self._approval_manager = approval_manager
        self._notifier = notifier
        self._event_bus = event_bus
        self._silijian = silijian

    async def on_before_tool_call(self, **context: object) -> HookResult | None:
        tool_name = context.get("tool_name")
        tool_args = context.get("tool_args") or {}
        edict = context.get("edict")
        memorial = context.get("memorial")
        iteration = context.get("iteration") or 0

        if not tool_name or not edict:
            return None  # 没上下文就放行，交给别的 handler

        try:
            workspace_root = resolve_workspace_root(self._workspace_root)
        except WorkspaceBindingError as exc:
            return HookResult(
                block=True,
                reason=f"workspace binding rejected tool policy evaluation: {exc}",
            )

        # 解析 tool tier（fail-secure → 缺失 = T3）
        defn = self._tool_registry.get_definition(tool_name) if self._tool_registry else None
        tier_val = defn.tier if defn else ToolTier.T4_DANGEROUS.value
        try:
            tool_tier = ToolTier(int(tier_val))
        except (TypeError, ValueError):
            tool_tier = ToolTier.T4_DANGEROUS

        ctx = PolicyContext(
            tool_name=str(tool_name),
            tool_tier=tool_tier,
            args=dict(tool_args) if isinstance(tool_args, dict) else {},
            edict=edict,  # type: ignore[arg-type]
            memorial=memorial,  # type: ignore[arg-type]
            workspace_root=workspace_root,
            iteration=int(iteration),
        )

        decision = await self._engine.evaluate(ctx)

        # Session rule cache — 仅对 require_approval 查询（Step 3 启用）
        #
        # 越级的官员不吃这层缓存（issue #48）：session rule 的匹配键是
        # tool_name + arg_fingerprint(+edict_id)，没有 persona 维度。而越级的
        # 风险源不是操作、是人——"兵部尚书能不能干这件事"与"翰林能不能干"是两个
        # 问题，用同一条缓存回答属语义错配：DAG 各节点共享 edict_id 却绑不同官员，
        # 高职权节点批出的 edict-scope 规则会让同 edict 内的受限官员零审批越级；
        # always-scope 规则更是跨 edict 对所有官员生效 30 天。
        #
        # **必须独立判定越级，不能看 decision.rule_id**：PolicyEngine 遇
        # require_approval 即短路，persona_tier(15) 排在 workspace(90)/bash(80)/
        # network(75)/approval-list(70) 之后，只要其中任一条先返回 require_approval
        # 就轮不到它评估。若按胜出规则的 rule_id 判断，一个 tier_max=1 的官员调
        # shell_exec 执行 `git push` 会得到 rule_id="bash_safety"，越级事实被掩盖、
        # 照样吃缓存——那正是本 issue 要堵的洞。
        #
        # 名单条款（denied/not_allowed）不受影响：它是硬 deny，在本段之前就返回了。
        exceeds_tier = self._persona_exceeds_tier(ctx)
        if (
            decision.verdict == "require_approval"
            and not exceeds_tier
            and self._session_rule_store is not None
        ):
            rule = await self._session_rule_store.find_match(
                tool_name=ctx.tool_name,
                args=ctx.args,
                edict_id=getattr(edict, "id", None),
            )
            if rule is not None:
                decision = PolicyDecision(
                    verdict="allow",
                    rule_id=f"session_rule:{rule.rule_id}",
                    reason=f"matched session rule (scope={rule.scope}, source={rule.source})",
                    metadata={
                        "session_rule_id": rule.rule_id,
                        "scope": rule.scope,
                        "source": rule.source,
                    },
                )
                self._emit_event(ctx, "policy.session_rule_matched", decision)

        self._emit_event(ctx, "policy.decision", decision)

        if decision.verdict == "allow":
            return HookResult(authorization_source="policy-engine")
        if decision.verdict == "deny":
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] {decision.reason}",
            )
        if decision.verdict == "require_approval":
            return await self._request_approval(
                ctx,
                decision,
                invocation_id=context.get("invocation_id"),
                messages=context.get("messages"),
                usage=context.get("usage"),
                persona_exceeds_tier=exceeds_tier,
            )
        return None

    @staticmethod
    def _persona_exceeds_tier(ctx: PolicyContext) -> bool:
        """当前官员这次调用是否属越级（issue #48）。

        独立于 PolicyEngine 的胜出规则判定——引擎短路会让 persona_tier(15)
        在安全规则先行 require_approval 时根本不评估，届时 decision.rule_id
        指向别的规则，越级事实被掩盖。这里直接用同一份 ACL 语义重算。
        """
        from tianshu.kernel.ambient import get_current_persona
        from tianshu.persona.match import persona_tool_verdict

        persona = get_current_persona()
        if persona is None:
            return False
        try:
            return persona_tool_verdict(persona, ctx.tool_name, int(ctx.tool_tier)) == (
                "tier_exceeded"
            )
        except Exception:  # noqa: BLE001 - 判定异常时按不越级处理，保持既有缓存行为
            logger.exception("policy_hook: persona tier check failed")
            return False

    async def _request_approval(
        self,
        ctx: PolicyContext,
        decision: PolicyDecision,
        *,
        invocation_id: object,
        messages: object,
        usage: object,
        persona_exceeds_tier: bool = False,
    ) -> HookResult | None:
        """Persist a durable tool suspension, notify, then poll generic authority."""
        if self._approval_manager is None:
            logger.error("policy_hook: require_approval but no ApprovalManager configured")
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no approval manager",
            )

        memorial_id = getattr(ctx.memorial, "id", None) if ctx.memorial else None
        if not memorial_id:
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no memorial context",
            )
        if not isinstance(invocation_id, str) or not invocation_id.strip():
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no tool invocation identity",
            )
        if not isinstance(messages, list) or usage is None:
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval required but no continuation state",
            )

        request = self._approval_manager.request_tool_decision(  # type: ignore[attr-defined]
            edict=ctx.edict,
            memorial=ctx.memorial,
            invocation_id=invocation_id,
            tool_name=ctx.tool_name,
            tool_args=ctx.args,
            tool_tier=ctx.tool_tier.name,
            policy_rule_id=decision.rule_id,
            persona_exceeds_tier=persona_exceeds_tier,
            messages=messages,
            iteration=ctx.iteration,
            usage=usage,
        )

        auto_resolved = False
        # 越级奏请不许司礼监代批（issue #48 的同一条理由）：代批的四道闸看的是
        # tool_tier 与该**操作**近期的人工通过率，没有 persona 维度。而越级问的是
        # "这个人该不该做这件事"——拿操作维度的历史统计替人放行，与 session rule
        # 缓存湮灭是同一种错配。且默认 silijian_max_tier=1 恰好覆盖 tier_max=0
        # 官员调 T1 工具这一最常见的越级形态，不挡住等于越级审批形同虚设。
        if self._silijian is not None and not persona_exceeds_tier:
            proposal = self._silijian.maybe_auto_approve(  # type: ignore[attr-defined]
                memorial_id, ctx.tool_tier, decision.rule_id
            )
            if proposal is not None:
                try:
                    self._approval_manager.resolve_tool_decision_as_silijian(  # type: ignore[attr-defined]
                        request,
                        reason=proposal.reason,
                    )
                except Exception:
                    logger.exception(
                        "policy_hook: Silijian proposal failed generic resolution; "
                        "falling back to human review"
                    )
                else:
                    auto_resolved = True
                    self._emit_event(ctx, "decree.auto_approved", decision)

        # 写事件，触发前端 toast
        approval_payload = {
            "decision_request_id": request.decision_request_id,
            "tool_name": ctx.tool_name,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "tool_tier": ctx.tool_tier.name,
            "args_summary": self._summarize_args(ctx.args),
        }
        edict_id = getattr(ctx.edict, "id", "")

        # tool.approval_required 通过 EventBus.fire 单点持久化（_persist 自动写入 events 表）
        # + 派发给订阅者（飞书机器人等）。避免双写导致 PolicyTimeline/EventTimeline 重复显示。
        if not auto_resolved and self._event_bus is not None:
            try:
                from tianshu.models.events import make_event

                self._event_bus.fire(  # type: ignore[attr-defined]
                    make_event(
                        "tool.approval_required",
                        edict_id=edict_id,
                        memorial_id=memorial_id,
                        producer="policy_hook",
                        payload=approval_payload,
                    )
                )
            except Exception:
                logger.exception("policy_hook: failed to fire tool.approval_required to EventBus")

        # Broadcast to WS so frontend can show toast
        if not auto_resolved and self._notifier is not None:
            try:
                import asyncio

                coro = self._notifier.broadcast_ws(
                    {  # type: ignore[attr-defined]
                        "type": "tool.approval_required",
                        "edict_id": edict_id,
                        "memorial_id": memorial_id,
                        "payload": approval_payload,
                    }
                )
                asyncio.create_task(coro)
            except Exception:
                logger.exception("policy_hook: failed to broadcast tool.approval_required")

        resolution = await self._approval_manager.wait_for_tool_decision(  # type: ignore[attr-defined]
            request.decision_request_id
        )
        if resolution is None:
            return HookResult(
                block=True,
                reason=f"[{decision.rule_id}] approval timed out or rejected",
            )
        if resolution.action == "approve":
            return HookResult(authorization_source="policy-engine")
        if resolution.action == "guide":
            # 驳回+指导(迭代 5):驳回本次工具,但把纠正意见注入,agent 据此换方式续跑
            return HookResult(
                block=True,
                reason=(
                    f"[{decision.rule_id}] 本次操作被驳回并给出指导:"
                    f"{resolution.payload.get('guidance') or '(未附具体指导)'}。"
                    "请据此调整方式后重试,不要重复原操作。"
                ),
            )
        return HookResult(
            block=True,
            reason=f"[{decision.rule_id}] rejected by decision: {resolution.reason}",
        )

    def _emit_event(
        self,
        ctx: PolicyContext,
        event_type: str,
        decision: PolicyDecision,
    ) -> None:
        edict_id = getattr(ctx.edict, "id", None)
        memorial_id = getattr(ctx.memorial, "id", None) if ctx.memorial else None
        if not edict_id:
            return
        payload = {
            "tool_name": ctx.tool_name,
            "tool_tier": ctx.tool_tier.name,
            "verdict": decision.verdict,
            "rule_id": decision.rule_id,
            "reason": decision.reason,
            "iteration": ctx.iteration,
            "args_summary": self._summarize_args(ctx.args),
            "metadata": decision.metadata,
        }
        try:
            self._storage.append_event(  # type: ignore[attr-defined]
                edict_id,
                memorial_id,
                event_type,
                payload,
            )
        except Exception:
            logger.exception("policy_hook: failed to write %s event", event_type)

        # Broadcast deny decisions to WS so frontend can show toast
        if (
            self._notifier is not None
            and event_type == "policy.decision"
            and decision.verdict == "deny"
        ):
            try:
                import asyncio

                coro = self._notifier.broadcast_ws(
                    {  # type: ignore[attr-defined]
                        "type": "policy.decision",
                        "edict_id": edict_id,
                        "memorial_id": memorial_id,
                        "payload": payload,
                    }
                )
                asyncio.create_task(coro)
            except Exception:
                logger.exception("policy_hook: failed to broadcast policy.decision")

    @staticmethod
    def _summarize_args(args: dict) -> dict:
        """每字段截断到 200 字符，避免事件 payload 过大。"""
        out: dict = {}
        for k, v in redact_sensitive_mapping(args).items():
            if isinstance(v, str):
                out[k] = v if len(v) <= 200 else v[:200] + "...[truncated]"
            else:
                out[k] = v
        return out
