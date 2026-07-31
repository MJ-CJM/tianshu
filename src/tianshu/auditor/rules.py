"""Rules engine — fast synchronous checks on execution results."""

from __future__ import annotations

import logging

from tianshu.auditor.rules_config import AuditRulesConfig
from tianshu.models.common import AuditResult
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial

logger = logging.getLogger(__name__)


class RulesEngine:
    """Layer 1: synchronous rule checks (fast, no LLM)."""

    def __init__(self, config: AuditRulesConfig | None = None) -> None:
        self._config = config or AuditRulesConfig()

    def check(self, edict: Edict, memorial: Memorial) -> AuditResult:
        reasons: list[str] = []
        rules_checked = 0

        # Rule 1: Token budget
        if self._config.check_token_budget:
            rules_checked += 1
            if (
                edict.runtime.token_budget
                and memorial.usage.total_tokens > edict.runtime.token_budget
            ):
                reasons.append(
                    f"Token usage ({memorial.usage.total_tokens}) exceeds budget "
                    f"({edict.runtime.token_budget})"
                )

        # Rule 2: Execution errors
        if self._config.check_execution_error:
            rules_checked += 1
            if memorial.error:
                reasons.append(f"Execution error: {memorial.error}")

        # Rule 3: Empty result
        if self._config.check_empty_result:
            rules_checked += 1
            if not memorial.result and not memorial.error:
                reasons.append("No result produced")

        # Rule 4: Configured risk vocabulary.  Count it as one rule regardless
        # of keyword count so the audit metric describes checks, not inputs.
        if self._config.risk_keywords:
            rules_checked += 1
            result_text = (memorial.result or "").casefold()
            for keyword in self._config.risk_keywords:
                if keyword.casefold() in result_text:
                    reasons.append(f"Risk keyword detected: {keyword}")

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
