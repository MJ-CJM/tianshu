"""Memory tools — cross-session recall (memory_search) + section-anchored
write (memory_write) for agent long-term experience.

memory_write 借鉴 hermes-agent (`tools/memory_tool.py`) 的设计：
  - 单一工具 + 路径完全在工具内决定（agent 不能传路径）
  - 内容扫描 + 字符上限 + 去重 + 原子写
  - caller_persona_id 通过 ambient ContextVar 取，避免 agent 走串
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
    """Search memory entries using full-text search.

    按 caller persona 自动限定可见范围：
      - 自己的私有条目（persona_id == caller.id）
      - 同部门共享条目（persona_id == "_dept_{caller.department}"）
      - 朝廷共享条目（persona_id == "court"）
    若不在 agent 上下文（无 caller），保持跨 persona 检索（向后兼容）。
    """
    from tianshu.executor.ambient import get_current_persona

    if not query.strip():
        return error_result("Query cannot be empty")

    limit = min(max(1, limit), 50)

    try:
        from tianshu.memory.fts import fts_search

        caller = get_current_persona()
        visible_ids: list[str] | None = None
        # memory_global_read 为真：跳过限定，visible_ids 保持 None → fts_search 跨全 persona 检索
        if (
            caller
            and getattr(caller, "id", None)
            and not getattr(caller, "memory_global_read", False)
        ):
            visible_ids = [caller.id, "court"]
            dept = getattr(caller, "department", None)
            if dept:
                visible_ids.append(f"_dept_{dept}")
        ids = fts_search(
            storage._conn, query, persona_ids=visible_ids, limit=limit,
        )

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


async def _memory_write(
    md_backend,
    event_bus,
    storage: Storage | None = None,
    *,
    action: str,
    scope: str,
    section: str,
    content: str | None = None,
    old_text: str | None = None,
) -> ToolResult:
    """memory_write 实现：把内容按 H2 section 锚定写入调用方私有/朝廷共享池。

    路径完全由 scope + caller_persona 决定，agent 不能干预。
    """
    from tianshu.executor.ambient import get_current_persona
    from tianshu.memory.safety import (
        MemorySafetyError,
        check_file_size,
        normalize_section,
        validate_content,
    )

    if scope not in ("self", "court", "department"):
        return error_result(
            f"unsupported scope: {scope!r}; allowed: self, court, department",
        )
    if action not in ("add", "replace", "remove"):
        return error_result(f"unsupported action: {action!r}; allowed: add, replace, remove")

    # 解析 caller persona / 写入 storage key
    # storage_key 用于 markdown_backend.write_section 的 persona_id 入参
    # 它兼任目录子路径（self → "{pid}"; court → "court"; department → "_dept/{dept}"）
    if scope == "self":
        persona = get_current_persona()
        if persona is None or not getattr(persona, "id", None):
            return error_result(
                "scope='self' 需要在 agent 执行上下文内调用（拿不到 caller_persona）。"
                "如需写入朝廷共享池，请用 scope='court'。",
            )
        storage_key = persona.id
    elif scope == "department":
        persona = get_current_persona()
        dept = getattr(persona, "department", None) if persona else None
        if not dept:
            return error_result(
                "scope='department' 需要在 agent 执行上下文内调用（拿不到 caller persona / department）。",
            )
        storage_key = f"_dept/{dept}"
    else:  # court
        storage_key = "court"

    # FTS 索引用的 persona_id 标签（不是路径）：
    #   self → caller.id（如 "wym"）
    #   court → "court"
    #   department → f"_dept_{dept}"（与 memory_search 的 visible_ids 对齐）
    if scope == "self":
        index_persona_id = get_current_persona().id  # type: ignore[union-attr]
    elif scope == "department":
        index_persona_id = f"_dept_{get_current_persona().department}"  # type: ignore[union-attr]
    else:
        index_persona_id = "court"

    # 校验输入
    try:
        section_norm = normalize_section(section)
        if action in ("add", "replace"):
            if not content:
                return error_result(f"action={action!r} 必须提供 content")
            validate_content(content)
        if action in ("replace", "remove") and not old_text:
            return error_result(f"action={action!r} 必须提供 old_text 用于定位")
    except MemorySafetyError as e:
        return error_result(str(e))

    # 写入
    mode_map = {"add": "append", "replace": "replace", "remove": "remove"}
    try:
        path, total_chars = md_backend.write_section(
            storage_key,
            section_norm,
            mode=mode_map[action],
            content=content,
            old_text=old_text,
        )
        check_file_size(total_chars)
    except FileNotFoundError as e:
        return error_result(f"未找到目标 section/old_text：{e}")
    except ValueError as e:
        return error_result(str(e))
    except MemorySafetyError as e:
        return error_result(str(e))
    except Exception as e:
        logger.exception("memory_write failed")
        return error_result(f"memory_write 失败: {e}")

    # 同步索引到 memory_entries（仅 add；replace/remove 不索引以避免清掉旧文本后索引仍残留）
    if action == "add" and storage is not None and content:
        try:
            from tianshu.memory.models import MemoryEntry

            entry = MemoryEntry(
                persona_id=index_persona_id,
                category="insight",
                content=content,
                source="agent",  # MemoryEntry.source 仅限 agent/compaction/reflection
            )
            storage.save_memory_entry(entry)
        except Exception:
            logger.warning(
                "save_memory_entry failed for memory_write (non-fatal)", exc_info=True,
            )

    # 审计事件
    if event_bus is not None:
        try:
            from tianshu.models import make_event

            await event_bus.emit(make_event(
                "memory.write",
                {
                    "caller_persona_id": getattr(get_current_persona(), "id", None),
                    "scope": scope,
                    "storage_key": storage_key,
                    "section": section_norm,
                    "action": action,
                    "total_chars": total_chars,
                    "path": str(path),
                },
            ))
        except Exception:
            logger.debug("memory.write event emit failed (non-fatal)")

    return ok_result(json.dumps({
        "ok": True,
        "scope": scope,
        "storage_key": storage_key,
        "section": section_norm,
        "action": action,
        "total_chars": total_chars,
    }, ensure_ascii=False))


def register_memory_tools(
    registry: ToolRegistry,
    storage: Storage,
    *,
    memory_dir: Path | None = None,
    personas_dir: Path | None = None,
    event_bus=None,
) -> None:
    """Register memory_search (always) + memory_write (when md deps provided)."""

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

    # memory_write 需要 markdown backend（有路径才能写）
    if memory_dir is None or personas_dir is None:
        logger.info(
            "memory_write 未注册：缺少 memory_dir / personas_dir; 仅注册 memory_search",
        )
        return

    from tianshu.memory.markdown_backend import MarkdownMemoryBackend

    md_backend = MarkdownMemoryBackend(
        memory_dir=memory_dir, personas_dir=personas_dir,
    )

    registry.register(
        "memory_write",
        lambda **kwargs: _memory_write(md_backend, event_bus, storage, **kwargs),
        ToolDefinition(
            name="memory_write",
            description=(
                "Write into your long-term MEMORY.md. Path is auto-determined: "
                "scope='self' writes to your own private MEMORY.md; "
                "scope='court' writes to the shared court memory (visible to all officials). "
                "Use this INSTEAD of write_file/edit_file to remember things — "
                "general file tools don't know your real memory path. "
                "See COURT.md '自学守则' for when to save what."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove"],
                        "description": (
                            "add: append into the section (creates section if missing); "
                            "replace: substitute old_text within the section; "
                            "remove: delete old_text from the section."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["self", "court", "department"],
                        "description": (
                            "self = your private MEMORY.md (only you load it); "
                            "department = shared with peers in your same department only; "
                            "court = shared MEMORY.md loaded by all officials across all departments."
                        ),
                    },
                    "section": {
                        "type": "string",
                        "description": (
                            "H2 section title (e.g. '## 心学要旨' or just '心学要旨'). "
                            "Acts as anchor; same title = same section."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "Required for add/replace. Max 4000 chars per call.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Required for replace/remove. Substring to locate within section.",
                    },
                },
                "required": ["action", "scope", "section"],
            },
            tier=ToolTier.T1_WORKSPACE.value,
            max_result_chars=2000,
            side_effect=True,
        ),
    )
