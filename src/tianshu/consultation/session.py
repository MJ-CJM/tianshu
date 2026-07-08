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

            # Run parallel analysis;言官(第一位,人数>1 时)强制反调破意见趋同(ADR-0008)
            tasks = []
            for idx, pid in enumerate(persona_ids):
                persona = self._personas.get(pid)
                if not persona:
                    continue
                is_censor = idx == 0 and len(persona_ids) > 1
                tasks.append(self._get_opinion(persona, request, is_censor=is_censor))

            opinions = await asyncio.gather(*tasks, return_exceptions=True)
            response.opinions = [o for o in opinions if isinstance(o, PersonaOpinion)]

            # Synthesize
            if response.opinions:
                synthesis_result = await self._synthesizer.synthesize(
                    request,
                    response.opinions,
                )
                response.synthesis = synthesis_result.get("synthesis", "")
                response.decision = synthesis_result.get("decision", "")

            response.status = "completed"
            response.completed_at = datetime.now(UTC)

            # Store consultation result to court Markdown (source of truth)
            if self._memory_manager and response.synthesis:
                try:
                    content = f"Consultation on '{request.topic[:60]}': {response.synthesis[:200]}"
                    # Append to court daily log
                    from tianshu.memory.models import MemoryEntry

                    entry = MemoryEntry(
                        persona_id="court",
                        category="insight",
                        content=content,
                        source="agent",
                        access_level="court",
                    )
                    self._memory_manager.store(entry)  # MD (source of truth) + write-through index

                    # Also append important decisions to court/MEMORY.md
                    md = self._memory_manager.md_backend
                    existing = md.read_core_memory("court")
                    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
                    section = f"\n## Consultation ({date_str})\n- {content}\n"
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
        self,
        persona,
        request: ConsultationRequest,
        is_censor: bool = False,
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
            "\n从你的职能视角给出意见,严格按以下格式:\n"
            "STANCE: support / oppose / conditional 三选一(赞成/反对/有条件)\n"
            "CONDITIONS: 若 conditional,列出条件(分号分隔);否则留空\n"
            "OPINION: 你的核心意见与论据\n"
        )
        if is_censor:
            prompt += (
                "\n【你是言官】职责是唱反调:即使个人倾向赞成,也要找出这个提案**最强的"
                "反对理由**、被忽视的风险、隐藏的代价。宁可偏 oppose/conditional,不随大流。\n"
            )

        messages = [
            {"role": "system", "content": f"You are {persona.name}, {persona.department}."},
            {"role": "user", "content": prompt},
        ]

        response = await llm.chat(messages)
        stance, conditions, opinion_text = self._parse_opinion(response.content or "")
        return PersonaOpinion(
            persona_id=persona.id,
            persona_name=persona.name,
            department=persona.department,
            opinion=opinion_text,
            stance=stance,
            conditions=conditions,
            is_censor=is_censor,
        )

    @staticmethod
    def _parse_opinion(content: str) -> tuple[str, list[str], str]:
        """从 LLM 响应解析结构化 stance(废 confidence,ADR-0008)。"""
        stance = "support"
        conditions: list[str] = []
        opinion = content.strip()
        for line in content.splitlines():
            u = line.strip()
            upper = u.upper()
            if upper.startswith("STANCE:"):
                v = u.split(":", 1)[1].strip().lower()
                stance = (
                    "oppose"
                    if "oppose" in v
                    else "conditional"
                    if "conditional" in v
                    else "support"
                )
            elif upper.startswith("CONDITIONS:"):
                c = u.split(":", 1)[1].strip()
                if c:
                    conditions = [x.strip() for x in c.split(";") if x.strip()]
            elif upper.startswith("OPINION:"):
                opinion = u.split(":", 1)[1].strip()
        return stance, conditions, opinion
