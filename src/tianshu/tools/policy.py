"""Policy Engine — 事前权限决策核心。

Spec: docs/superpowers/specs/2026-04-14-tool-policy-pipeline-design.md (Section 3)

设计原则：
- 所有数据模型都是 frozen dataclass（immutable → 测试与并发友好）
- PolicyRule 是 Protocol → 便于 fake rule 注入
- fail-open 单条规则 + fail-secure 整体引擎
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tianshu.tools.types import ToolTier

if TYPE_CHECKING:
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial

logger = logging.getLogger(__name__)

POLICY_RULE_TIMEOUT = 1.0      # 单条规则 1s
POLICY_ENGINE_TIMEOUT = 3.0    # 引擎整体 3s


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的历史记录（未来组合策略用，初版仅预留）。"""

    tool_name: str
    args_summary: dict[str, Any]
    verdict: str
    iteration: int
    timestamp: datetime


@dataclass(frozen=True)
class PolicyContext:
    """一次工具调用的完整决策上下文。"""

    tool_name: str
    tool_tier: ToolTier
    args: dict[str, Any]
    edict: Edict
    memorial: Memorial | None
    workspace_root: Path
    iteration: int
    recent_calls: tuple[ToolCallRecord, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """策略决策结果 — verdict + rule_id + reason 是决策可追溯的最小单元。"""

    verdict: Literal["allow", "deny", "require_approval"]
    rule_id: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


class PolicyRule(Protocol):
    rule_id: str
    priority: int  # 高优先级先跑

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        """返回 None 表示弃权（让下一条规则决定）。"""
        ...


class PolicyEngine:
    """按 priority 执行 rules，遇 deny/require_approval 短路。

    全局可观测 + fail-secure 引擎：
    - 单规则抛异常 / 超时 → 弃权 + WARNING
    - 引擎整体超时 / 未知异常 → deny + ERROR（对应 policy_deny_due_to_engine_error_total 指标）
    - 所有规则弃权 → allow（默认）
    """

    def __init__(self, rules: list[PolicyRule]) -> None:
        # priority 高的先跑 → 降序排序
        self._rules: tuple[PolicyRule, ...] = tuple(
            sorted(rules, key=lambda r: r.priority, reverse=True)
        )

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        try:
            return await asyncio.wait_for(
                self._evaluate_inner(ctx),
                timeout=POLICY_ENGINE_TIMEOUT,
            )
        except TimeoutError:
            logger.error(
                "PolicyEngine timeout (>%.1fs) for tool=%s — fail-secure deny",
                POLICY_ENGINE_TIMEOUT, ctx.tool_name,
            )
            return PolicyDecision(
                verdict="deny",
                rule_id="engine_timeout",
                reason=f"PolicyEngine exceeded {POLICY_ENGINE_TIMEOUT}s timeout",
            )
        except Exception as e:
            logger.exception(
                "PolicyEngine unexpected error for tool=%s — fail-secure deny",
                ctx.tool_name,
            )
            return PolicyDecision(
                verdict="deny",
                rule_id="engine_error",
                reason=f"PolicyEngine failed: {type(e).__name__}: {e}",
            )

    async def _evaluate_inner(self, ctx: PolicyContext) -> PolicyDecision:
        last_allow: PolicyDecision | None = None
        for rule in self._rules:
            rule_start = time.monotonic()
            try:
                decision = await asyncio.wait_for(
                    rule.evaluate(ctx),
                    timeout=POLICY_RULE_TIMEOUT,
                )
            except TimeoutError:
                logger.warning(
                    "Rule %s timed out (>%.1fs) — abstain",
                    rule.rule_id, POLICY_RULE_TIMEOUT,
                )
                continue
            except Exception:
                logger.exception(
                    "Rule %s raised — abstain", rule.rule_id,
                )
                continue

            elapsed = time.monotonic() - rule_start
            logger.debug(
                "[POLICY] rule=%s verdict=%s elapsed=%.3fs",
                rule.rule_id,
                decision.verdict if decision else "abstain",
                elapsed,
            )

            if decision is None:
                continue  # 弃权
            if decision.verdict in ("deny", "require_approval"):
                return decision  # 短路
            if decision.verdict == "allow":
                last_allow = decision  # 不短路，允许后续规则覆盖

        # 所有规则弃权 or 仅有 allow → 用最后的 allow 或默认
        if last_allow is not None:
            return last_allow
        return PolicyDecision(
            verdict="allow",
            rule_id="default",
            reason="all rules abstained",
        )
