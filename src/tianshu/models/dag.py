"""DAG execution and node models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field
from ulid import ULID


class DAGNodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DAGNode(BaseModel):
    node_id: str
    dag_execution_id: str = ""
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: DAGNodeStatus = DAGNodeStatus.PENDING
    assigned_official: str | None = None
    assigned_worker: str | None = None
    tools_required: list[str] = Field(default_factory=list)
    memorial_id: str | None = None
    checkpoint_json: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class DAGExecution(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    edict_id: str
    plan_json: str = "{}"
    status: str = "pending"  # pending | running | completed | failed | cancelled
    root_memorial_id: str | None = None
    max_concurrency: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    nodes: list[DAGNode] = Field(default_factory=list)
