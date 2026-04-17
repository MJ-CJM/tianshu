"""Consultation data models."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from ulid import ULID


class PersonaOpinion(BaseModel):
    persona_id: str
    persona_name: str
    department: str
    opinion: str
    confidence: float = 1.0
    key_points: list[str] = Field(default_factory=list)


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class ConsultationResult(BaseModel):
    consultation_id: str
    opinions: list[PersonaOpinion]
    synthesis: str
    decision: str
