# 04 Persona、Memory 与 Skills

## 1. 六部 Persona

Persona 是系统的角色化执行边界。当前模板位于 `personas/{id}/`，运行时覆盖位于 `~/.tianshu/personas/{id}/`。

| id | 部门 | 主要职责 |
|---|---|---|
| `neige` | 内阁 | 规划、协调、战略拆解 |
| `bingbu` | 兵部 | 默认执行者，`DEFAULT_EXECUTOR_ID` |
| `ducha` | 都察院 | 审计、复核、监督 |
| `tongzheng` | 通政司 | 通知、渲染、飞书/会话协调 |
| `wenyuan` | 文渊阁 | 文档、知识、记忆 |
| `hubu` | 户部 | 成本、预算、账本 |
| `court` | 朝廷共享 | 跨 persona 共享上下文，不是普通执行人格 |

PersonaLoader 会加载模板、合并运行时覆盖，并同步部门/persona 元数据到 SQLite。

## 2. PromptBuilder 分层

`persona/prompt_builder.py` 是人格上下文的核心。当前系统 prompt 分层：

| 层 | 内容 | 来源 |
|---|---|---|
| 1 | Base Identity | 内置 |
| 2 | Court Protocol | `personas/court/COURT.md` |
| 2.5 | Identity Card | persona 元数据 |
| 3 | Persona Identity | `SOUL.md` |
| 4 | Role Specification | `ROLE.md` |
| 5 | Agent Memory | `~/.tianshu/memory/{persona}/MEMORY.md` |
| 5.1 | L1 Critical Facts | DrawerStore / Memory Palace |
| 5.5 | Recent Activity | 最近 2 天 Markdown logs |
| 5.6 | Department Memory | `~/.tianshu/memory/_dept/{department}/MEMORY.md` |
| 6 | Court Memory | `~/.tianshu/memory/court/MEMORY.md` |
| 6.5 | Peer Profiles | 同僚近况 |
| 7 | Skills | skill index + always-on skill body |
| 7c | Network Capability Hint | 内置网络能力说明 |
| 8 | Task Context | 当前 Edict ID |

PromptBuilder 的设计目标是把人格、记忆、技能、任务上下文集中在一处组装，避免各执行路径自己拼 prompt。

## 3. Memory 架构

MemoryManager 明确区分写入源和检索索引：

```text
Agent / audit 写入
   -> Markdown daily log / MEMORY.md
   -> DrawerStore chunk

Web / API 查询
   -> SQLite memory_entries + FTS
   -> 可从 Markdown sync 重建
```

关键原则：

- Markdown 是人格长期记忆的可读真相源。
- SQLite 是展示和搜索索引，不应成为唯一记忆源。
- DrawerStore 存 chunk，用于 Memory Palace 的 L1/L2 检索。
- `MemoryAccessControl` 控制不同 persona 之间的读写权限。

## 4. Memory 写入路径

| 触发 | 动作 |
|---|---|
| `BEFORE_AGENT_START` | 从记忆中 recall，注入 history |
| `AGENT_END` | 写执行摘要、观察和 drawer |
| `execution.completed` | 处理执行完成后的沉淀 |
| `audit.completed` | 把审计 insight 写入相关记忆 |
| API 操作 | 可写入/同步 SQLite index 和 Markdown |

`retain_drawers()` 会把内容 chunk 成 Drawer，写入 `drawers.sqlite3`，保留 wing、room、edict、category、confidence、chunk_index 等元数据。

## 5. Skills 渐进加载

SkillsLoader 支持三层来源：

1. builtin：`src/tianshu/skills/builtin/`
2. workspace：当前工作区 skill
3. user：`~/.tianshu/skills/`

Prompt 里不会默认塞入所有 skill 全文，而是：

- 注入 skill index，让 LLM 知道有哪些技能；
- 对 `always=true` 的 skill 注入全文；
- 通过 `skill_view` 等工具按需加载；
- 通过 `SkillMetricsStore` 记录使用、成功、失败。

`SkillReviewHandler` 在 `AGENT_END` 后复盘 skill 使用情况，`SkillValidator` 用于校验新 skill 或修改建议。

## 6. 画像合成

`ProfileSynthesizer` 和 `ProfileTrigger` 负责 persona 成长画像：

- Agent 结束后增加 persona metrics；
- 达到触发条件后合成 PROFILE；
- 结合 DrawerStore、Storage、skill metrics、runtime persona dir；
- 合成过程通过 `persona_metrics.synthesis_in_progress` 做并发保护。

这条链路让 persona 不只是静态 prompt，而是能基于任务历史逐渐形成“擅长什么、近期状态如何、退化在哪里”的运行时档案。

## 7. Consultation

`ConsultationSession` 用于多 persona 会诊。它在两个地方重要：

- 用户主动发起咨询，获得结构化建议；
- outer loop 升级到 L2 时，系统调用会诊结果作为下一轮 actor 的建议。

这把“多 Agent 协作”限制在明确场景里，避免主链路过早复杂化。
