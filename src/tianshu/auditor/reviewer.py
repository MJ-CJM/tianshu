"""LLM Reviewer — deep review for flagged items."""

from __future__ import annotations

import json
import logging

from tianshu.config_manager import ConfigManager
from tianshu.llm import LLMClient
from tianshu.models.common import AuditResult
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial

logger = logging.getLogger(__name__)

_REVIEW_PROMPT = """\
You are a quality reviewer. Given an execution result, assess whether it meets the goal.

Respond in JSON: {"verdict": "pass"|"flag"|"block", "reasons": ["..."]}

- "pass": result satisfactorily addresses the goal
- "flag": result has issues that need human review
- "block": result is clearly wrong or harmful
"""


class LLMReviewer:
    """Layer 2: LLM-based review for items flagged by rules engine."""

    def __init__(self, config_manager: ConfigManager) -> None:
        self._config_manager = config_manager

    async def review(
        self,
        edict: Edict,
        memorial: Memorial,
        rule_reasons: list[str],
    ) -> AuditResult:
        state = self._config_manager.state
        if not state.enabled:
            return AuditResult(
                verdict="flag",
                reasons=rule_reasons + ["LLM reviewer unavailable"],
                llm_reviewed=False,
            )

        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            max_retries=1,
            temperature=0.1,
            max_tokens=512,
        )

        user_msg = (
            f"Goal: {edict.goal}\n\n"
            f"Result: {(memorial.result or '')[:2000]}\n\n"
            f"Rule flags: {', '.join(rule_reasons)}"
        )

        try:
            response = await llm.chat(
                [
                    {"role": "system", "content": _REVIEW_PROMPT},
                    {"role": "user", "content": user_msg},
                ]
            )
            if response.content:
                data = json.loads(response.content)
                return AuditResult(
                    verdict=data.get("verdict", "flag"),
                    reasons=data.get("reasons", rule_reasons),
                    llm_reviewed=True,
                )
        except Exception:
            logger.exception("LLM review failed for memorial %s", memorial.id)

        return AuditResult(
            verdict="flag",
            reasons=rule_reasons + ["LLM review failed"],
            llm_reviewed=False,
        )
