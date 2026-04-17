# Memory（记忆系统）

覆盖 `src/tianshu/memory/` 全部 14 个 Python 文件。记忆系统采用 **split-write** 架构：Markdown 文件是真相源（Source of Truth），SQLite 是派生索引（derived index），Drawer 是 Memory Palace 的最小存储单元。

---

## 1. 数据模型（`drawer.py`、`models.py`）

### Drawer — Memory Palace 最小单元

```python
@dataclass(frozen=True)
class Drawer:
    id: str
    wing: str              # persona_id / "court" / "emperor"
    room: str              # 主题房间
    content: str           # 原文片段（≤ 800 字符）
    source_edict_id: str
    timestamp: str         # ISO 8601 UTC
    category: str          # W(world) / B(biographical) / O(opinion) / D(decision)
    confidence: float      # 0.0–1.0
    chunk_index: int
```

相关模型：`DrawerResult`（search 返回，含 score + matched_via）、`Closet`（主题指针，不存内容）、`Tunnel`（跨 wing 链接）。

### MemoryBackend Protocol

`drawer.py:62` 定义 `@runtime_checkable` Protocol：`store_drawer` / `search` / `get_drawers` / `delete_drawer` / `get_l1`。

### MemoryEntry（`models.py`）

Web/API 层使用的传统记忆条目：`persona_id` / `category`（observation/insight/entity/summary）/ `content` / `source` / `access_level` / `entity_refs` / `confidence`。

## 2. DrawerStore（`drawer_store.py`）

默认 `MemoryBackend` 实现：SQLite + FTS5 BM25。

**独立数据库**：`~/.tianshu/memory/drawers.sqlite3`（与主 `tianshu.db` 分离，避免 FTS5 重建影响主库事务）。

**两张表**：
- `drawers`（主表，9 列 + 双索引 `(wing)`、`(wing, room)`）
- `drawers_fts`（FTS5 虚表，`tokenize='unicode61'`，不可用则降级）

**并发**：`sqlite3.connect(check_same_thread=False)` + `threading.Lock`，每次操作 `with self._lock`。

**关键方法**：
- `store_drawer(drawer)` — 主表 + FTS 表同步 INSERT OR REPLACE
- `search(query, wing, room, n_results)` — 优先 FTS5 BM25；不可用时降级 `LIKE + ORDER BY timestamp DESC`
- `_escape_fts5_query(query)` — 每个 token 包成 `"..."`，内部 `"` 加倍转义，避开 FTS5 保留字与 `C++`/`foo(bar)` 等特殊输入
- `get_l1(wing, max_chars=3200)` — 按 `confidence × exp(-0.693 × age_days / 30)` 打分（半衰期 30 天），取 Top-15，按 room 分组输出 L1 Markdown

BM25 分数 → 归一化：`score = 1.0 / (1.0 + abs(rank))`，`matched_via` 标记为 `"bm25"`。

## 3. MemoryStack（`layers.py`）— 4 层记忆栈

```python
class MemoryStack:
    def __init__(self, store: MemoryBackend, config: MemoryConfig)
    async def get_l1(self, wing) -> str
    async def recall(self, query, wing, room, include_court) -> list[DrawerResult]
    async def deep_search(self, query, n_results=20) -> list[DrawerResult]
```

| 层 | 方法 | 用途 | 注入点 |
|---|---|---|---|
| L0 | — | 人格身份（SOUL.md / ROLE.md） | PromptBuilder Layer 2–4 |
| L1 | `get_l1` | 关键事实（常驻） | PromptBuilder Layer 5.1 |
| L2 | `recall` | 按需检索（`BEFORE_AGENT_START` hook） | Agent messages 前置 |
| L3 | `deep_search` | 全 Palace 无 wing 过滤 | 工具 `memory_search` 主动触发 |

`include_court=True` 时 L2 会并发查询 court wing，`_merge_results` 去重（by drawer_id）并按 score 降序。

## 4. MemoryConfig（`config.py`）— Ablation 开关

```python
@dataclass
class MemoryConfig:
    enabled: bool = True               # 主开关
    l1_enabled: bool = True
    l2_recall_enabled: bool = True
    reflect_enabled: bool = True
    tunnels_enabled: bool = True
    emperor_wing_enabled: bool = True
    verbatim_mode: bool = True         # True=原文 / False=摘要

    l1_max_chars: int = 3200           # ≈ 800 tokens
    l1_top_k: int = 15
    l2_n_results: int = 10
    chunk_max_chars: int = 800
    chunk_min_chars: int = 10
    recency_half_life_days: int = 30
```

七个布尔 + 六个 tuning 参数，支持独立消融实验。

## 5. Chunker（`chunker.py`）

`chunk_text(text, max_chars=800, min_chars=10) -> list[str]`：
1. `text.split("\n\n")` 按段落切
2. 贪心合并：若当前 + 下段 ≤ max_chars，拼接
3. 超长强制切：`while len(chunk) > max_chars: chunk[:max_chars] + chunk[max_chars:]`
4. 过滤 `< min_chars` 的碎片

