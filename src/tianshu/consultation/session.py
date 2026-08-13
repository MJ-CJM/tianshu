"""ConsultationSession — orchestrate multi-persona parallel analysis."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from tianshu.config_manager import ConfigManager
from tianshu.consultation.models import (
    ConsultationRequest,
    ConsultationResponse,
    PersonaOpinion,
)
from tianshu.consultation.synthesizer import Synthesizer
from tianshu.llm import LLMUsageContext
from tianshu.persona.loader import PersonaLoader
from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)

# 单个官员意见 / 综合各自的墙钟上限：LLM 挂起时廷议必须判死，否则状态永远停在
# running，前端只能无限轮询（issue #52）。
DEFAULT_OPINION_TIMEOUT_SECONDS = 180.0
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 180.0


class ConsultationSession:
    """Runs a consultation: parallel persona analysis + LLM synthesis."""

    def __init__(
        self,
        persona_loader: PersonaLoader,
        config_manager: ConfigManager,
        provider_manager: ProviderManager | None = None,
        memory_manager: object | None = None,
        storage: Any | None = None,
        notifier: Any | None = None,
        opinion_timeout: float = DEFAULT_OPINION_TIMEOUT_SECONDS,
        synthesis_timeout: float = DEFAULT_SYNTHESIS_TIMEOUT_SECONDS,
    ) -> None:
        self._personas = persona_loader
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._memory_manager = memory_manager
        self._storage = storage
        self._notifier = notifier
        self._opinion_timeout = opinion_timeout
        self._synthesis_timeout = synthesis_timeout
        self._synthesizer = Synthesizer(config_manager, provider_manager)
        # storage 缺席时（单测、无库装配）退回进程内字典；有库时一律以库为准，
        # 避免 orchestrator 长跑把会话无上限堆在内存里。
        self._sessions: dict[str, ConsultationResponse] = {}

    # --- persistence ---

    def _persist(self, response: ConsultationResponse) -> None:
        if self._storage is None:
            self._sessions[response.id] = response
            return
        try:
            self._storage.save_consultation(response)
        except Exception:
            logger.exception("Failed to persist consultation %s", response.id)

    def get(self, consultation_id: str) -> ConsultationResponse | None:
        if self._storage is None:
            return self._sessions.get(consultation_id)
        return self._storage.get_consultation(consultation_id)

    def list_recent(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ConsultationResponse]:
        if self._storage is None:
            items = sorted(self._sessions.values(), key=lambda c: c.created_at, reverse=True)
            if status:
                items = [c for c in items if c.status == status]
            return items[offset : offset + limit]
        return self._storage.list_consultations(status=status, limit=limit, offset=offset)

    async def _broadcast(self, payload: dict) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.broadcast_ws(payload)
        except Exception:
            logger.debug("Failed to broadcast consultation progress", exc_info=True)

    # --- lifecycle ---

    def create_pending(self, request: ConsultationRequest) -> ConsultationResponse:
        """登记一次廷议并落库（status=pending），返回可供轮询的 id。"""
        response = ConsultationResponse(request=request, status="pending")
        self._persist(response)
        return response

    def mark_failed(self, consultation_id: str, error: str) -> None:
        """把一场未收尾的廷议判死（供后台任务异常/取消时兜底）。"""
        record = self.get(consultation_id)
        if record is None or record.status in {"completed", "failed"}:
            return
        record.status = "failed"
        record.error = error
        record.completed_at = datetime.now(UTC)
        self._persist(record)

    async def start(
        self,
        request: ConsultationRequest,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationResponse:
        """Start a consultation session (登记 + 执行，同步等待结果)。"""
        response = self.create_pending(request)
        return await self.run(response.id, usage_context=usage_context)

    async def run(
        self,
        consultation_id: str,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationResponse:
        """执行一次已登记的廷议：并行取意见（逐条落库+推送）后综合。"""
        response = self.get(consultation_id)
        if response is None:
            raise ValueError(f"Consultation '{consultation_id}' not found")
        request = response.request
        if request is None:
            raise ValueError(f"Consultation '{consultation_id}' has no request")

        response.status = "running"
        self._persist(response)
        await self._broadcast(
            {
                "type": "consultation.started",
                "consultation_id": response.id,
                "status": "running",
            }
        )

        try:
            personas = self._resolve_personas(request)
            failures = await self._collect_opinions(
                response,
                request,
                personas,
                usage_context=usage_context,
            )

            if not response.opinions:
                response.status = "failed"
                response.error = "; ".join(failures) or "no persona produced an opinion"
            else:
                if failures:
                    response.error = "; ".join(failures)
                await self._synthesize(response, request, usage_context=usage_context)
                response.status = "completed"
            response.completed_at = datetime.now(UTC)

        except Exception as e:  # noqa: BLE001 — 兜底：任何未预期异常都要留痕，不能挂死在 running
            logger.exception("Consultation failed: %s", e)
            response.status = "failed"
            response.error = f"{type(e).__name__}: {e}"
            response.completed_at = datetime.now(UTC)

        self._persist(response)
        await self._broadcast(
            {
                "type": "consultation.finished",
                "consultation_id": response.id,
                "status": response.status,
            }
        )

        if response.status == "completed":
            self._archive_to_memory(response, request)
        return response

    def _resolve_personas(self, request: ConsultationRequest) -> list:
        persona_ids = request.persona_ids
        if not persona_ids:
            persona_ids = list(self._personas.load_all().keys())
        personas = []
        for pid in persona_ids:
            persona = self._personas.get(pid)
            if persona:
                personas.append(persona)
        return personas

    async def _collect_opinions(
        self,
        response: ConsultationResponse,
        request: ConsultationRequest,
        personas: list,
        *,
        usage_context: LLMUsageContext | None,
    ) -> list[str]:
        """并行取意见；每条到达即落库并推送，让前端能逐条看到进展。

        返回失败说明列表（供 error 字段归因）。
        """
        order = {persona.id: idx for idx, persona in enumerate(personas)}

        async def _one(persona, idx: int) -> tuple[PersonaOpinion | None, str | None]:
            """单个官员的意见；失败在此收敛为归因文本，不拖垮整场廷议。"""
            try:
                opinion = await asyncio.wait_for(
                    self._get_opinion(
                        persona,
                        request,
                        # 言官(第一位,人数>1 时)强制反调破意见趋同(ADR-0008)
                        is_censor=idx == 0 and len(personas) > 1,
                        usage_context=usage_context,
                    ),
                    timeout=self._opinion_timeout,
                )
            except TimeoutError:
                return None, f"{persona.name}: timeout after {self._opinion_timeout:.0f}s"
            except Exception as e:  # noqa: BLE001 — 单个官员失败不应拖垮整场廷议
                logger.warning("Persona %s failed to opine: %s", persona.id, e)
                return None, f"{persona.name}: {type(e).__name__}: {e}"
            return opinion, None

        failures: list[str] = []
        pending = [_one(persona, idx) for idx, persona in enumerate(personas)]
        for completed in asyncio.as_completed(pending):
            opinion, failure = await completed
            if opinion is None:
                if failure:
                    failures.append(failure)
                continue

            response.opinions.append(opinion)
            response.opinions.sort(key=lambda o: order.get(o.persona_id, len(order)))
            self._persist(response)
            await self._broadcast(
                {
                    "type": "consultation.opinion",
                    "consultation_id": response.id,
                    "status": "running",
                    "persona_id": opinion.persona_id,
                    "persona_name": opinion.persona_name,
                    "done": len(response.opinions),
                    "total": len(personas),
                }
            )
        return failures

    async def _synthesize(
        self,
        response: ConsultationResponse,
        request: ConsultationRequest,
        *,
        usage_context: LLMUsageContext | None,
    ) -> None:
        try:
            synthesis_result = await asyncio.wait_for(
                self._synthesizer.synthesize(
                    request,
                    response.opinions,
                    usage_context=usage_context,
                ),
                timeout=self._synthesis_timeout,
            )
        except TimeoutError:
            # 意见已经拿到且落库，综合超时不该让整场廷议归零
            response.error = "; ".join(
                filter(
                    None,
                    [response.error, f"synthesis timeout after {self._synthesis_timeout:.0f}s"],
                )
            )
            return
        response.synthesis = synthesis_result.get("synthesis", "")
        response.decision = synthesis_result.get("decision", "")

    def _archive_to_memory(
        self,
        response: ConsultationResponse,
        request: ConsultationRequest,
    ) -> None:
        """Store consultation result to court Markdown (source of truth)."""
        if not self._memory_manager or not response.synthesis:
            return
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

    async def _get_opinion(
        self,
        persona,
        request: ConsultationRequest,
        is_censor: bool = False,
        usage_context: LLMUsageContext | None = None,
    ) -> PersonaOpinion:
        """Get a single persona's opinion via LLM call."""
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

        if usage_context is None:
            response = await llm.chat(messages)
        else:
            response = await llm.chat(messages, usage_context=usage_context)
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
