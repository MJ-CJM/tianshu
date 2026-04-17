# Memory Palace（记忆宫殿）

> **WHY + WHAT**：Memory Palace 是天枢的长期记忆抽象层，把一个复杂多代理系统的记忆组织成可以被检索、消融、分层注入的结构。实现细节见 `docs/impl/memory.md`；Phase 1 完整计划见 `docs/superpowers/plans/2026-04-16-memory-palace.md`。

---

## 1. 隐喻

把整个系统的记忆看作一座宫殿：

| 概念 | 隐喻 | 对应代码 |
|---|---|---|
| **Palace** | 整座宫殿 | 全局 `DrawerStore` |
| **Wing** | 偏殿（某个人格的专属区） | `persona_id` / `"court"` / `"emperor"` |
| **Room** | 偏殿里的房间（按主题划分） | `room: str`（如 `execution` / `planning` / `audit`） |
| **Drawer** | 房间里的抽屉（最小存储单元） | `Drawer(frozen dataclass)`，最多 ~800 字符 |
| **Closet** | 主题索引柜（指向多个抽屉） | `Closet`（Phase 2+ 启用） |
| **Tunnel** | 偏殿间暗道（跨 wing 链接） | `Tunnel`（Phase 2+ 启用） |

宫殿的核心原则：
1. **Verbatim 原文**：Drawer 存储原文片段，不做即时摘要（避免信息损耗），后续才可选摘要化
2. **Wing 隔离但 Court 共享**：每个人格的记忆私有；跨人格共享走 `court` wing
3. **主题内聚**：同 wing 内按 room 组织，方便按主题检索与降维

## 2. 数据模型

### Drawer — 最小单元

```python
@dataclass(frozen=True)
class Drawer:
    id: str
    wing: str              # persona_id / "court" / "emperor"
    room: str              # 主题房间
    content: str           # 原文（≤ 800 字符）
    source_edict_id: str   # 来源 edict
    timestamp: str         # ISO 8601 UTC
    category: str          # W / B / O / D
    confidence: float      # 0.0–1.0
    chunk_index: int       # 多 chunk 源的位置
```

### Category 四维

| 字母 | 含义 | 场景 |
|---|---|---|
| W | World | 客观事实 / 执行观察 |
| B | Biographical | 人物 / 实体传记 |
| O | Opinion | 主观判断 / 评价 |
| D | Decision | 决策记录 |

Category 让 L1 / L2 可按主题过滤，例如 ducha 倾向保留 D（决策）+ O（评价），而 bingbu 倾向 W（执行观察）。

### 复合结构

- **Closet**：`(topics, entities, drawer_ids)` — 主题指针，方便"跳到这个主题"
- **Tunnel**：`(from_wing/room, to_wing/room, reason, created_by)` — 跨人格联想链接

Phase 1 仅实现 Drawer + DrawerStore；Closet / Tunnel 为后续阶段预留。

## 3. MemoryStack（L0–L3）

```text
L0 Identity     — SOUL.md / ROLE.md          永远在 system prompt
L1 Critical     — drawer Top-K (confidence × recency)   每次 prompt 注入
L2 Recall       — filtered search             BEFORE_AGENT_START hook 按 goal 查
L3 Deep         — full palace search           工具 memory_search 主动触发
```

### L1：关键事实

`DrawerStore.get_l1(wing, max_chars=3200)`：
- 打分：`score = confidence × exp(-0.693 × age_days / 30)`（半衰期 30 天）
- Top-15
- 按 room 分组输出 Markdown
- 预算 3200 字符（≈ 800 tokens）

### L2：按需检索

`MemoryStack.recall(query, wing, room, include_court)`：
- FTS5 BM25 优先，降级 LIKE
- 默认 `n_results=10`
- `include_court=True` 时并发查 court wing，合并去重
- 由 `MemoryManager.on_before_agent_start` 以 `edict.goal` 为 query 自动触发，结果以 `[Palace 记忆 | {wing}/{room}] {content}` 注入 messages

### L3：全局深搜

`MemoryStack.deep_search(query, n_results=20)`：无 wing 过滤，通过工具（`memory_search`）主动调用，用于跨 wing 联想或 consultation session 主持人检索。

## 4. 与 PromptBuilder 8 层的绑定

