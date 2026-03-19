"""Rules engine — fast synchronous checks on execution results."""

from __future__ import annotations

import logging

from tianshu.models.common import AuditResult
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial

logger = logging.getLogger(__name__)


class RulesEngine:
    """Layer 1: synchronous rule checks (fast, no LLM)."""

    def check(self, edict: Edict, memorial: Memorial) -> AuditResult:
        reasons: list[str] = []
        rules_checked = 0

        # Rule 1: Token budget
        rules_checked += 1
        if edict.runtime.token_budget and memorial.usage.total_tokens > edict.runtime.token_budget:
            reasons.append(
                f"Token usage ({memorial.usage.total_tokens}) exceeds budget "
                f"({edict.runtime.token_budget})"
            )

        # Rule 2: Execution errors
        rules_checked += 1
        if memorial.error:
            reasons.append(f"Execution error: {memorial.error}")

        # Rule 3: Empty result
        rules_checked += 1
        if not memorial.result and not memorial.error:
            reasons.append("No result produced")

        # Determine verdict
        if not reasons:
            verdict = "pass"
        elif any("error" in r.lower() for r in reasons):
            verdict = "flag"
        else:
            verdict = "flag"

        return AuditResult(
            verdict=verdict,
            reasons=reasons,
            rules_checked=rules_checked,
        )
