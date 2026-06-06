# 记忆召回全量化(write-through 索引)+ compact 非破坏化 — 设计文档

| 项 | 值 |
|---|---|
| 日期 | 2026-06-06 |
| 议题 | A（记忆保真 + 检索增强） |
| 状态 | 设计已确认，待 review → writing-plans |
| 范围 | `src/tianshu/memory/`、`src/tianshu/tools/memory_tools.py`、`src/tianshu/storage.py` |

---

## 1. 背景与问题

天枢的「记忆宫殿」按 persona（官员）组织、跨执行持久化。当前为 **split-write 架构**：

- **Markdown = 唯一真相（source of truth）**：`~/.tianshu/memory/{persona}/`，含按日 `YYYY-MM-DD.md`（append-only 全量流水）+ `MEMORY.md`（常驻 prompt 的核心记忆层）。
- **SQLite + FTS5 = 派生索引**：从 MD 按需重建，用于 Web/API 查询与全文检索。

### 1.1 已确认的事实（代码依据）

- daily log 是 **append-only 全量流水**，compact / reflect 都不删它（`markdown_backend.py:72` `append_daily_log`）。**原始数据并未被销毁**——系统本质上已是「全量原文 + 派生摘要」。
- `compact()` 动的只是 `MEMORY.md`：`write_core_memory` 用 `path.write_text` **覆盖整文件**（`manager.py:364` → `markdown_backend.py:109`）。
- **两条召回路径不一致**：
  - 自动注入 hook `on_before_agent_start` 走 `search_daily_logs`（MD 文件 substring 扫描，**只扫最近 30 天**，`markdown_backend.py:336` `log_files[:30]`）。
  - `memory_search` 工具走 `fts_search`（全量 SQLite FTS5，`memory_tools.py:54`）。
  - 即：**已有全量索引，但自动注入召回没用它。**

### 1.2 三个真实问题

1. **召回窗口受限**：自动注入只覆盖最近 30 天；更早的原文虽在磁盘，注入路径够不到。
2. **compact 破坏性覆盖**：`write_core_memory` 整文件覆盖，会冲掉 agent 经 `memory_write`（`markdown_backend.py:151` `write_section`，按 H2 section 锚定）写入 `MEMORY.md` 的结构化内容。compact / reflect / memory_write 在 `MEMORY.md` 上是互相打架的写者。
3. **无语义检索**：只有 BM25 关键词匹配，换种说法/近义词召回不到。

### 1.3 隐藏元凶：FTS5 query 未转义 → 静默零召回

`fts_search` 内部 `memory_fts MATCH ?` **不转义**用户 query（`fts.py:48`），只有 `drawer_store._escape_fts5_query`（`drawer_store.py:13`）做了转义。自然语言 goal（含中文标点、括号、引号等）直接 MATCH 会触发 FTS5 语法错误，而 `fts_search` 将异常 `except` 掉返回空（`fts.py:89`）——**静默零召回**。这与 30 天窗口共同造成了「召回够不到旧记忆」的现象。

---

## 2. 目标与验收

1. 召回能命中**任意时间**的旧记忆（去掉 30 天窗口），统一走全量 FTS5。
2. 写入**即时可召回**（write-through），不依赖 session 级 sync。
3. 自然语言 goal 经转义后不再静默零召回。
4. `compact` 不再覆盖整个 `MEMORY.md`，不冲掉 `memory_write` 的 section。
5. 不引入向量 / embedding（保持 BM25）。

---

## 3. 非目标（本轮不做）

- 向量 / embedding 语义检索（用户明确未选）。
- 议题 B：persona「全局记忆访问」开关——A 落地后单独对齐。初判：底层已支持（`fts_search` 在 `persona_ids=None` 时即跨全 persona 检索），只需 persona model 加字段 + 召回处判断，改动很小。

---

## 4. 设计

### 4.1 架构取向

`split-write` → **`write-through` 索引**：Markdown 仍是唯一真相；`store()` 写 MD 后同步调 `backend.save()`，SQLite/FTS 成为「始终与 MD 同步的派生索引」，而非「按需重建」。`sync_index` / `sync_all_indices` 降级为**灾难恢复 / 索引漂移时的重建手段**，不再是召回前置依赖。

> 取舍：这温和地偏离了原 `manager.py:1-10` 强调的「no dual-write」。代价是每次 `store` 多一次本地 SQLite 写；收益是召回实时全量可达。一致性风险可控——MD 仍可全量 rebuild 修复索引。这是业界「全量原文 + 索引」的标准形态。

### 4.2 组件改动（逐处）

