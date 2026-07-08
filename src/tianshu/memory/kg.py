"""时序知识图谱 KnowledgeGraph —— 带校勘门的事实断言与 as_of 查询(迭代 4)。

记忆宫殿的结构化事实层:与 Markdown 真相源互补——Markdown 存自由文本,KG 存
可查询的三元组(subject-predicate-object),每条带有效期。用途:
- 偏好漂移可表达(旧偏好 valid_to 盖章退场,新偏好接位);
- 过时事实可作废(as_of 查询只见当时有效的);
- 起居注把用户偏好写成三元组入 KG(迭代 4 后续)。

**事实校勘门**(spec):断言前比对同 (scope, subject, predicate) 的当前有效事实——
object 相同则幂等跳过;不同则**时序更新**(旧事实盖 valid_to 退场,新事实接位),
verdict 记为 "updated"。这样矛盾不是简单覆盖,而是留下可追溯的时序链。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ulid import ULID

if TYPE_CHECKING:
    from tianshu.storage import Storage


@dataclass(frozen=True)
class AssertResult:
    triple_id: str
    verdict: str  # inserted | idempotent | updated
    superseded_id: str | None = None


class KnowledgeGraph:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def assert_fact(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        scope: str = "court",
        confidence: float = 1.0,
        source: str = "agent",
        now: str | None = None,
    ) -> AssertResult:
        """断言一条事实,经校勘门。返回 verdict(inserted/idempotent/updated)。"""
        now = now or datetime.now(UTC).isoformat()
        current = self._storage.query_kg_triples(
            scope=scope, subject=subject, predicate=predicate, as_of=None
        )

        # 幂等:已有完全相同的当前事实 → 不动
        for t in current:
            if t["object"] == obj:
                return AssertResult(triple_id=t["id"], verdict="idempotent")

        # 时序更新:同 (subject,predicate) 有不同 object 的现存事实 → 盖章退场
        superseded_id = None
        for t in current:
            self._storage.invalidate_kg_triple(t["id"], now)
            superseded_id = t["id"]

        triple_id = str(ULID())
        self._storage.save_kg_triple(
            {
                "id": triple_id,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "scope": scope,
                "valid_from": now,
                "valid_to": None,
                "confidence": confidence,
                "source": source,
                "created_at": now,
            }
        )
        verdict = "updated" if superseded_id else "inserted"
        return AssertResult(triple_id=triple_id, verdict=verdict, superseded_id=superseded_id)

    def query(
        self,
        *,
        scope: str = "court",
        subject: str | None = None,
        predicate: str | None = None,
        as_of: str | None = None,
    ) -> list[dict]:
        """as_of 时刻有效的三元组;as_of=None 返回当前有效。"""
        return self._storage.query_kg_triples(
            scope=scope, subject=subject, predicate=predicate, as_of=as_of
        )

    def invalidate(self, triple_id: str, now: str | None = None) -> None:
        """手动作废一条事实(如用户纠正)。"""
        self._storage.invalidate_kg_triple(triple_id, now or datetime.now(UTC).isoformat())
