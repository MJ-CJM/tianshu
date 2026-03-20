"""ConsultationSession — orchestrate multi-persona parallel analysis."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from tianshu.config_manager import ConfigManager
from tianshu.consultation.models import (
    ConsultationRequest,
    ConsultationResponse,
    PersonaOpinion,
)
from tianshu.consultation.synthesizer import Synthesizer
from tianshu.persona.loader import PersonaLoader
from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)


class ConsultationSession:
    """Runs a consultation: parallel persona analysis + LLM synthesis."""

    def __init__(
        self,
        persona_loader: PersonaLoader,
        config_manager: ConfigManager,
        provider_manager: ProviderManager | None = None,
        memory_manager: object | None = None,
    ) -> None:
        self._personas = persona_loader
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._memory_manager = memory_manager
        self._synthesizer = Synthesizer(config_manager, provider_manager)
        self._sessions: dict[str, ConsultationResponse] = {}

    def get(self, consultation_id: str) -> ConsultationResponse | None:
        return self._sessions.get(consultation_id)

    async def start(self, request: ConsultationRequest) -> ConsultationResponse:
        """Start a consultation session."""
        response = ConsultationResponse(
            request=request,
            status="running",
        )
        self._sessions[response.id] = response

        try:
            # Determine participating personas
            persona_ids = request.persona_ids
            if not persona_ids:
                all_personas = self._personas.load_all()
                persona_ids = list(all_personas.keys())

            # Run parallel analysis
            tasks = []
            for pid in persona_ids:
                persona = self._personas.get(pid)
                if not persona:
                    continue
                tasks.append(self._get_opinion(persona, request))

            opinions = await asyncio.gather(*tasks, return_exceptions=True)
            response.opinions = [
                o for o in opinions if isinstance(o, PersonaOpinion)
            ]

            # Synthesize
            if response.opinions:
                synthesis_result = await self._synthesizer.synthesize(
                    request, response.opinions,
                )
                response.synthesis = synthesis_result.get("synthesis", "")
                response.decision = synthesis_result.get("decision", "")

            response.status = "completed"
            response.completed_at = datetime.now(UTC)

            # Store consultation result to court Markdown (source of truth)
            if self._memory_manager and response.synthesis:
                try:
                    content = (
                        f"Consultation on '{request.topic[:60]}': "
                        f"{response.synthesis[:200]}"
                    )
                    # Append to court daily log
                    from tianshu.memory.models import MemoryEntry
                    entry = MemoryEntry(
                        persona_id="court",
                        category="insight",
                        content=content,
                        source="agent",
                        access_level="court",
                    )
                    self._memory_manager.store(entry)  # writes MD only

                    # Also append important decisions to court/MEMORY.md
                    md = self._memory_manager.md_backend
                    existing = md.read_core_memory("court")
                    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
                    section = (
                        f"\n## Consultation ({date_str})\n"
                        f"- {content}\n"
                    )
                    if response.decision:
                        section += f"- Decision: {response.decision[:200]}\n"
                    md.write_core_memory("court", existing + section)
                except Exception:
                    logger.debug("Failed to store consultation result to memory")

        except Exception as e:
            logger.exception("Consultation failed: %s", e)
            response.status = "failed"
            response.completed_at = datetime.now(UTC)

        return response

    async def _get_opinion(
        self, persona, request: ConsultationRequest,
    ) -> PersonaOpinion:
        """Get a single persona's opinion via LLM call."""
        from tianshu.llm import LLMClient

        state = self._config_manager.state
        if self._provider_manager and hasattr(self._provider_manager, "get_client"):
            llm = self._provider_manager.get_client()
        else:
            llm = LLMClient(
                model=state.model,
                api_key=state.api_key,
                api_base=state.api_base,
            )

        prompt = (
            f"You are {persona.name} from the {persona.department} department.\n"
            f"Analyze the following topic and provide your professional opinion.\n\n"
            f"Topic: {request.topic}\n"
        )
        if request.context:
            prompt += f"\nContext: {request.context}\n"

        prompt += (
            "\nProvide your opinion with:\n"
            "1. A summary opinion\n"
            "2. Key points (as a numbered list)\n"
            "3. Your confidence level (0.0-1.0)\n"
        )

        messages = [
            {"role": "system", "content": f"You are {persona.name}, {persona.department}."},
            {"role": "user", "content": prompt},
        ]

        response = await llm.chat(messages)

        return PersonaOpinion(
            persona_id=persona.id,
            persona_name=persona.name,
            department=persona.department,
            opinion=response.content or "",
            confidence=0.8,
            key_points=[],
        )