| 位置 | 现状 | 改为 |
|---|---|---|
| `manager.py store()` `:108` | 只 append MD | append MD 后 `self._backend.save(entry)` 直写索引（`memory_fts_insert` trigger `fts.py:24` 自动同步 FTS） |
| `manager.py on_before_agent_start` `:438` | `search_daily_logs`（30 天 substring） | 走全量 `fts_search`，可见范围 `[persona, court, _dept_{dept}]`，BM25 + recency 重排，**移除 30 天窗口** |
| 召回 query 转义 | `fts_search` 未转义 → 静默零召回 | 召回前复用 `_escape_fts5_query`；同步修补 `memory_search` 工具（`memory_tools.py:54`）与 `storage.search_memory`（`storage.py:1312`）的同一缺陷 |
| `manager.py compact()` `:331` | `write_core_memory` 覆盖整文件 | 只更新 `## 历史摘要` 单 section（section 锚定写，保留其余 section） |

### 4.3 召回数据流

```
新任务 goal
  → escape(goal)                              # FTS5 安全转义
  → fts_search(memory_fts, visible_ids=[persona, court, _dept_x], limit=N)
  → memory_entries（全量，任意时间）
  → recency 加权重排（created_at 时间衰减，复用 drawer get_l1 的 half-life 思路，drawer_store.py:204）
  → guard_context 截断到 token 预算（8000 chars / 20 条，compactor.py:80）
  → 注入 [Memory context]
```

Palace（drawer）L2 召回那路**维持不变**（本就走全量 FTS5 BM25，且已转义）。

### 4.4 写入数据流

```
store(entry)
  → append_daily_log(MD)        # 唯一真相，不变
  → backend.save(entry)         # 新增：INSERT INTO memory_entries（storage.py:1278）
       → memory_fts_insert trigger 自动同步 FTS   # 索引实时全量
```

去重：`store` 主要由 `on_agent_end`（每任务一次）触发，重复概率低。本轮**不做**额外去重（YAGNI）；若测试发现重复注入，再按 `(persona_id, content, created_at)` 去重。

### 4.5 compact 非破坏化

- 给纯函数 `_mutate_section`（`markdown_backend.py:248`）增加 `mode="set"`：section 存在 → 替换其 body；不存在 → 创建。
- `compact()` 输出改为 `write_section(persona_id, "## 历史摘要", mode="set", content=summary)`，其余 section（含 `memory_write` 写入的）原样保留。
- `write_section` 的 `.bak` 备份与原子写（`markdown_backend.py:228-244`）沿用。

### 4.6 错误处理与降级

- 转义后仍空命中 → 正常返回空、不注入，不报错。
- `backend.save` 失败 → 记 log、**不阻断 MD 写**（MD 是真相，索引可后续 `sync_index` 修复），沿用 `store()` 现有 try/except 风格。
- FTS5 不可用（老环境，`storage._fts_available=False`）→ 维持现状降级行为，与 `memory_search` 工具一致。

---

## 5. 测试计划

| 用例 | 验证点 |
|---|---|
| store() 后立即 `fts_search` 命中 | write-through 生效 |
| goal 含中文标点 / 括号 / 引号 | 转义后不抛错、能命中（堵住静默零召回） |
| 命中 31 天前的条目 | 30 天窗口已移除 |
| compact 后 `memory_write` 的 section 仍在、`## 历史摘要` 被更新 | 非破坏验证 |
| `sync_index` 全量重建后 FTS 可查 | 修复 / 重建路径回归 |

> 遵循项目约定：本阶段先实现功能，测试统一补（见个人记忆 `feedback_test_last`）。上表为验收口径，落地计划在 writing-plans 阶段细化。

---

## 6. 风险与权衡

- **偏离 no-dual-write**：见 4.1 取舍说明。已接受。
- **`mode="set"` 的语义**：需保证「section 不存在则创建、存在则整段 body 覆盖」与现有 append/replace/remove 行为正交，不破坏 `memory_write` 工具路径。纯函数单测覆盖。
- **recency 权重系数**：half-life 取值（drawer 现用 30 天）需与召回质量一起调，先沿用 30 天 half-life，留作可调参数。

---

## 7. 关键文件清单

| 文件 | 角色 |
|---|---|
| `src/tianshu/memory/manager.py` | `store()` 直写索引、`on_before_agent_start` 召回全量化、`compact()` 非破坏化 |
| `src/tianshu/memory/fts.py` | `fts_search` 加转义；`memory_fts` trigger（已有） |
| `src/tianshu/memory/drawer_store.py` | `_escape_fts5_query`（复用）、`get_l1` recency（复用思路） |
| `src/tianshu/memory/markdown_backend.py` | `_mutate_section` 加 `mode="set"`、`write_section` |
| `src/tianshu/tools/memory_tools.py` | `_memory_search` 转义修补 |
| `src/tianshu/storage.py` | `search_memory` 转义修补；`save_memory_entry`/FTS 建表（已有） |
| `src/tianshu/memory/compactor.py` | `guard_context` token 预算（复用） |

---

## 8. 后续：议题 B（persona 全局记忆访问开关）

A 落地后单独 brainstorm。待澄清：开关是布尔还是分级、配置入口（persona 模板字段）、是否同时影响 drawer 召回（`stack.recall(wing=...)` → `deep_search` 无 wing 过滤，`layers.py:55`）。
