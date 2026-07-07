# 记忆后端 — Markdown 真相源、Drawer SQLite、三层记忆

> 设计意图：把「人可读的真相源」与「可重建的检索索引」彻底分开，索引坏了能从真相源重建。

## 1. 三层记忆与数据源

| 层 | 载体 | 路径 | 角色 |
|---|---|---|---|
| **L1 Drawer** | 独立 SQLite | `~/.tianshu/memory/drawers.sqlite3` | Palace 最小单元 + FTS5 BM25 |
| **L2 Markdown** | 文本文件 | `~/.tianshu/memory/{wing}/` | **Source of Truth**（人可读、可手改） |
| **L3 索引** | 主库 SQLite | `tianshu.db` 的 `memory_entries` + `memory_fts` | Web/API 全文检索（派生，可重建） |

关键判断：Drawer 独立成库，**与主 `tianshu.db` 分离**，避免 FTS5 重建影响主库事务。

## 2. Markdown 后端（真相源）

目录布局（运行时数据，**不入 git**）：

```
~/.tianshu/memory/
  {persona_id}/
    MEMORY.md           # 核心长期记忆（始终入 prompt）
    YYYY-MM-DD.md       # 每日追加日志（可搜索）
    MEMORY.md.bak       # write_section 写前备份（仅最近一份）
  court/MEMORY.md       # 跨人格共享
  _dept/{dept}/MEMORY.md# 部门共享
```

**首次启动**：`ensure_dirs()` 扫描 `personas/{id}/MEMORY.md`（git 跟踪模板）拷贝到运行时目录；court 必含。

### 写入契约

| 方法 | 语义 |
|---|---|
| `append_daily_log` | 追加 `- [HH:MM] [W/B/O/S] content` 到当日 `.md` |
| `write_section` | 以 **H2 section 为锚**的安全写入；mode = append/replace/remove/set |
| `read_recent_logs` | 读近 N 天日志，按 char_budget 截断（prompt 注入用） |
| `sync_to_sqlite` | 扫 MD 重建 `memory_entries` 索引 |

`write_section` 的安全设计是边界关键点：
- **原子写**：写 `.tmp` 再 `os.replace`；macOS/Linux 加 `fcntl.flock` 排他锁
- **写前备份** `MEMORY.md.bak`（覆盖式）
- **整文件上限** `MAX_FILE_CHARS=32000`，超限抛 `MemorySafetyError` 并附「最大 3 个 section」trim 建议
- **append 去重**：新内容已完整包含在 section body 中则拒绝
- section 必须先经 `safety.normalize_section` 归一为 `## xxx`

## 3. Drawer 独立 SQLite（DrawerStore）

默认 `MemoryBackend` 实现：SQLite + FTS5 BM25。

**两张表**：`drawers`（主表 9 列 + 索引 `(wing)`、`(wing, room)`）；`drawers_fts`（FTS5 虚表，`tokenize='unicode61'`，不可用则降级）。

**并发**：`sqlite3.connect(check_same_thread=False)` + `threading.Lock`，每次操作 `with self._lock`。

**检索**：优先 FTS5 BM25，`OperationalError` 时降级 `LIKE %query% ORDER BY timestamp DESC`。BM25 归一化 `score = 1.0 / (1.0 + abs(rank))`。FTS5 query 转义：每个 token 包成 `"..."` phrase，内部 `"` 加倍，避开保留字与 `C++` / `foo(bar)` 等特殊输入。

**L1 关键事实** `get_l1(wing, max_chars=3200)`：按 `confidence × exp(-0.693 × age_days / 30)` 打分（半衰期 30 天），取 Top-15，按 room 分组输出 L1 Markdown（≈800 tokens）。

## 4. MemoryStack — L0–L3 注入栈

```text
L0 Identity     SOUL.md / ROLE.md            永远在 system prompt
L1 Critical     DrawerStore.get_l1           每次 prompt 注入（PromptBuilder Layer 5.1）
L2 Recall       MemoryStack.recall           BEFORE_AGENT_START hook 按 goal 查
L3 Deep         MemoryStack.deep_search      工具 memory_search 主动触发
```

`recall(query, wing, room, include_court)`：FTS5 BM25 优先；`include_court=True` 时并发查 court wing，`_merge_results` 按 drawer_id 去重并按 score 降序。`deep_search` 无 wing 过滤，用于跨 wing 联想 / consultation 主持人检索。

## 5. MemoryConfig — Ablation 开关

每个能力独立 toggle，为消融实验 / 性能调优 / 隐私场景提供最小切面：

| 布尔开关 | 默认 | tuning 参数 | 默认 |
|---|---|---|---|
| `enabled`（主开关） | True | `l1_max_chars` | 3200 |
| `l1_enabled` | True | `l1_top_k` | 15 |
| `l2_recall_enabled` | True | `l2_n_results` | 10 |
| `reflect_enabled` | True | `chunk_max_chars` | 800 |
| `tunnels_enabled` | True | `chunk_min_chars` | 10 |
| `emperor_wing_enabled` | True | `recency_half_life_days` | 30 |
| `verbatim_mode`（True=原文/False=摘要） | True | | |

## 6. 与 PromptBuilder 的绑定

| Layer | 内容 | 数据源 |
|---|---|---|
| 5 | MEMORY.md 核心记忆 | `~/.tianshu/memory/{id}/MEMORY.md`（真相源） |
| 5.1 | L1 关键事实 | `DrawerStore.get_l1(wing=id)` |
| 5.5 | 近期日志 | `~/.tianshu/memory/{id}/YYYY-MM-DD.md` |
| 5.6 | 部门记忆 | `~/.tianshu/memory/_dept/{dept}/MEMORY.md` |
| 6 | Court 共享 | `~/.tianshu/memory/court/MEMORY.md` |

**相关实现**：[../../impl/memory/](../../impl/memory/)