## 6. MarkdownMemoryBackend（`markdown_backend.py`）— 真相源

目录布局（运行时数据，不入 git）：

```
~/.tianshu/memory/
  {persona_id}/
    MEMORY.md           # 核心长期记忆（始终入 prompt）
    YYYY-MM-DD.md       # 每日追加日志（可搜索）
  court/
    MEMORY.md           # 共享记忆
```

**首次启动**：`ensure_dirs()` 扫描 `personas/{id}/MEMORY.md`（git 跟踪模板），拷贝到 `~/.tianshu/memory/{id}/MEMORY.md`。

**核心方法**：
- `append_daily_log(persona_id, content, category, timestamp)` — 追加到当日 `.md`
- `search_daily_logs(persona_id, query, limit)` — 扫描近 30 天日志按关键词匹配
- `list_daily_entries(persona_id, days)` — 读最近 N 天条目
- `write_core_memory(persona_id, summary)` — 覆盖写入 `MEMORY.md`（compact 后）
- `delete_line(persona_id, content, created_at)` — 从日志精确删除一行
- `sync_to_sqlite(storage, persona_id)` — 将 MD 日志导入 SQLite index

## 7. MemoryManager（`manager.py`）— split-write 协调

**写路径**（只写 MD）：
- Agent 执行产生的记忆 → `store()` → MD 日志
- Drawer 存储 → `retain_drawers()` → `drawer_store.store_drawer` → SQLite（drawers.sqlite3）

**读路径**：
- Agent prompt 注入 → MD（`recall(source="markdown")`）+ Drawer L2（`MemoryStack.recall`）
- Web/API 查询 → SQLite index（`recall(source="sqlite")`）

**Hook 集成**：
- `BEFORE_AGENT_START`（`on_before_agent_start`）：提取 `edict.goal`，执行 MD search + Drawer L2 recall，通过 `HookResult.modified_args={"memory_history": ...}` 注入
- `AGENT_END`（`on_agent_end`）：写 MD 观察条目 + `retain_drawers` chunk 入 Palace（category="W"、confidence=0.9）

**EventBus 集成**：
- `audit.completed`（`handle_audit_completed`）：verdict ∈ {flag, block} 时，写 `ducha` 人格 insight（access_level="court"）

**辅助方法**：
- `_resolve_persona_id(context, plan)` — 三级 fallback：`context["persona"].id` → `plan.tasks[0].assigned_official` → `DEFAULT_EXECUTOR_ID`
- `_infer_room(edict)` — 关键词匹配映射到 room（execution/planning/audit/tools/recovery/cost-patterns/general）
- `sync_index(persona_id)` / `sync_all_indices()` — 从 MD 重建 SQLite index（Web 侧用）
- `auto_sync_if_needed(persona_id)` — 每会话每人格一次

## 8. Compactor（`compactor.py`）+ Reflector（`reflect.py`）

**Compactor**：`MemoryCompactor.compact(persona_id, entries)` 调用 LLM 对 > 3 条记忆做摘要，写回 `MEMORY.md`。`MAX_HISTORY_MESSAGES=20`、`MAX_HISTORY_CHARS=8000` 作上下文 guard。

**Reflector**：`Reflector.reflect(persona_id, observations)` 周期性反思，产出 1–3 条 insight。`REFLECTION_COOLDOWN=3600` 秒避免频繁触发，输出写 MEMORY.md 的"洞察"区段并追加到 daily log。

## 9. AccessControl（`access_control.py`）

`MemoryAccessPolicy(persona_id, can_read, can_write, share_level)`：
- `share_level` ∈ {private, shared, court}
- `allows_read(from_persona)` / `allows_write(to_persona)` — 逐人格白名单 + share_level 兜底
- `MemoryAccessControl` 聚合所有 policy，`can_store` / `filter_readable` 给 MemoryManager 调用

## 10. SQLiteMemoryBackend（`backends/sqlite_backend.py`）+ FTS（`fts.py`）

`SQLiteMemoryBackend` 将 `MemoryEntry` 写入 `memory_entries` 表（主 tianshu.db），配合 `fts.py` 的 FTS5 虚表 + 三个触发器（insert / delete / update）自动同步 `memory_fts`。

这条路径只服务 Web/API（/memory/search、/memory/recall），Agent 执行路径永远不经过这里。

## 代码路径索引

- `src/tianshu/memory/drawer.py`
- `src/tianshu/memory/drawer_store.py`
- `src/tianshu/memory/layers.py`
- `src/tianshu/memory/config.py`
- `src/tianshu/memory/chunker.py`
- `src/tianshu/memory/markdown_backend.py`
- `src/tianshu/memory/manager.py`
- `src/tianshu/memory/compactor.py`
- `src/tianshu/memory/reflect.py`
- `src/tianshu/memory/access_control.py`
- `src/tianshu/memory/models.py`
- `src/tianshu/memory/fts.py`
- `src/tianshu/memory/backends/sqlite_backend.py`
