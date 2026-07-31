# Memory（记忆系统）— 实现现状

**相关设计**：[../../design/memory/](../../design/memory/)

覆盖 `src/tianshu/memory/` 全部文件。架构：**split-write** — Markdown 是真相源，SQLite 是派生索引，Drawer 是 Memory Palace 最小单元。

## 1. 关键类 / 文件路径

| 文件 | 关键类 / 函数 | 职责 |
|---|---|---|
| `memory/manager.py` | `MemoryManager` | split-write 协调；hook + EventBus handler |
| `memory/markdown_backend.py` | `MarkdownMemoryBackend` | 真相源读写、section 锚定写、MD→SQLite sync |
| `memory/drawer_store.py` | `DrawerStore` | 独立 SQLite + FTS5 BM25（`drawers.sqlite3`） |
| `memory/drawer.py` | `Drawer` / `DrawerResult` / `Closet` / `Tunnel` / `MemoryBackend`(Protocol) | 数据模型 + 后端 Protocol |
| `memory/layers.py` | `MemoryStack` | L0–L3 检索栈（get_l1 / recall / deep_search） |
| `memory/config.py` | `MemoryConfig` | 7 布尔 + 6 tuning 的消融开关 |
| `memory/fts.py` | `create_fts_table` / `fts_search` / `escape_fts5_query` | 主库 `memory_fts` 虚表 + 三触发器 |
| `memory/safety.py` | `validate_content` / `normalize_section` / `MemorySafetyError` | 注入扫描 + 字符上限 + Unicode 卫生 |
| `memory/access_control.py` | `MemoryAccessControl` / `MemoryAccessPolicy` | 跨人格读写权限 + `DEFAULT_POLICIES` |
| `memory/chunker.py` | `chunk_text` | 段落贪心合并 chunk |
| `memory/compactor.py` | `MemoryCompactor` | LLM 压缩旧记忆 |
| `memory/reflect.py` | `Reflector` | 周期反思产 insight |
| `memory/models.py` | `MemoryEntry` / `MemoryQuery` / `CompactionResult` | Web/API 层模型 |
| `memory/backends/sqlite_backend.py` | `SQLiteMemoryBackend` | `memory_entries` CRUD（Web/API 派生索引） |
| `tools/memory_tools.py` | `_memory_search` / `_memory_write` / `register_memory_tools` | Agent 读写记忆工具 |

## 2. 核心流程

### 写（split-write）

```text
on_agent_end(memorial 完成 + summary)
  → store(entry): append_daily_log(MD, stable entry_id) + _backend.save(SQLite projection)
  → retain_drawers(summary): chunk_text → DrawerStore.store_drawer ×N (drawers.sqlite3)
```

`store()` 的 Markdown 写失败会抛出，避免把 SQLite 索引误当真相源；索引投影可从 Markdown
通过 `sync_index` 重建。日志多行内容用缩进续行并保存 stable entry ID。

### 读（双路由）

- Agent：`on_before_agent_start` → `_recall_fulltext`（FTS5 + recency 加权，注入 `memory_history`）+ `MemoryStack.recall`（L2 drawer）
- Web/API：`recall(source="sqlite")` → `SQLiteMemoryBackend.recall`

### 索引重建

`sync_index(persona_id)` 清空该 persona 的派生行，再扫描
`- [HH:MM] [WBOS] [id:<stable-id>] content`（兼容没有 ID 的旧行）和缩进续行；新格式重建
后保留原 ID。`MEMORY.md` 的 H2 section 由确定性 section ID 投影，replace/remove 后不会
留下同 section 的旧索引。

## 3. 数据库

| 库 | 表 | 维护方式 |
|---|---|---|
| `tianshu.db`（主库） | `memory_entries` | `SQLiteMemoryBackend.save` |
| 同上 | `memory_fts`（FTS5 虚表） | `fts.py` 的 insert/delete/update 三触发器自动同步 |
| `~/.tianshu/memory/drawers.sqlite3` | `drawers` + `drawers_fts` | `DrawerStore`，独立连接 + `threading.Lock` |

## 4. 扩展点

- **换向量后端**：实现 `drawer.py` 的 `MemoryBackend` Protocol（`store_drawer` / `search` / `get_drawers` / `delete_drawer` / `get_l1`），注入替换 `DrawerStore`（如 ChromaDB / Qdrant）
- **新消融开关**：在 `MemoryConfig` 加字段，`MemoryStack` / `MemoryManager` 读取
- **新注入威胁模式**：在 `safety._INJECTION_REGEXES` 增补
- **新跨人格策略**：`access_control.DEFAULT_POLICIES` 增 `MemoryAccessPolicy` 或运行时 `set_policy`
- **Closet / Tunnel**：模型已在 `drawer.py` 定义，存储 + 检索待实现

## 5. 删除与策略

- `delete(entry_id)` 先按 stable ID 删除 Markdown 行和续行，成功后再删 SQLite。找不到
  真相源时返回 false，不允许 index-only 删除。
- legacy 无 ID 行只在精确内容可唯一定位时兼容删除，不做 substring 删除。
- `MemoryAccessControl` 从 `app_settings.memory_access_policies` 加载并在更新时持久化；
  `list_policies()` 返回副本，不再由 Gateway 读取私有字段。
- 全局 memory API 受 admin middleware 保护；它与普通 principal 的任务 ownership 是两套
  边界。

## 6. 注意点（与旧文档纠偏）

- Drawer 的 `category` 是 `W/B/O/D`，但 `MemoryManager.store()` 写 MD 日志用 `W/B/O/S`（summary），两套字母不同源
- `_recall_fulltext` 已移除 30 天硬窗口，改为全量 + recency 加权
- `DrawerResult.matched_via` 取值为 `"bm25"` / `"fallback"`（非 "fts5"/"exact"）
