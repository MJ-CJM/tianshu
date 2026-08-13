"""Synthesizer — LLM-based synthesis of multi-persona opinions."""

from __future__ import annotations

import logging

from tianshu.config_manager import ConfigManager
from tianshu.consultation.models import ConsultationRequest, PersonaOpinion
from tianshu.llm import LLMUsageContext

logger = logging.getLogger(__name__)


class Synthesizer:
    """Synthesizes multiple persona opinions into a unified decision."""

    def __init__(
        self,
        config_manager: ConfigManager,
        provider_manager: object | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._provider_manager = provider_manager

    async def synthesize(
        self,
        request: ConsultationRequest,
        opinions: list[PersonaOpinion],
        *,
        persona: object | None = None,
        usage_context: LLMUsageContext | None = None,
    ) -> dict:
        """Combine multiple opinions into a synthesis and decision.

        `persona` 是执笔汇聚的官员（`request.synthesizer_persona_id` 解析所得）；
        留空则沿用通用「首席顾问」身份。前端据此为综合/决策署名（issue #54）。
        """
        from tianshu.llm import LLMClient

        state = self._config_manager.state
        if self._provider_manager and hasattr(self._provider_manager, "get_client_for_slot"):
            llm = self._provider_manager.get_client_for_slot("court")
        elif self._provider_manager and hasattr(self._provider_manager, "get_client"):
            llm = self._provider_manager.get_client()
        else:
            llm = LLMClient(
                model=state.model,
                api_key=state.api_key,
                api_base=state.api_base,
            )

        opinions_text = "\n\n".join(
            f"**{o.persona_name}** ({o.department}, 立场={o.stance}"
            + (f", 条件=[{'; '.join(o.conditions)}]" if o.conditions else "")
            + (" 【言官·反调】" if o.is_censor else "")
            + f"):\n{o.opinion}"
            for o in opinions
        )

        if persona is not None:
            identity = (
                f"You are {persona.name} from the {persona.department} department, "
                f"acting as the chief counselor"
            )
            system_identity = f"You are {persona.name}, {persona.department}, "
        else:
            identity = "You are the chief counselor"
            system_identity = "You are a senior advisor "

        prompt = (
            f"{identity} synthesizing opinions on the following topic.\n\nTopic: {request.topic}\n"
        )
        if request.context:
            prompt += f"\nContext: {request.context}\n"

        prompt += (
            f"\n## Opinions from officials:\n\n{opinions_text}\n\n"
            f"## Your task:\n"
            f"1. Synthesize the key themes and agreements/disagreements\n"
            f"2. Provide a clear decision recommendation\n"
            f"Format your response as:\n"
            f"### Synthesis\n[your synthesis]\n\n### Decision\n[your decision]"
        )

        messages = [
            {
                "role": "system",
                "content": f"{system_identity}synthesizing multi-perspective analysis.",
            },
            {"role": "user", "content": prompt},
        ]

        if usage_context is None:
            response = await llm.chat(messages)
        else:
            response = await llm.chat(messages, usage_context=usage_context)
        content = response.content or ""

        # Parse synthesis and decision
        synthesis = content
        decision = ""
        if "### Decision" in content:
            parts = content.split("### Decision", 1)
            synthesis = parts[0].replace("### Synthesis", "").strip()
            decision = parts[1].strip()
        elif "### Synthesis" in content:
            synthesis = content.split("### Synthesis", 1)[1].strip()

        return {"synthesis": synthesis, "decision": decision}
