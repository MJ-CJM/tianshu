"""Memory search tool — cross-session recall for agent long-term experience."""

from __future__ import annotations

import json
import logging

from tianshu.storage import Storage
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

logger = logging.getLogger(__name__)


async def _memory_search(
    storage: Storage,
    query: str,
    limit: int = 10,
    category: str | None = None,
) -> ToolResult:
    """Search memory entries using full-text search."""
    if not query.strip():
        return error_result("Query cannot be empty")

    limit = min(max(1, limit), 50)

    try:
        from tianshu.memory.fts import fts_search

        ids = fts_search(storage._conn, query, persona_id=None, limit=limit)

        if not ids:
            return ok_result(json.dumps({"results": [], "message": "No matching memories found"}))

        placeholders = ",".join("?" for _ in ids)
        sql = f"""
            SELECT id, persona_id, category, content, edict_id, created_at
            FROM memory_entries
            WHERE id IN ({placeholders})
        """
        params: list = list(ids)
        if category:
            sql += " AND category = ?"
            params.append(category)

        rows = storage._conn.execute(sql, params).fetchall()

        results = [
            {
                "id": row["id"],
                "persona_id": row["persona_id"],
                "category": row["category"],
                "content": row["content"][:500],  # Truncate for token efficiency
                "edict_id": row["edict_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

        return ok_result(json.dumps({"results": results, "total": len(results)}, ensure_ascii=False, indent=2))

    except Exception as e:
        logger.exception("Memory search failed")
        return error_result(f"Memory search failed: {e}")


def register_memory_tools(registry: ToolRegistry, storage: Storage) -> None:
    """Register memory_search tool."""

    registry.register(
        "memory_search",
        lambda **kwargs: _memory_search(storage, **kwargs),
        ToolDefinition(
            name="memory_search",
            description=(
                "Search past task memories and insights using keywords. "
                "Returns matching memory entries with summaries. "
                "Use this to recall past experiences and approaches."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keywords",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10, max 50)",
                        "default": 10,
                    },
                    "category": {
                        "type": "string",
                        "description": "Filter by memory category (observation/insight/entity/summary)",
                        "enum": ["observation", "insight", "entity", "summary"],
                    },
                },
                "required": ["query"],
            },
            tier=ToolTier.T0_READONLY.value,
        ),
    )
