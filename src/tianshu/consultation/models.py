"""Consultation data models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


class PersonaOpinion(BaseModel):
    persona_id: str
    persona_name: str
    department: str
    opinion: str
    # ADR-0008:废 confidence(硬编码占位、无信息量)换结构化 stance——
    # 赞成/反对/有条件 + 条件清单 + 论据,让廷议纪要可汇聚、不再假装有置信度。
    stance: Literal["support", "oppose", "conditional"] = "support"
    conditions: list[str] = Field(default_factory=list)
    key_points: list[str] = Field(default_factory=list)
    is_censor: bool = False  # 言官——强制反调,破单模型六官同构的意见趋同


class ConsultationRequest(BaseModel):
    topic: str
    context: str | None = None
    edict_id: str | None = None
    persona_ids: list[str] = Field(default_factory=list)
    # 言官名单：显式任命谁唱反调。此前由 `idx == 0` 硬编码决定，
    # 谁执异取决于列表顺序，用户无从指定（issue #55）。
    censor_persona_ids: list[str] = Field(default_factory=list)
    # 首辅（票拟者）的 persona id；留空则由通用「首席顾问」身份票拟。
    # 旧默认值 "neige" 是部门名而非 persona id（真实 id 形如 wym/zjz/smg），
    # 且该字段从未被消费——issue #54 接线时一并改正。
    synthesizer_persona_id: str | None = None


class RoundRequest(BaseModel):
    """追加一轮：可 @指定作答者，留空则沿用首轮全体（issue #55）。"""

    prompt: str
    participant_ids: list[str] = Field(default_factory=list)


class ConsultationRound(BaseModel):
    """一轮朝议：一次追问 + 各官员作答 + 首辅票拟。"""

    id: str = Field(default_factory=lambda: str(ULID()))
    consultation_id: str = ""
    round_index: int = 0
    # 第 0 轮为议题本身，其后为用户追问
    prompt: str = ""
    participant_ids: list[str] = Field(default_factory=list)
    status: str = "pending"  # pending | running | completed | failed
    opinions: list[PersonaOpinion] = Field(default_factory=list)
    synthesis: str | None = None
    # 票拟：内阁建议，仅供参考——最终裁决权在用户手里（见 Consultation.verdict）
    proposal: str | None = None
    # 实际执笔票拟的官员；为空表示由通用「首席顾问」身份票拟（issue #54）
    synthesizer_persona_id: str | None = None
    synthesizer_name: str | None = None
    synthesizer_department: str | None = None
    # 失败归因：让前端能区分"key 配错"与"模型超时"，而不是只显示一句"廷议失败"
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class ConsultationResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    status: str = "pending"  # pending | running | completed | failed
    request: ConsultationRequest | None = None
    rounds: list[ConsultationRound] = Field(default_factory=list)
    # 裁决：用户写下的最终决定。LLM 只出票拟，裁决权不外包（issue #55）
    verdict: str | None = None
    verdict_at: datetime | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @property
    def latest_round(self) -> ConsultationRound | None:
        return self.rounds[-1] if self.rounds else None

    # 以下三个代理属性让「一场廷议」在只读取最新一轮时仍像 2.0 之前那样使用，
    # orchestrator L2（executor/orchestrator/loop.py::_run_consultation）因此零改动。
    @property
    def opinions(self) -> list[PersonaOpinion]:
        latest = self.latest_round
        return latest.opinions if latest else []

    @property
    def synthesis(self) -> str | None:
        latest = self.latest_round
        return latest.synthesis if latest else None

    @property
    def proposal(self) -> str | None:
        latest = self.latest_round
        return latest.proposal if latest else None


class ConsultationResult(BaseModel):
    consultation_id: str
    opinions: list[PersonaOpinion]
    synthesis: str
    decision: str
