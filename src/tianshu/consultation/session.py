"""ConsultationSession — orchestrate multi-round, multi-persona court deliberation."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from tianshu.config_manager import ConfigManager
from tianshu.consultation.models import (
    ConsultationRequest,
    ConsultationResponse,
    ConsultationRound,
    PersonaOpinion,
    RoundRequest,
)
from tianshu.consultation.synthesizer import Synthesizer
from tianshu.llm import LLMUsageContext
from tianshu.persona.loader import PersonaLoader
from tianshu.providers.manager import ProviderManager

logger = logging.getLogger(__name__)

# 单个官员意见 / 票拟各自的墙钟上限：LLM 挂起时廷议必须判死，否则状态永远停在
# running，前端只能无限轮询（issue #52）。
DEFAULT_OPINION_TIMEOUT_SECONDS = 180.0
DEFAULT_SYNTHESIS_TIMEOUT_SECONDS = 180.0
# 回放给官员的往轮记录字符预算：从最近轮往前累计，至少保留最近一轮（issue #55）
DEFAULT_HISTORY_MAX_CHARS = 12000


class ConsultationSession:
    """Runs a consultation: multi-round parallel persona analysis + LLM proposal."""

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
        history_max_chars: int = DEFAULT_HISTORY_MAX_CHARS,
    ) -> None:
        self._personas = persona_loader
        self._config_manager = config_manager
        self._provider_manager = provider_manager
        self._memory_manager = memory_manager
        self._storage = storage
        self._notifier = notifier
        self._opinion_timeout = opinion_timeout
        self._synthesis_timeout = synthesis_timeout
        self._history_max_chars = history_max_chars
        self._synthesizer = Synthesizer(config_manager, provider_manager)
        # storage 缺席时（单测、无库装配）退回进程内字典；有库时一律以库为准，
        # 避免 orchestrator 长跑把会话无上限堆在内存里。
        self._sessions: dict[str, ConsultationResponse] = {}

    # --- persistence ---

    def _persist(self, consultation: ConsultationResponse) -> None:
        if self._storage is None:
            self._sessions[consultation.id] = consultation
            return
        try:
            self._storage.save_consultation(consultation)
        except Exception:
            logger.exception("Failed to persist consultation %s", consultation.id)

    def _persist_round(self, round_: ConsultationRound) -> None:
        if self._storage is None:
            consultation = self._sessions.get(round_.consultation_id)
            if consultation is not None:
                rounds = [r for r in consultation.rounds if r.id != round_.id]
                rounds.append(round_)
                consultation.rounds = sorted(rounds, key=lambda r: r.round_index)
            return
        try:
            self._storage.save_consultation_round(round_)
        except Exception:
            logger.exception("Failed to persist consultation round %s", round_.id)

    def get(self, consultation_id: str) -> ConsultationResponse | None:
        if self._storage is None:
            return self._sessions.get(consultation_id)
        return self._storage.get_consultation(consultation_id)

    def get_round(self, consultation_id: str, round_id: str) -> ConsultationRound | None:
        consultation = self.get(consultation_id)
        if consultation is None:
            return None
        return next((r for r in consultation.rounds if r.id == round_id), None)

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
        """登记一场廷议并落库（含第 0 轮 = 议题本身），返回可供轮询的 id。"""
        consultation = ConsultationResponse(request=request, status="pending")
        self._persist(consultation)
        first = ConsultationRound(
            consultation_id=consultation.id,
            round_index=0,
            prompt=request.topic,
            participant_ids=list(request.persona_ids),
            status="pending",
        )
        self._persist_round(first)
        consultation.rounds = [first]
        return consultation

    def append_round(
        self,
        consultation_id: str,
        round_request: RoundRequest,
    ) -> ConsultationRound:
        """追加一轮追问；participant_ids 为空则沿用首轮全体（issue #55）。"""
        consultation = self.get(consultation_id)
        if consultation is None:
            raise ValueError(f"Consultation '{consultation_id}' not found")
        if any(r.status in {"pending", "running"} for r in consultation.rounds):
            raise ValueError("previous round is still in progress")

        participants = list(round_request.participant_ids)
        if not participants:
            participants = list(consultation.request.persona_ids if consultation.request else [])

        round_ = ConsultationRound(
            consultation_id=consultation_id,
            round_index=max((r.round_index for r in consultation.rounds), default=-1) + 1,
            prompt=round_request.prompt,
            participant_ids=participants,
            status="pending",
        )
        self._persist_round(round_)

        consultation.status = "pending"
        consultation.completed_at = None
        self._persist(consultation)
        return round_

    def set_verdict(self, consultation_id: str, verdict: str) -> ConsultationResponse | None:
        """落裁决——LLM 只出票拟，最终决定由用户写下（issue #55）。"""
        consultation = self.get(consultation_id)
        if consultation is None:
            return None
        consultation.verdict = verdict
        consultation.verdict_at = datetime.now(UTC)
        self._persist(consultation)
        return consultation

    def mark_failed(self, consultation_id: str, error: str) -> None:
        """把一场未收尾的廷议判死（供后台任务异常/取消时兜底）。"""
        consultation = self.get(consultation_id)
        if consultation is None or consultation.status in {"completed", "failed"}:
            return
        for round_ in consultation.rounds:
            if round_.status in {"pending", "running"}:
                round_.status = "failed"
                round_.error = error
                round_.completed_at = datetime.now(UTC)
                self._persist_round(round_)
        consultation.status = "failed"
        consultation.error = error
        consultation.completed_at = datetime.now(UTC)
        self._persist(consultation)

    async def start(
        self,
        request: ConsultationRequest,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationResponse:
        """Start a consultation (登记 + 跑第 0 轮，同步等待结果)。"""
        consultation = self.create_pending(request)
        return await self.run(consultation.id, usage_context=usage_context)

    async def run(
        self,
        consultation_id: str,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationResponse:
        """跑该廷议中最早一个未收尾的轮次。"""
        consultation = self.get(consultation_id)
        if consultation is None:
            raise ValueError(f"Consultation '{consultation_id}' not found")
        pending = next(
            (r for r in consultation.rounds if r.status in {"pending", "running"}),
            None,
        )
        if pending is None:
            return consultation
        await self.run_round(consultation_id, pending.id, usage_context=usage_context)
        return self.get(consultation_id) or consultation

    async def run_round(
        self,
        consultation_id: str,
        round_id: str,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationRound:
        """执行一轮：并行取意见（逐条落库+推送）后票拟。"""
        consultation = self.get(consultation_id)
        if consultation is None:
            raise ValueError(f"Consultation '{consultation_id}' not found")
        round_ = next((r for r in consultation.rounds if r.id == round_id), None)
        if round_ is None:
            raise ValueError(f"Round '{round_id}' not found")
        request = consultation.request
        if request is None:
            raise ValueError(f"Consultation '{consultation_id}' has no request")

        round_.status = "running"
        self._persist_round(round_)
        consultation.status = "running"
        self._persist(consultation)
        await self._broadcast(
            {
                "type": "consultation.started",
                "consultation_id": consultation_id,
                "round_id": round_.id,
                "round_index": round_.round_index,
                "status": "running",
            }
        )

        try:
            personas = self._resolve_personas(round_.participant_ids)
            history = self._build_history(consultation, round_.round_index)
            failures = await self._collect_opinions(
                consultation,
                round_,
                request,
                personas,
                history=history,
                usage_context=usage_context,
            )

            if not round_.opinions:
                round_.status = "failed"
                round_.error = "; ".join(failures) or "no persona produced an opinion"
            else:
                if failures:
                    round_.error = "; ".join(failures)
                # 只有首轮自动票拟。后续轮次何时汇总由用户决定——每轮都自动票拟
                # 既多烧一次 LLM 调用，也把「首辅汇总」变成了噪音（issue #55 追加）。
                if round_.round_index == 0:
                    await self._synthesize(round_, request, usage_context=usage_context)
                round_.status = "completed"
            round_.completed_at = datetime.now(UTC)

        except Exception as e:  # noqa: BLE001 — 兜底：任何未预期异常都要留痕，不能挂死在 running
            logger.exception("Consultation round failed: %s", e)
            round_.status = "failed"
            round_.error = f"{type(e).__name__}: {e}"
            round_.completed_at = datetime.now(UTC)

        self._persist_round(round_)
        consultation.status = round_.status
        consultation.error = round_.error
        consultation.completed_at = round_.completed_at
        self._persist(consultation)
        await self._broadcast(
            {
                "type": "consultation.finished",
                "consultation_id": consultation_id,
                "round_id": round_.id,
                "round_index": round_.round_index,
                "status": round_.status,
            }
        )

        if round_.status == "completed":
            self._archive_to_memory(round_, request)
        return round_

    def assert_can_synthesize(self, consultation_id: str, round_id: str) -> ConsultationRound:
        """票拟前置校验——供 HTTP 面在派后台任务之前先把 404/409 判出来。"""
        consultation = self.get(consultation_id)
        if consultation is None:
            raise ValueError(f"Consultation '{consultation_id}' not found")
        round_ = next((r for r in consultation.rounds if r.id == round_id), None)
        if round_ is None:
            raise ValueError(f"Round '{round_id}' not found")
        if round_.status != "completed":
            raise ValueError("round is not ready for synthesis")
        if not round_.opinions:
            raise ValueError("round has no opinion to synthesize")
        return round_

    async def synthesize_round(
        self,
        consultation_id: str,
        round_id: str,
        *,
        usage_context: LLMUsageContext | None = None,
    ) -> ConsultationRound:
        """按需为某一轮请首辅票拟——首轮之外由用户自行决定何时汇总。

        期间把轮次置回 running：前端见「意见已收齐但仍在跑」即显示汇总中。
        """
        self.assert_can_synthesize(consultation_id, round_id)
        consultation = self.get(consultation_id)
        assert consultation is not None  # noqa: S101 — 上一行已校验存在
        round_ = next(r for r in consultation.rounds if r.id == round_id)
        request = consultation.request
        if request is None:
            raise ValueError(f"Consultation '{consultation_id}' has no request")

        round_.status = "running"
        self._persist_round(round_)
        # 后续轮次可能只有一两位官员发言，只看本轮意见会让票拟脱离上下文
        history = self._build_history(consultation, round_.round_index)

        try:
            await self._synthesize(
                round_,
                request,
                history=history,
                usage_context=usage_context,
            )
        except Exception as e:  # noqa: BLE001 — 票拟失败不该让已到手的意见连坐
            logger.exception("Consultation synthesis failed: %s", e)
            round_.error = "; ".join(filter(None, [round_.error, f"{type(e).__name__}: {e}"]))

        round_.status = "completed"
        round_.completed_at = datetime.now(UTC)
        self._persist_round(round_)
        await self._broadcast(
            {
                "type": "consultation.finished",
                "consultation_id": consultation_id,
                "round_id": round_.id,
                "round_index": round_.round_index,
                "status": round_.status,
            }
        )
        return round_

    # --- history ---

    def _build_history(self, consultation: ConsultationResponse, upto_index: int) -> str:
        """把此前轮次回放成文本，供本轮官员看到来龙去脉（issue #55）。

        字符预算从最近轮往前累计，超预算即停，至少保留最近一轮——沿用
        `executor/conversation.py::build_conversation_history` 的截断思路。
        """
        prior = [
            r for r in consultation.rounds if r.round_index < upto_index and r.status == "completed"
        ]
        if not prior:
            return ""

        blocks: list[str] = []
        used = 0
        for round_ in reversed(prior):
            block = self._render_round(round_)
            if blocks and used + len(block) > self._history_max_chars:
                break
            blocks.insert(0, block)
            used += len(block)

        history = "\n\n".join(blocks)
        if consultation.verdict:
            history += f"\n\n【陛下裁决】{consultation.verdict}"
        return history

    @staticmethod
    def _render_round(round_: ConsultationRound) -> str:
        lines = [f"### 第 {round_.round_index + 1} 轮：{round_.prompt}"]
        for opinion in round_.opinions:
            tags = [opinion.department, _STANCE_LABELS.get(opinion.stance, opinion.stance)]
            if opinion.is_censor:
                tags.append("言官·执异")
            lines.append(f"\n**{opinion.persona_name}**（{'，'.join(tags)}）：\n{opinion.opinion}")
            if opinion.conditions:
                lines.append(f"（所附条件：{'；'.join(opinion.conditions)}）")
        if round_.proposal:
            lines.append(f"\n【票拟】{round_.proposal}")
        return "\n".join(lines)

    def _resolve_personas(self, persona_ids: list[str]) -> list:
        ids = persona_ids
        if not ids:
            ids = list(self._personas.load_all().keys())
        personas = []
        for pid in ids:
            persona = self._personas.get(pid)
            if persona:
                personas.append(persona)
        return personas

    async def _collect_opinions(
        self,
        consultation: ConsultationResponse,
        round_: ConsultationRound,
        request: ConsultationRequest,
        personas: list,
        *,
        history: str,
        usage_context: LLMUsageContext | None,
    ) -> list[str]:
        """并行取意见；每条到达即落库并推送，让前端能逐条看到进展。

        返回失败说明列表（供 error 字段归因）。
        """
        order = {persona.id: idx for idx, persona in enumerate(personas)}
        # 言官由 censor_persona_ids 显式任命；此前是 idx==0 硬编码，谁执异
        # 取决于列表顺序，用户无从指定（issue #55）。
        censors = set(request.censor_persona_ids)

        async def _one(persona) -> tuple[PersonaOpinion | None, str | None]:
            """单个官员的意见；失败在此收敛为归因文本，不拖垮整场廷议。"""
            try:
                opinion = await asyncio.wait_for(
                    self._get_opinion(
                        persona,
                        request,
                        prompt=round_.prompt,
                        history=history,
                        is_censor=persona.id in censors,
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
        pending = [_one(persona) for persona in personas]
        for completed in asyncio.as_completed(pending):
            opinion, failure = await completed
            if opinion is None:
                if failure:
                    failures.append(failure)
                continue

            round_.opinions.append(opinion)
            round_.opinions.sort(key=lambda o: order.get(o.persona_id, len(order)))
            self._persist_round(round_)
            await self._broadcast(
                {
                    "type": "consultation.opinion",
                    "consultation_id": consultation.id,
                    "round_id": round_.id,
                    "round_index": round_.round_index,
                    "status": "running",
                    "persona_id": opinion.persona_id,
                    "persona_name": opinion.persona_name,
                    "done": len(round_.opinions),
                    "total": len(personas),
                }
            )
        return failures

    async def _synthesize(
        self,
        round_: ConsultationRound,
        request: ConsultationRequest,
        *,
        history: str = "",
        usage_context: LLMUsageContext | None,
    ) -> None:
        # 票拟者署名：让「综合意见 / 票拟」有明确出处，而不是一段无主之言（issue #54）
        synthesizer = (
            self._personas.get(request.synthesizer_persona_id)
            if request.synthesizer_persona_id
            else None
        )
        if synthesizer is not None:
            round_.synthesizer_persona_id = synthesizer.id
            round_.synthesizer_name = synthesizer.name
            round_.synthesizer_department = synthesizer.department

        try:
            synthesis_result = await asyncio.wait_for(
                self._synthesizer.synthesize(
                    request,
                    round_.opinions,
                    persona=synthesizer,
                    history=history,
                    usage_context=usage_context,
                ),
                timeout=self._synthesis_timeout,
            )
        except TimeoutError:
            # 意见已经拿到且落库，票拟超时不该让整轮归零
            round_.error = "; ".join(
                filter(
                    None,
                    [round_.error, f"synthesis timeout after {self._synthesis_timeout:.0f}s"],
                )
            )
            return
        round_.synthesis = synthesis_result.get("synthesis", "")
        round_.proposal = synthesis_result.get("decision", "")

    def _archive_to_memory(
        self,
        round_: ConsultationRound,
        request: ConsultationRequest,
    ) -> None:
        """Store consultation result to court Markdown (source of truth)."""
        if not self._memory_manager or not round_.synthesis:
            return
        try:
            content = f"Consultation on '{request.topic[:60]}': {round_.synthesis[:200]}"
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
            if round_.proposal:
                section += f"- Proposal: {round_.proposal[:200]}\n"
            md.write_core_memory("court", existing + section)
        except Exception:
            logger.debug("Failed to store consultation result to memory")

    async def _get_opinion(
        self,
        persona,
        request: ConsultationRequest,
        *,
        prompt: str,
        history: str = "",
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

        user_prompt = (
            f"You are {persona.name} from the {persona.department} department.\n"
            f"Analyze the following topic and provide your professional opinion.\n\n"
            f"Topic: {request.topic}\n"
        )
        if request.context:
            user_prompt += f"\nContext: {request.context}\n"
        if history:
            user_prompt += f"\n## 此前廷议记录\n{history}\n"
            user_prompt += f"\n## 本轮追问\n{prompt}\n"
        elif prompt and prompt != request.topic:
            user_prompt += f"\n## 本轮追问\n{prompt}\n"

        user_prompt += (
            "\n从你的职能视角给出意见,严格按以下格式:\n"
            "STANCE: support / oppose / conditional 三选一(赞成/反对/有条件)\n"
            "CONDITIONS: 若 conditional,列出条件(分号分隔);否则留空\n"
            "OPINION: 你的核心意见与论据\n"
        )
        if is_censor:
            user_prompt += (
                "\n【你是言官】职责是唱反调:即使个人倾向赞成,也要找出这个提案**最强的"
                "反对理由**、被忽视的风险、隐藏的代价。宁可偏 oppose/conditional,不随大流。\n"
            )

        messages = [
            {"role": "system", "content": f"You are {persona.name}, {persona.department}."},
            {"role": "user", "content": user_prompt},
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
        """从 LLM 响应解析结构化 stance(废 confidence,ADR-0008)。

        按标记分段而非逐行取值：每个标记的内容延续到下一个标记为止（issue #54）。
        旧实现只取 `OPINION:` 冒号后同一行，LLM 一换行正文就整段丢失。
        """
        sections = ConsultationSession._split_marked_sections(content)

        stance = "support"
        raw_stance = sections.get("STANCE", "").lower()
        if "oppose" in raw_stance:
            stance = "oppose"
        elif "conditional" in raw_stance:
            stance = "conditional"

        conditions = [c.strip() for c in sections.get("CONDITIONS", "").split(";") if c.strip()]

        # 未按格式输出、或给了标记却没正文时回落全文——宁可多余也不要空白卡片
        opinion = sections.get("OPINION", "").strip() or content.strip()
        return stance, conditions, opinion

    @staticmethod
    def _split_marked_sections(content: str) -> dict[str, str]:
        """把 `MARKER: ...` 形式的文本切成 {marker: body}，body 可跨行。"""
        markers = ("STANCE", "CONDITIONS", "OPINION")
        sections: dict[str, list[str]] = {}
        current: str | None = None
        for line in content.splitlines():
            stripped = line.strip()
            matched = next(
                (m for m in markers if stripped.upper().startswith(f"{m}:")),
                None,
            )
            if matched:
                current = matched
                sections[matched] = [stripped.split(":", 1)[1].strip()]
            elif current:
                sections[current].append(line.rstrip())
        return {marker: "\n".join(body).strip() for marker, body in sections.items()}


_STANCE_LABELS = {"support": "赞成", "oppose": "反对", "conditional": "有条件"}
