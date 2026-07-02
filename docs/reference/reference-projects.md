# 天枢参考项目借鉴分析

> 本文记录**实际影响了当前代码**的参考项目。其他项目作为附录一行提及。

---

## 一、核心参考（实际落地）

### 1.1 Claude Code — Agent Loop + Skills + Compaction

feat_phase4 深度参考。落点见 [plan](../superpowers/plans/2026-04-02-phase1-agent-loop-redesign.md) 与 [design/agent](../design/agent/)、[impl/agent](../impl/agent/)。

**采纳点**：

1. **ExitReason 枚举**（10 种退出原因）—— 取代此前的 `bool success` + 错误字符串。明确区分 `completed` / `max_iterations` / `context_overflow` / `timeout` / `cancelled` / `hook_blocked` / `budget_exhausted` / `llm_error` / `output_truncated` / `repeated_tool_failure`。
   落点：`src/tianshu/executor/exit_reason.py`

2. **LoopState frozen dataclass** —— 每轮返回新对象（`next_turn` / `with_recovery` / `with_compacted` / `accumulate_usage`），消除隐式状态。
   落点：`src/tianshu/executor/loop_state.py`

3. **3 层 Compaction** —— `reactive`（上下文溢出救急）+ `micro`（每轮末尾预防性截断非最近 tool_result）+ `auto`（token 阈值触发 LLM 摘要中段，保留首尾）。
   落点：`src/tianshu/executor/compaction/{auto,reactive,micro}.py`

4. **Skills 渐进加载** —— `load_index` 只注入名称 + 描述，`load_always` 注入 `always=true` 的完整 SKILL.md，LLM 通过 `skill_view` 工具按需取全文。
   落点：`src/tianshu/skills/loader.py` + PromptBuilder Layer 7

5. **Anthropic prompt_caching** —— 对 `claude*` / `anthropic/*` 模型插入 `cache_control: ephemeral` 断点（system + 最后 3 非 system 消息），~75% 输入 token 节省。
   落点：`src/tianshu/llm.py` `_apply_prompt_caching`

6. **Streaming** —— `StreamCallback` protocol（`on_delta` / `on_tool_call_start` / `on_tool_call_end`）+ `WebSocketStreamCallback`（`notifier/notifier.py`）桥到前端。
   落点：`src/tianshu/executor/streaming.py`

7. **Hook 生命周期系统** —— 10 种 HookType，priority 排序，`HookResult(block, reason, modified_args)` 可阻断或改写。
   落点：`src/tianshu/executor/hooks.py`

### 1.2 Hermes Agent — 安全与模糊匹配

feat_phase4 深度参考。落点见 [plan](../superpowers/plans/2026-04-09-hermes-inspired-enhancements.md)。多处代码头部注释标记 `Ported from hermes-agent`。

**采纳点**：

1. **Guard 安全扫描** —— 13 个威胁类别（exfiltration / injection / invisible_unicode / destructive / persistence / network / obfuscation / execution / supply_chain / credential_exposure / traversal / mining / privilege_escalation）× 50+ regex + 无形 Unicode 字符检测（ZWSP / RLM / LRM / tag chars）+ TrustLevel（builtin / trusted / community / agent-created）策略矩阵。
   落点：`src/tianshu/skills/guard.py`

2. **FuzzyMatch 8 策略链** —— `exact → line_trimmed → whitespace_normalized → indentation_flexible → escape_normalized → trimmed_boundary → unicode_normalized → block_anchor`，用于 skill patch / 代码片段查找替换。
   落点：`src/tianshu/skills/fuzzy_match.py`

3. **Skills 3 层缓存** —— L1 LRU（`get_skill` 结果）+ L2 stat 快照（mtime/size 避免重扫）+ L3 磁盘扫描（首次 / mtime 变动）。
   落点:`src/tianshu/skills/loader.py`

4. **ToolResult 截断** —— 超 `ToolDefinition.max_result_chars` 自动 truncate 并附省略说明。
   落点：`src/tianshu/executor/agent.py`

此外，多个模块带显式 `Ported from` / `参考 hermes` 注释（已对照源码核实）：

| 落点 | 借鉴内容 |
|---|---|
| `skills/guard.py` | `Ported from hermes-agent's skills_guard.py` |
| `skills/fuzzy_match.py` | `Ported from hermes-agent's fuzzy_match.py` |
| `gateway/telegram/markdown_v2.py` | 移植自 hermes `gateway/platforms`（Markdown V2 渲染） |
| `tools/memory_tools.py` | `memory_write` 借鉴 hermes `tools/memory_tool.py` |
| `tools/schedule_edict.py` + `scheduler/schedule_spec.py` | 统一 `cronjob` 工具（`action` 分发 + deliver 渠道 + 自然语言 schedule） |
| `memory/safety.py` | 记忆注入扫描借鉴 hermes `memory_tool.py` |
| `tools/mcp/client.py` | MCP 重连重试 / 生命周期等待参照 hermes |
| `gateway/feishu/security.py` + `settings.py` | 空 allowlist = 放行任意人，与 hermes 行为一致 |

### 1.3 NanoBot — 分层记忆与 Subagent

feat_phase3 + feat_phase5 参考。

**采纳点**：

1. **双层记忆（MEMORY.md + 日志）** —— NanoBot 的 `MEMORY.md`（长期）+ `HISTORY.md`（历史流）模式，天枢演化为 `MEMORY.md`（核心长期）+ `YYYY-MM-DD.md`（daily log）+ court 共享。
   落点：`src/tianshu/memory/markdown_backend.py`

