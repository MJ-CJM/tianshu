"""MemoryManager — Markdown source-of-truth + write-through index.

Write routing:
  Agent execution → Markdown (source of truth) + SQLite write-through (FTS 即时可召回)
  Web/API display → SQLite (derived index, rebuildable from MD via sync_index)

Read routing:
  Agent (prompt injection) → Markdown files
  Web/API queries         → SQLite index
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.memory.access_control import MemoryAccessControl
from tianshu.memory.backends.sqlite_backend import SQLiteMemoryBackend
from tianshu.memory.chunker import chunk_text
from tianshu.memory.compactor import MemoryCompactor
from tianshu.memory.config import MemoryConfig
from tianshu.memory.drawer import Drawer
from tianshu.memory.markdown_backend import MarkdownMemoryBackend
from tianshu.memory.models import CompactionResult, MemoryEntry, MemoryQuery
from tianshu.memory.reflect import Reflector

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.models.events import EventEnvelope
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

# Regex for extracting @entity_name references
_ENTITY_REF_PATTERN = re.compile(r"@([\w\-]+)")


class MemoryManager:
    """Unified memory management — Markdown source-of-truth + write-through index.

    Markdown files = Source of Truth (under ~/.tianshu/memory/)
    SQLite = Derived search index (for API queries, Web display, FTS5)

    Integrates with:
    - Hooks: BEFORE_AGENT_START → inject memory context from MD
             AGENT_END → persist execution memory to MD
    - EventBus: audit.completed → store audit insights to MD
    """

    def __init__(
        self,
        storage: Storage,
        config_manager: ConfigManager,
        hook_registry: object | None = None,
        personas_dir: Path | None = None,
        memory_dir: Path | None = None,
        drawer_store: object | None = None,
        memory_config: MemoryConfig | None = None,
    ) -> None:
        self._storage = storage
        self._backend = SQLiteMemoryBackend(storage)
        self._compactor = MemoryCompactor(config_manager)
        self._access_control = MemoryAccessControl(storage)
        self._config_manager = config_manager
        self._hooks = hook_registry
        self._drawer_store = drawer_store
        self._memory_config = memory_config or MemoryConfig()

        if personas_dir is None:
            personas_dir = Path(__file__).parent.parent.parent.parent / "personas"
        self._personas_dir = personas_dir

        if memory_dir is None:
            memory_dir = Path("~/.tianshu/memory").expanduser()
        self._memory_dir = Path(memory_dir).expanduser()

        self._md_backend = MarkdownMemoryBackend(
            memory_dir=self._memory_dir,
            personas_dir=self._personas_dir,
        )
        self._reflector = Reflector(config_manager, md_backend=self._md_backend)
        self._synced_personas: set[str] = set()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def ensure_memory_dirs(self) -> None:
        """Ensure memory directories exist and seed templates on first run."""
        self._md_backend.ensure_dirs()

    @property
    def md_backend(self) -> MarkdownMemoryBackend:
        """Expose MD backend for direct use by ConsultationSession etc."""
        return self._md_backend

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    # ------------------------------------------------------------------
    # Core operations — Markdown source-of-truth + write-through index
    # ------------------------------------------------------------------

    def store(
        self,
        entry: MemoryEntry,
        writer: str | None = None,
    ) -> MemoryEntry:
        """Store a memory entry — writes Markdown (source of truth) + SQLite index (write-through).

        Extracts @entity_name references from content.
        """
        if writer and not self._access_control.can_store(writer, entry):
            logger.warning(
                "Access denied: %s cannot write to %s", writer, entry.persona_id,
            )
            return entry

        # Extract entity references from content
        if not entry.entity_refs:
            refs = _ENTITY_REF_PATTERN.findall(entry.content)
            if refs:
                entry.entity_refs = list(dict.fromkeys(refs))

        # Write to Markdown daily log ONLY (source of truth)
        category_map = {
            "observation": "W",
            "insight": "O",
            "entity": "B",
            "summary": "S",
        }
        md_category = category_map.get(entry.category, "W")
        try:
            self._md_backend.append_daily_log(
                entry.persona_id,
                entry.content,
                category=md_category,
                timestamp=entry.created_at,
            )
        except Exception:
            logger.exception("Markdown write failed for %s", entry.id)

        logger.debug(
            "[MEM] store: persona=%s, category=%s, content_len=%d",
            entry.persona_id, entry.category, len(entry.content),
        )

        # write-through 索引：MD 写完后同步刷 SQLite + FTS（memory_fts trigger 自动维护）。
        # MD 仍是唯一真相；索引写失败不阻断，可后续 sync_index 修复。
        try:
            self._backend.save(entry)
        except Exception:
            logger.exception(
                "Index write-through failed for %s content=%.60r (MD already persisted)",
                entry.id, entry.content,
            )

        return entry

    async def retain_drawers(
        self,
        persona_id: str,
        room: str,
        content: str,
        edict_id: str,
        category: str = "W",
        confidence: float = 0.9,
    ) -> list[str]:
        """Chunk content into drawers and store them. Returns drawer IDs."""
        if not self._drawer_store or not self._memory_config.enabled:
            return []

        from datetime import timezone

        from ulid import ULID

        chunks = chunk_text(
            content,
            max_chars=self._memory_config.chunk_max_chars,
            min_chars=self._memory_config.chunk_min_chars,
        )

        ids: list[str] = []
        ts = datetime.now(timezone.utc).isoformat()
        for i, chunk in enumerate(chunks):
            drawer = Drawer(
                id=str(ULID()),
                wing=persona_id,
                room=room,
                content=chunk,
                source_edict_id=edict_id,
                timestamp=ts,
                category=category,
                confidence=confidence,
                chunk_index=i,
            )
            await self._drawer_store.store_drawer(drawer)
            ids.append(drawer.id)

        return ids

    def store_to_index(self, entry: MemoryEntry) -> None:
        """Write directly to SQLite index only — for Web/API display needs."""
        self._backend.save(entry)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def recall(
        self,
        query: MemoryQuery,
        requestor: str | None = None,
        source: str = "sqlite",
    ) -> list[MemoryEntry]:
        """Recall memories matching a query.

        source='sqlite' → search SQLite index (Web/API)
        source='markdown' → search Markdown daily logs (Agent)
        """
        logger.debug(
            "[MEM] recall: persona=%s, query=%.80s, source=%s",
            query.persona_id, query.query, source,
        )
        if source == "markdown" and query.query:
            md_results = self._md_backend.search_daily_logs(
                query.persona_id, query.query, limit=query.limit,
            )
            entries = [
                MemoryEntry(
                    persona_id=query.persona_id,
                    category="observation",
                    content=r["content"],
                    source="agent",
                )
                for r in md_results
            ]
            if requestor:
                entries = self._access_control.filter_readable(requestor, entries)
            return entries

        # Default: SQLite index
        entries = self._backend.recall(query)
        if requestor:
            entries = self._access_control.filter_readable(requestor, entries)
        return entries

    def list_by_persona(self, persona_id: str, limit: int = 50) -> list[MemoryEntry]:
        """List all memories for a persona (from SQLite index)."""
        return self._backend.list_by_persona(persona_id, limit=limit)

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry from both SQLite index and Markdown source."""
        # Read entry details before deleting from SQLite
        entries = self._storage.search_memory(
            persona_id="", query=None, category=None, limit=1,
        )
        # Direct lookup by id
        with self._storage._lock:
            row = self._storage._conn.execute(
                "SELECT persona_id, content, created_at FROM memory_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()

        if row:
            self._md_backend.delete_line(
                row["persona_id"],
                row["content"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )

        return self._backend.delete(entry_id)

    def delete_batch(self, entry_ids: list[str]) -> int:
        """Delete multiple memory entries from both SQLite and Markdown."""
        if not entry_ids:
            return 0

        # Read entry details before deleting from SQLite
        placeholders = ",".join("?" for _ in entry_ids)
        with self._storage._lock:
            rows = self._storage._conn.execute(
                f"SELECT persona_id, content, created_at FROM memory_entries WHERE id IN ({placeholders})",
                entry_ids,
            ).fetchall()

        for row in rows:
            self._md_backend.delete_line(
                row["persona_id"],
                row["content"],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            )

        return self._backend.delete_batch(entry_ids)

    # ------------------------------------------------------------------
    # Index sync — rebuild SQLite from Markdown
    # ------------------------------------------------------------------

    def auto_sync_if_needed(self, persona_id: str) -> int:
        """Auto-sync once per persona per session. Returns count of synced entries."""
        if persona_id in self._synced_personas:
            return 0
        self._synced_personas.add(persona_id)
        return self.sync_index(persona_id)

    def sync_index(self, persona_id: str) -> int:
        """Rebuild SQLite index for a single persona from Markdown files.

        Clears existing entries for the persona, then re-imports from MD.
        """
        self._synced_personas.add(persona_id)
        # Clear existing entries for this persona
        existing = self._backend.list_by_persona(persona_id, limit=10000)
        if existing:
            self._backend.delete_batch([e.id for e in existing])

        return self._md_backend.sync_to_sqlite(self._storage, persona_id)

    def sync_all_indices(self) -> dict[str, int]:
        """Rebuild SQLite index for all personas. Returns {persona_id: count}."""
        results: dict[str, int] = {}
        if not self._memory_dir.exists():
            return results

        for persona_dir in self._memory_dir.iterdir():
            if persona_dir.is_dir():
                pid = persona_dir.name
                results[pid] = self.sync_index(pid)

        logger.info("Synced all memory indices: %s", results)
        return results

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    async def compact(self, persona_id: str) -> CompactionResult:
        """Compact old memories into a summary, written to the '## 历史摘要' section of MEMORY.md."""
        # Read recent daily entries as the compaction source
        daily_entries = self._md_backend.list_daily_entries(persona_id, days=30)
        if len(daily_entries) <= 5:
            return CompactionResult(
                original_count=len(daily_entries),
                compacted_count=len(daily_entries),
                summary="Not enough entries to compact",
            )

        # BEFORE_COMPACTION hook
        if self._hooks and hasattr(self._hooks, "run"):
            from tianshu.executor.hooks import HookType
            await self._hooks.run(
                HookType.BEFORE_COMPACTION,
                persona_id=persona_id,
                entries=daily_entries,
            )

        # Build MemoryEntry list for compactor
        entries = [
            MemoryEntry(
                persona_id=persona_id,
                category="observation",
                content=e.lstrip("- "),
                source="agent",
            )
            for e in daily_entries
        ]
        result = await self._compactor.compact(persona_id, entries)

        # 非破坏写入：只更新「## 历史摘要」section，保留 memory_write / reflect 写入的其余 section
        try:
            self._md_backend.write_section(
                persona_id, "## 历史摘要", mode="set", content=result.summary,
            )
        except Exception:
            # 写失败无害：daily logs 全量保留，下次 compact 会重新生成摘要
            logger.exception("compact write_section failed for %s", persona_id)

        return result

    async def retain(self, persona_id: str) -> CompactionResult:
        """Retain cycle: compact old memories to free context budget."""
        return await self.compact(persona_id)

    async def reflect(self, persona_id: str) -> list[MemoryEntry]:
        """Reflect cycle: generate insights from observations.

        Reads from Markdown daily logs, writes insights to MEMORY.md.
        """
        daily_entries = self._md_backend.list_daily_entries(persona_id, days=7)
        if not daily_entries:
            return []

        observations = [
            MemoryEntry(
                persona_id=persona_id,
                category="observation",
                content=e.lstrip("- "),
                source="agent",
            )
            for e in daily_entries[:20]
        ]

        insights = await self._reflector.reflect(persona_id, observations)
        # Insights are written to MEMORY.md by the Reflector itself
        # Also append to daily log for searchability
        for insight in insights:
            self.store(insight)
        return insights

    # ------------------------------------------------------------------
    # Hook handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_persona_id(context: dict, plan: object = None) -> str:
        """Extract persona ID from context with consistent fallback chain.

        Priority: context["persona"].id > plan.tasks[0].assigned_official > DEFAULT_EXECUTOR_ID
        """
        from tianshu.persona.model import DEFAULT_EXECUTOR_ID

        persona = context.get("persona")
        if persona and hasattr(persona, "id") and persona.id:
            return persona.id
        if plan and hasattr(plan, "tasks") and plan.tasks:
            first_task = plan.tasks[0]
            if first_task and hasattr(first_task, "assigned_official") and first_task.assigned_official:
                return first_task.assigned_official
        return DEFAULT_EXECUTOR_ID

    @staticmethod
    def _infer_room(edict: object) -> str:
        """Infer room name from edict goal. Simple keyword matching."""
        if not edict:
            return "general"
        goal = (getattr(edict, "goal", "") or "").lower()
        room_keywords = {
            "execution": ["deploy", "run", "execute", "build", "install"],
            "planning": ["plan", "design", "architect", "decompose"],
            "audit": ["review", "audit", "check", "inspect"],
            "tools": ["tool", "command", "script", "cli"],
            "recovery": ["fix", "error", "bug", "recover", "debug"],
            "cost-patterns": ["cost", "token", "budget", "usage"],
        }
        for room, keywords in room_keywords.items():
            if any(kw in goal for kw in keywords):
                return room
        return "general"

    def _recall_fulltext(
        self,
        persona_id: str,
        goal: str,
        department: str | None = None,
        limit: int = 5,
    ) -> list[str]:
        """执行前召回：全量 FTS5（persona + court [+ dept]）+ recency 加权。

        返回注入用的 content 列表，按 BM25 位次 × 时间衰减排序，取 top-limit。
        转义已内置于 fts_search，此处无需再转义。
        """
        import math

        from tianshu.memory.fts import fts_search

        visible_ids = [persona_id, "court"]
        if department:
            visible_ids.append(f"_dept_{department}")

        with self._storage._lock:
            ids = fts_search(
                self._storage._conn, goal, persona_ids=visible_ids, limit=limit * 4,
            )
        if not ids:
            return []

        rank = {entry_id: i for i, entry_id in enumerate(ids)}
        placeholders = ",".join("?" for _ in ids)
        with self._storage._lock:
            rows = self._storage._conn.execute(
                f"SELECT id, content, created_at FROM memory_entries WHERE id IN ({placeholders})",
                ids,
            ).fetchall()

        now = datetime.now(UTC)
        scored: list[tuple[float, str]] = []
        for row in rows:
            bm25 = 1.0 / (1.0 + rank.get(row["id"], len(ids)))
            try:
                ts = datetime.fromisoformat(row["created_at"])
                age_days = (now - ts).total_seconds() / 86400
                recency = math.exp(-0.693 * age_days / 30)  # half-life = 30 天
            except (TypeError, ValueError):
                recency = 0.5
            scored.append((bm25 * (0.5 + 0.5 * recency), row["content"]))

        scored.sort(key=lambda x: -x[0])
        return [content for _, content in scored[:limit]]

    async def on_before_agent_start(self, **context: object) -> object:
        """BEFORE_AGENT_START hook — inject relevant memories from Markdown + Palace."""
        from tianshu.executor.hooks import HookResult

        edict = context.get("edict")
        if not edict:
            return None

        plan = context.get("plan")
        persona_id = self._resolve_persona_id(context, plan)

        goal = getattr(edict, "goal", None)
        if not goal:
            return None

        history_messages: list[dict] = []

        # 全量 FTS5 召回（write-through 索引，任意时间 + recency 加权，已移除 30 天窗口）
        persona = context.get("persona")
        department = getattr(persona, "department", None) if persona else None
        for content in self._recall_fulltext(persona_id, goal, department=department, limit=5):
            history_messages.append(
                {"role": "user", "content": f"[Memory context — do not respond to this] {content}"}
            )

        # NEW: drawer-based L2 recall
        if self._drawer_store and self._memory_config.l2_recall_enabled:
            try:
                from tianshu.memory.layers import MemoryStack
                stack = MemoryStack(store=self._drawer_store, config=self._memory_config)
                results = await stack.recall(goal, wing=persona_id, include_court=True)
                for r in results[:5]:
                    history_messages.append({
                        "role": "user",
                        "content": f"[Palace 记忆 | {r.wing}/{r.room}] {r.content}",
                    })
            except Exception:
                logger.exception("Failed to recall drawers for %s", persona_id)

        if not history_messages:
            return None

        return HookResult(modified_args={"memory_history": history_messages})

    async def on_agent_end(self, **context: object) -> None:
        """AGENT_END hook — store execution memory to Markdown ONLY."""
        from tianshu.models import TaskStatus

        edict = context.get("edict")
        memorial = context.get("memorial")
        if not edict or not memorial:
            return

        status = getattr(memorial, "status", None)
        summary = getattr(memorial, "summary", None)
        if status != TaskStatus.COMPLETED or not summary:
            return

        persona_id = self._resolve_persona_id(context)

        edict_goal = getattr(edict, "goal", "unknown")

        entry = MemoryEntry(
            persona_id=persona_id,
            edict_id=getattr(edict, "id", None),
            memorial_id=getattr(memorial, "id", None),
            category="observation",
            content=f"Task '{edict_goal[:60]}' -> {status.value}: {summary[:150]}",
            source="agent",
        )
        self.store(entry)  # Markdown only

        # Also store as drawers for Memory Palace
        if self._drawer_store and summary:
            room = self._infer_room(edict)
            try:
                await self.retain_drawers(
                    persona_id=persona_id,
                    room=room,
                    content=summary,
                    edict_id=getattr(edict, "id", "") or "",
                    category="W",
                    confidence=0.9,
                )
            except Exception:
                logger.exception("Failed to retain drawers for %s", persona_id)

    # ------------------------------------------------------------------
    # EventBus handlers
    # ------------------------------------------------------------------

    async def handle_execution_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.completed — no-op.

        Redundant with on_agent_end which already stores the observation.
        """
        return

    async def handle_audit_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for audit.completed — store audit insights to Markdown."""
        if not event.memorial_id:
            return

        payload = event.payload or {}
        verdict = payload.get("verdict", "")
        if verdict in ("flag", "block"):
            reasons = payload.get("reasons", [])
            content = f"Audit {verdict}: {', '.join(reasons[:3])}"

            # Write to ducha daily log (Markdown only)
            entry = MemoryEntry(
                persona_id="ducha",
                edict_id=event.edict_id,
                memorial_id=event.memorial_id,
                category="insight",
                content=content,
                source="agent",
                access_level="court",
            )
            self.store(entry)