见 `docs/impl/persona.md` §5 完整表格。与 Memory Palace 相关的层：

| Layer | 作用 | 数据源 |
|---|---|---|
| 5 | MEMORY.md 核心记忆 | `~/.tianshu/memory/{id}/MEMORY.md`（Markdown，真相源） |
| 5.1 | L1 Critical Facts | `DrawerStore.get_l1(wing=id)`（SQLite drawer） |
| 5.5 | 近 2 天日志 | `~/.tianshu/memory/{id}/YYYY-MM-DD.md` |
| 6 | Court 共享记忆 | `~/.tianshu/memory/court/MEMORY.md` |

Markdown = 真相源（人可读、可手改、git-friendly）；DrawerStore = 派生索引（SQLite，结构化检索）。两者通过 `retain_drawers`（`on_agent_end` hook）并行写入，读路径也分别服务 Web UI 与 Agent prompt。

## 5. 检索路径

```text
Agent prompt 构建
  ├→ Layer 5  MEMORY.md                    (读 Markdown)
  ├→ Layer 5.1 DrawerStore.get_l1(wing)   (FTS5 / L1)
  └→ Layer 5.5 近期日志                    (读 Markdown)

BEFORE_AGENT_START hook
  └→ MemoryStack.recall(goal, wing, include_court=True)   (L2 / FTS5 BM25)

工具 memory_search (Agent 主动)
  └→ MemoryStack.deep_search(query)                       (L3)

AGENT_END hook
  └→ MemoryManager.retain_drawers + 写 Markdown 日志
```

FTS5 BM25：`_escape_fts5_query` 将每个 token 包成 phrase `"..."`，内部 `"` 加倍转义，避免 `C++` / `foo(bar)` / `hello"world` 触发语法错误。降级路径 `LIKE %query% ORDER BY timestamp DESC`。

## 6. Ablation 开关（`MemoryConfig`）

每个能力独立 toggle，为实验提供最小切面：

```python
@dataclass
class MemoryConfig:
    enabled: bool = True
    l1_enabled: bool = True
    l2_recall_enabled: bool = True
    reflect_enabled: bool = True
    tunnels_enabled: bool = True
    emperor_wing_enabled: bool = True
    verbatim_mode: bool = True       # True=原文 / False=摘要

    l1_max_chars: int = 3200
    l1_top_k: int = 15
    l2_n_results: int = 10
    chunk_max_chars: int = 800
    chunk_min_chars: int = 10
    recency_half_life_days: int = 30
```

用途：
- 消融实验：关掉 L1 看 Agent 能力退化程度
- 性能调优：改 `chunk_max_chars` 观察 FTS5 命中率
- 隐私场景：关 `emperor_wing_enabled` 禁用用户侧记忆

## 7. Court 共享

`court` wing 是一个"虚拟人格"，存放需要跨人格共享的记忆：
- `COURT.md` 模板（`personas/court/COURT.md`）→ PromptBuilder Layer 2
- `~/.tianshu/memory/court/MEMORY.md` → PromptBuilder Layer 6
- DrawerStore `wing="court"` → L2 recall 的 `include_court=True` 会并入结果
- ducha 的 audit insight（verdict ∈ {flag, block}）会以 `access_level="court"` 写入 court 可见区

## 8. 演进路线

Phase 1（已落地，feat_phase5）：
- Drawer / DrawerStore / MemoryStack L0–L3
- L1 关键事实注入 + L2 hook 触发 recall
- FTS5 BM25 搜索 + phrase escape
- MemoryConfig 消融开关
- `/memory/search` + `/memory/l1` API

Phase 2+（待规划）：
- Closet / Tunnel 跨 wing 链接
- 向量化后端（ChromaDB / Qdrant）通过 `MemoryBackend` Protocol 替换 DrawerStore
- Consolidation：L1/L2 与 MEMORY.md 周期性合并
- Verbatim → Summary 可选降维策略

## 9. 相关文档

- **实现细节**：`docs/impl/memory.md`（14 个 Python 文件的函数级索引）
- **Phase 1 详细计划**：`docs/superpowers/plans/2026-04-16-memory-palace.md`
- **Prompt 8 层注入**：`docs/impl/persona.md` §5
- **存储表结构**：`docs/impl/storage-and-events.md` §1
