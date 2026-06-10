# agent 执行引擎 · 设计总览

## 1. 职责定位

agent 子系统是天枢的「执行之手」：把一道 `plan.completed` 后的指令真正跑成结果。它对外只暴露三种执行形态，由 Executor 按 Edict 决策分派：

| 形态 | 触发条件 | 核心模块 |
|---|---|---|
| 单任务 ReAct | 单任务 plan，无 acceptance | `executor/agent.py` |
| 多任务 DAG | `plan.tasks > 1` | `executor/dag_scheduler.py` + `dag/` |
| 长任务 outer loop | `edict.acceptance` 非空 | `executor/orchestrator/` |

三者共享同一套底座：不可变 `LoopState`、统一 `HookRegistry` 生命周期钩点、三层上下文压缩、`WorkerPool`/`LaneManager` 并发治理。

## 2. 核心设计判断

- **不可变状态**：`LoopState`（单轮）与 `OuterLoopState`（外循环）都是 `@dataclass(frozen=True)`，每轮通过 `next_turn`/`advance` 等方法返回新对象，永不原地修改——降低隐式状态、便于 checkpoint 与回放。
- **退出原因显式化**：`ExitReason` 枚举把「为什么停」变成可分派的一等公民，每个值对应一种 post-exit 处理策略，避免用布尔/异常含糊表达终态。
- **压缩分层而非单点**：micro（零成本、每轮）/ auto（阈值预防）/ reactive（溢出救急）三层各司其职，先便宜后昂贵、先预防后补救。
- **治理走钩点不走硬编码**：policy、审批、记账、记忆、画像都通过 `HookRegistry` 注册到固定钩点，Agent 主循环不感知具体治理逻辑。
- **DAG 是 plan 的派生物**：多任务 plan 经 `plan.to_dag()` 转 DAG，节点级 persona/Memorial/checkpoint，失败沿拓扑下游传播取消。
- **长任务是「监督闭环」而非「一次输出」**：outer loop 把 actor→checks→critic→completion audit→升级 串成可验收、可升级、可软着陆的外循环，解决「跑完但没达标」的终态漏洞。

## 3. 与相邻子系统关系

| 相邻子系统 | 关系 |
|---|---|
| planner | 上游：产出 `Plan`，决定单任务 / DAG / outer loop 分派依据 |
| persona | 横向：`PromptBuilder` 构建 system prompt，DAG 节点按 `assigned_official` 取 persona |
| tools | 横向：`ToolRegistry` 执行工具，`PolicyHook` 在 `BEFORE_TOOL_CALL` 治理 |
| memory | 横向：`MemoryManager` 在 `BEFORE_AGENT_START`/`AGENT_END` recall/retain |
| cost | 横向：`CostManager` 在 `BEFORE_ITERATION`/`LLM_OUTPUT` 预算检查与记账 |
| audit / notifier | 下游：执行完成发 `execution.completed` / `outer_loop.*` 事件 |

## 4. 本目录子文档索引

| 文档 | 主题 |
|---|---|
| [react-loop.md](./react-loop.md) | ReAct 主循环、LoopState、ExitReason、StreamCallback |
| [action-space.md](./action-space.md) | 工具粒度与 observation 设计纪律、截断/错误措辞、失败时回给模型什么 |
| [compaction.md](./compaction.md) | reactive / micro / auto 三层上下文压缩 |
| [dag.md](./dag.md) | DAG 图模型、拓扑排序、节点级 Memorial、WorkerPool、LaneManager |
| [harness.md](./harness.md) | 执行壳：节点级受控执行、WorkerPool + 双层泳道、级联取消/部分重试/checkpoint |
| [orchestrator.md](./orchestrator.md) | 长任务 outer loop、AcceptanceCriteria 验收、critic、L0-L3 升级 |
| [hooks.md](./hooks.md) | HookRegistry、HookType、priority、与 PolicyHook/ApprovalManager 关系 |

**相关实现**：[../../impl/agent/](../../impl/agent/)
