# 记忆回忆 — FTS5 检索、官员间全局读、注入安全

> 设计意图：执行前把相关历史经验召回注入，同时保证注入内容不携带越权/注入风险。

## 1. 执行前召回（FTS5）

`BEFORE_AGENT_START` hook（`on_before_agent_start`）以 `edict.goal` 为 query 召回，注入到 Agent 的 history messages：

### 全量 FTS5 + recency 加权（`_recall_fulltext`）

- 可见范围 `persona_ids = [persona_id, "court"]`，有 department 时加 `_dept_{department}`
- 取 BM25 top `limit*4`，再二次打分：`bm25_pos × (0.5 + 0.5 × recency)`，recency `= exp(-0.693 × age_days / 30)`（半衰期 30 天，无 30 天硬窗口）
- 排序取 top-limit（默认 5），注入为 `[Memory context — do not respond to this] {content}`

### Drawer L2 召回

`l2_recall_enabled` 时另走 `MemoryStack.recall(goal, wing=persona_id, include_court=True)`，结果注入为 `[Palace 记忆 | {wing}/{room}] {content}`（取前 5 条）。

两路召回并存：FTS5 走 MD 派生索引（覆盖全部历史观察），Drawer 走 Palace（结构化分层）。

## 2. 官员间记忆全局读

默认每个官员只能召回**自己 + court + 本部门**的条目，但有两个放宽通道：

### memory_search 工具的可见范围

`memory_search` 按 ambient caller persona 自动限定 `visible_ids`：
- 自己私有（`persona_id == caller.id`）
- 本部门共享（`_dept_{caller.department}`）
- 朝廷共享（`court`）

例外：caller 的 `memory_global_read=True` 时跳过限定，`visible_ids=None` → 跨全 persona 检索。不在 agent 上下文（无 caller）时也跨 persona（向后兼容）。

### MemoryAccessControl（API 层）

`MemoryAccessPolicy(persona_id, can_read, can_write, share_level)` 控制 Web/API 召回的跨人格可见性：
- `share_level` ∈ {private, shared, court}
- `filter_readable(requestor, entries)`：自己的条目 / court 级 / share_level=shared 且 policy 允许 / 私有但来自允许的 persona → 放行
- `can_store(writer, entry)`：写入目标 persona 的权限校验

`DEFAULT_POLICIES` 预置六部读写白名单（如 wenyuan share_level=court，可写 bingbu/neige/ducha）。

## 3. memory_write — 按 section 锚定写入

Agent 主动记忆工具，路径完全由 `scope + caller_persona` 决定，**agent 不能传路径**（借鉴 hermes-agent 设计）：

| scope | storage_key（路径） | index_persona_id（FTS 标签） |
|---|---|---|
| `self` | `{caller.id}` | `caller.id` |
| `department` | `_dept/{dept}` | `_dept_{dept}` |
| `court` | `court` | `court` |

action = add / replace / remove，映射到 `write_section` 的 append/replace/remove。仅 `add` 同步索引到 `memory_entries`（replace/remove 不索引，避免删了旧文本索引仍残留）。写完 emit `memory.write` 审计事件。

## 4. 注入安全扫描（safety）

`safety.validate_content` 对 add/replace 的新内容做纯函数校验，失败抛 `MemorySafetyError`：

| 校验项 | 约束 |
|---|---|
| 非空 | 空内容拒绝 |
| 单条上限 | `MAX_CONTENT_CHARS=4000` |
| 不可见 Unicode | zero-width / RTL override 等 → 拒绝 |
| 控制字符 | `Cc` 类（除 `\t\n\r`）→ 拒绝 |
| 注入/凭证模式 | 18 种 regex（见下）命中即拒绝 |

`_INJECTION_REGEXES` 覆盖三类威胁（照搬 hermes 的 INJECTION_PATTERNS + 中文越狱变体）：
- **Prompt injection**：`ignore previous instructions`、`<\|system\|>`、`act as ... developer mode`、`reveal system prompt` 等
- **凭证外泄**：`curl ... $TOKEN`、`BEGIN PRIVATE KEY`、`AKIA[0-9A-Z]{16}`（AWS）、`sk-...`、`ghp_...`
- **中文越狱**：`忽略(以上|之前|前面)(所有)?(指令|要求|提示)`

另：`MAX_FILE_CHARS=32000`（整文件上限，`check_file_size` / `write_section` 前置检查）、`normalize_section`（H2 归一，标题 ≤80 字）。

## 5. 召回-注入完整链路

```text
Agent prompt 构建
  ├→ Layer 5    MEMORY.md            (读 Markdown 真相源)
  ├→ Layer 5.1  DrawerStore.get_l1   (L1 FTS5)
  └→ Layer 5.5  近 2 天日志           (读 Markdown)

BEFORE_AGENT_START hook
  ├→ _recall_fulltext(goal)                       (全量 FTS5 + recency)
  └→ MemoryStack.recall(goal, include_court=True) (L2 BM25)

工具 memory_search (Agent 主动)   → 可见范围限定 / global_read
工具 memory_write  (Agent 主动)   → safety 扫描 → write_section
AGENT_END hook                    → store(MD) + retain_drawers
```

**相关实现**：[../../impl/memory/](../../impl/memory/)
