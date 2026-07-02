"""位面数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from ulid import ULID


class UniverseStatus(str, Enum):
    CHAMPION = "champion"  # 在役（唯一）
    CHALLENGER = "challenger"  # 候选
    ARCHIVED = "archived"  # 已归档（可恢复）


class UniverseOrigin(str, Enum):
    GENESIS = "genesis"  # 首次启用时捕获的初始位面
    MANUAL_BRANCH = "manual_branch"  # 人工分支
    MUTATION = "mutation"  # 演化引擎变异产生（1b）
    CODE_VARIANT = "code_variant"  # 预留：第二步代码变体位面


@dataclass(frozen=True)
class Universe:
    name: str
    status: UniverseStatus = UniverseStatus.CHALLENGER
    origin: UniverseOrigin = UniverseOrigin.MANUAL_BRANCH
    id: str = field(default_factory=lambda: str(ULID()))
    parent_universe_id: str | None = None
    mutation_reason: str | None = None
    code_ref: str | None = None
    description: str = ""
    fitness: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "parent_universe_id": self.parent_universe_id,
            "status": self.status.value,
            "origin": self.origin.value,
            "mutation_reason": self.mutation_reason,
            "code_ref": self.code_ref,
            "description": self.description,
            "fitness": self.fitness,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict) -> Universe:
        return cls(
            id=row["id"],
            name=row["name"],
            parent_universe_id=row.get("parent_universe_id"),
            status=UniverseStatus(row["status"]),
            origin=UniverseOrigin(row["origin"]),
            mutation_reason=row.get("mutation_reason"),
            code_ref=row.get("code_ref"),
            description=row.get("description", ""),
            fitness=row.get("fitness", {}),
            created_at=row["created_at"],
        )
