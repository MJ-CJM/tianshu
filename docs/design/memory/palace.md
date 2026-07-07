# 记忆宫殿 — 隐喻、数据模型与生命周期

> 设计意图：把多 Agent 系统的长期记忆组织成可检索、可分层、可隔离的结构。

## 1. 隐喻与边界

整座系统记忆是一座宫殿，每个官员是一座偏殿，偏殿按主题分房间，房间里放抽屉：

| 概念 | 隐喻 | 对应代码 | 边界 |
|---|---|---|---|
| Palace | 宫殿 | 全局 `DrawerStore` | 一个进程一座 |
| Wing | 偏殿 | `wing` = persona_id / `court` / `emperor` | 私有，court 共享 |
| Room | 房间 | `room: str` | 主题内聚，便于降维 |
| Drawer | 抽屉 | `Drawer`（frozen dataclass） | 最小单元，≤800 字符 |
| Closet | 索引柜 | `Closet`（预留） | 主题指针，不存内容 |
| Tunnel | 暗道 | `Tunnel`（预留） | 跨 wing 联想链接 |

三条核心原则：**Verbatim 原文**（存原文不即时摘要）、**Wing 隔离但 Court 共享**、**主题内聚**（同 wing 按 room 组织）。Closet / Tunnel 当前仅定义模型，未启用。

### Wing 三类

| Wing 类型 | 数据归属 | 隐私边界 |
|---|---|---|
| 六部官员 | 各自执行记忆 | Drawer 私有；同部门 / court 可见区单独标注 |
| court | 跨人格共识 | 所有 persona 可读可写 |
| emperor | 用户个人画像 | 用户独享（agent 侧只读，后续阶段实现） |

## 2. Drawer 数据模型

```python
@dataclass(frozen=True)
class Drawer:
    id: str
    wing: str              # persona_id / "court" / "emperor"
    room: str              # 主题房间
    content: str           # 原文片段（≤ 800 字符）
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

Category 让 L1 / L2 可按主题过滤（如 ducha 偏 D+O，bingbu 偏 W）。注：`MemoryManager.store()` 写 Markdown 日志用的是另一套字母 `W/B/O/S`（observation→W, insight→O, entity→B, summary→S），与 Drawer 的 `W/B/O/D` 不完全等同，分属两个写入路径。

### 相关模型

- `DrawerResult`：search 返回，含 `score` + `matched_via`（"bm25" / "fallback"）
- `Closet(topics, entities, drawer_ids)`：主题指针（预留）
- `Tunnel(from_wing/room, to_wing/room, reason, created_by)`：跨 wing 链接（预留）
- `MemoryEntry`：Web/API 层传统条目，`category` 取 observation/insight/entity/summary

## 3. MemoryManager 生命周期

`MemoryManager` 是 split-write 协调者，区分写入源与检索索引。

### 写路径（MD = 真相源）

| 触发 | 动作 |
|---|---|
| `AGENT_END` (`on_agent_end`) | 任务完成且有 summary 时写 MD 观察条目（write-through 刷索引）+ `retain_drawers` chunk 入 Palace（category="W", confidence=0.9） |
| `audit.completed` (`handle_audit_completed`) | verdict∈{flag,block} 写 ducha insight（access_level="court"） |
| `memory_write` 工具 | Agent 主动按 H2 section 锚定写入（见 recall.md） |
| reflect / compact | 周期性产出 insight / 摘要写回 MEMORY.md |

`store()` 先写 MD daily log（唯一真相），再 write-through 刷 SQLite + FTS；索引写失败只记日志不阻断，可后续 `sync_index` 修复。

### 读路径

| 消费方 | 来源 |
|---|---|
| Agent prompt 注入 | `BEFORE_AGENT_START` → FTS5 全量召回（MD 派生索引）+ Drawer L2 recall |
| Web/API 查询 | SQLite `memory_entries` index（`recall(source="sqlite")`） |

### 生命周期辅助

- `_resolve_persona_id(context, plan)`：三级 fallback — `context["persona"].id` → `plan.tasks[0].assigned_official` → `DEFAULT_EXECUTOR_ID`
- `_infer_room(edict)`：按 goal 关键词映射 room（execution/planning/audit/tools/recovery/cost-patterns/general）
- `compact(persona_id)`：>5 条 daily entry 时 LLM 摘要，非破坏写入 `## 历史摘要` section
- `reflect(persona_id)`：从近 7 天日志反思产出 insight，写 MEMORY.md + 追加 daily log
- `sync_index` / `auto_sync_if_needed`：从 MD 重建 SQLite index（每会话每人格一次）

**相关实现**：[../../impl/memory/](../../impl/memory/)
