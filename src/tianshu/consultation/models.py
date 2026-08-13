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
    synthesizer_persona_id: str = "neige"


class ConsultationResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    status: str = "pending"  # pending | running | completed | failed
    request: ConsultationRequest | None = None
    opinions: list[PersonaOpinion] = Field(default_factory=list)
    synthesis: str | None = None
    decision: str | None = None
    # 失败归因：让前端能区分"key 配错"与"模型超时"，而不是只显示一句"廷议失败"
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class ConsultationResult(BaseModel):
    consultation_id: str
    opinions: list[PersonaOpinion]
    synthesis: str
    decision: str