2. **分层 Context 构建** —— NanoBot 4 层（Identity → Bootstrap → Memory → Skills），天枢扩展为多层有序注入（+ Court / 身份卡 / L1 Palace / 部门记忆 / 近期日志 / 同僚画像 / Task Context 等）。
   落点：`src/tianshu/persona/prompt_builder.py`

3. **Subagent 隔离** —— 子代理独立工具集防止递归失控。天枢的 DAG 节点也采用此思路，每个子节点独立 persona + memorial。
   落点：`src/tianshu/executor/dag_scheduler.py`

4. **三模式调度（at / every / cron）** —— 天枢的 `Scheduler` 支持 `immediate` / `at` / `cron` 三种 schedule_type。
   落点：`src/tianshu/scheduler/scheduler.py`

### 1.4 DeepAgents — 多代理与 Subagent 上下文隔离

feat_phase3 参考。

**采纳点**：

1. **SubAgent context isolation** —— 子代理拥有独立 context，不污染父 agent。天枢的每个 DAG 节点独立 memorial + prompt，符合此模式。

2. **Summarization** —— DeepAgents 的 context summarization 策略启发了天枢的 `auto_compact`（见 `src/tianshu/executor/compaction/auto.py`），`_extract_existing_summary` + `_format_for_summary` 避免重复摘要。

3. **Planning step** —— DeepAgents 将 plan 作为独立阶段产物。天枢 `Planner` 产出结构化 `Plan` → DAG 节点展开，而非让 Agent 即兴规划。

## 二、其他参考项目（附录）

以下项目有启发但未直接落地代码，或仅提供了次要的灵感片段：

| 项目 | 语言 | 启发点 | 落地程度 |
|---|---|---|---|
| CoPaw | Python / FastAPI | 生产级 FastAPI 架构参考 | 架构形状参考 |
| PicoClaw | Go | Worker 队列 + 工作区隔离；ToolResult 语义分类 | WorkerPool 思路 |
| ZeroClaw | Rust | Trait 可插拔（MemoryBackend / ToolRegistry Protocol 抽象） | Protocol 思路 |
| Pi-Mono | TypeScript | 事件系统 + 不可变上下文 + Factory 优于 DI | LoopState immutable 设计理念 |
| OpenClaw | TypeScript | SOUL.md / AGENTS.md Bootstrap 文件体系；Markdown-as-SOT | 人格模板结构 |
| OpenCode | TypeScript | 开源编程 Agent 的 tool 分级 | Policy tier 思路 |
| Crush | Go | 终端编程助手的 skill 组织 | — |
| Kimi-CLI | Python | wire 协议、approval、compaction | Approval pattern |

详细代码路径分析已归档，未来需要查询时可重新展开。

---

## 三、参考采纳总表

| 天枢能力 | 参考源 | 落点代码 |
|---|---|---|
| ExitReason / LoopState / Compaction / Streaming | Claude Code | `executor/` |
| Anthropic prompt cache | Claude Code | `llm.py` |
| Skills 渐进加载 + Hook 生命周期 | Claude Code | `skills/loader.py`, `executor/hooks.py` |
| Guard 13 × 50 regex + TrustLevel | Hermes | `skills/guard.py` |
| FuzzyMatch 8 策略 | Hermes | `skills/fuzzy_match.py` |
| Skills 3 层缓存 | Hermes | `skills/loader.py` |
| ToolResult 截断 | Hermes | `executor/agent.py` |
| 双层记忆（MEMORY.md + daily log） | NanoBot | `memory/markdown_backend.py` |
| 分层 Context（8 层） | NanoBot（4 层扩展） | `persona/prompt_builder.py` |
| 三模式调度 | NanoBot | `scheduler/scheduler.py` |
| Subagent 隔离 | NanoBot + DeepAgents | `executor/dag_scheduler.py` |
| Auto compact 摘要中段 | DeepAgents | `executor/compaction/auto.py` |
| Planner / Plan 独立阶段 | DeepAgents | `planner/planner.py` |

---

## 四、下一步潜在借鉴

- **Memory Palace Phase 2+ 向量后端** —— 参考 NanoBot 的 heartbeat 两阶段决策模式，让 `Reflector` 通过 tool_call 做结构化反思决策
- **Pi-Mono 的 EventStream 不可变链** —— 若未来将 LoopState + EventBus 融合成更强的时间旅行调试，可参考
- **OpenClaw 的 channels 架构** —— 若要扩展通知通道到 10+，可参考其 11 通道抽象
- **Multica 的控制平面设计** —— 调度自愈（Sweeper）、并发治理、控制/数据平面解耦，契合 Phase 3 分布式。详见 [multica-analysis.md](./multica-analysis.md) 与落地方案 [../superpowers/plans/2026-07-02-multica-inspired-control-plane.md](../superpowers/plans/2026-07-02-multica-inspired-control-plane.md)

---

## 五、天枢原创设计（非借鉴）

以下能力是天枢在参考项目基础上的原创扩展，未直接对应某个外部项目。完整原创设计专篇（含真原创 / 原创封装 / 借鉴边界的诚实分级、机制类名、代码落点、设计文档）见 [original-designs.md](./original-designs.md)。

精简指针：六部官制组织、诏令→题本→批红 领域模型、长任务 Outer Loop + 验收标准、平行位面演化（Universe）、代码变体位面——以上为天枢原创，逐条展开见 [original-designs.md](./original-designs.md)。位面演化与代码变体是当前阶段（feat_phase8）的重点，设计见 [../design/universe/](../design/universe/)。
