# 会诊（consultation）— 多人格并行决策

> 设计意图：当外层循环靠单一 actor/critic 反复打回仍无法收敛时，引入多个人格「会诊」——并行收集各部门视角的意见，再由一个汇聚人格把分歧合成一个决策建议，作为 L2 升级的智囊，避免直接惊动人类（L3）。

**相关实现**：[../../impl/consultation/README.md](../../impl/consultation/README.md)

## 1. 两层结构：并行收集 → LLM 汇聚

会诊是「决策并行」：同一个问题分发给 N 个人格，每个人格各自给意见，**互不依赖**，跑完再汇总。

```text
ConsultationRequest(topic, persona_ids)
   │
   ├─ persona A ─┐
   ├─ persona B ─┤  asyncio.gather 并行调 LLM，各出一份 PersonaOpinion
   ├─ persona C ─┘  （return_exceptions=True：单个人格失败不拖垮整场）
   │
   ▼
Synthesizer.synthesize(request, opinions)   ← 第二次 LLM 调用
   │   把所有 opinion 拼进一个 prompt，让「首席顾问」人格
   │   合成主题/分歧 + 给出决策建议
   ▼
ConsultationResponse(opinions, synthesis, decision)
```

第一层 `ConsultationSession.start` 负责 fan-out 并行收集；第二层 `Synthesizer` 是一次独立的 LLM 调用做 fan-in 汇聚。两层都用同一个 LLM client（优先 `ProviderManager.get_client()`，否则按 config state 现起一个 `LLMClient`）。

## 2. 三个数据模型（`consultation/models.py`）

| 模型 | 字段 | 角色 |
|---|---|---|
| `ConsultationRequest` | `topic` / `context` / `edict_id` / `persona_ids` / `synthesizer_persona_id` | 入参：议题 + 参与人格。`persona_ids` 为空时由 session 取「全部人格」 |
| `PersonaOpinion` | `persona_id` / `persona_name` / `department` / `opinion` / `confidence` / `key_points` | 单个人格的意见 |
| `ConsultationResponse` | `id` / `status` / `opinions` / `synthesis` / `decision` / `created_at` / `completed_at` | 出参：聚合结果，`status` ∈ pending/running/completed/failed |

> `models.py` 另有一个未在主流程使用的 `ConsultationResult`（精简版三元组），当前 session 走的是 `ConsultationResponse`。

### confidence 当前是占位值（重要）

`PersonaOpinion.confidence` 字段虽然存在，但 `ConsultationSession._get_opinion` 在构造 opinion 时**硬编码 `confidence=0.8`**——尽管 prompt 让人格自报置信度，返回文本并未被解析回填到该字段。`key_points` 同理恒为 `[]`。

也就是说：**confidence 目前只是占位值，尚未实现真正的置信度汇聚机制**。Synthesizer 的 prompt 里会把 `confidence=0.8` 原样渲染给 LLM，但它对所有人格都是同一个常数，不构成有效的加权信号。文档不宣称「已按置信度加权」；要做到真汇聚，需先在 `_get_opinion` 里解析 LLM 输出的置信度与要点。

## 3. 与 outer loop L2 的集成 + 降级 L3 的 fallback

会诊是长任务外层循环的 **L2** 智囊层，介于 L1（自动重试）和 L3（人工介入）之间。集成见 [../agent/orchestrator.md](../agent/orchestrator.md)。

```text
FAIL → decide_escalation
  ├─ 升级到 L2 且 consultation_session 存在：
  │     _run_consultation(edict, state)
  │       topic = goal + 上一轮 actor 输出 + critic 反馈 + 连续打回轮数
  │       persona_ids = acceptance.escalation.l2_consultation_personas
  │       advice = resp.synthesis → state.with_consultation_advice(advice)
  │   会诊抛异常 → except 捕获 → 直接 with_level("L3") 走人工
  └─ L3：_escalate_to_human（推送 + 等审批）
```

降级语义有两处：
- **会诊不可用**：`ctx.consultation_session is None` 时 `_run_consultation` 返回 `None`，外层循环略过 L2、按既有逻辑继续。
- **会诊失败/异常**：`_run_consultation` 抛错被 `try/except` 捕获 → 记 `outer_loop.escalated{from:L2,to:L3}` 审计 → 强制升 L3 交人类。

会诊的产物是「建议文本」(`synthesis`)，注入 `OuterLoopState` 供下一轮 actor 参考，**不是终态决策**——是否采纳由后续循环或 L3 人类裁定。

## 4. 结果落 court Markdown（source of truth）+ 写穿索引

会诊完成且有 `synthesis` 时，`ConsultationSession.start` 把结果写入「court」公共记忆（仅在注入了 `memory_manager` 时；orchestrator 注入了，gateway 路由也注入了）：

1. `MemoryEntry(persona_id="court", category="insight", access_level="court")` → `memory_manager.store`：先写 Markdown（source of truth）再 write-through 索引。
2. 追加一段 `## Consultation (YYYY-MM-DD)` 到 `court/MEMORY.md`（`md_backend.read_core_memory` → 拼接 → `write_core_memory`），含摘要与 decision 摘录。

记忆的「Markdown 为权威 + 索引为可查缓存」语义见 [../memory/backends.md](../memory/backends.md)。落盘失败只 `logger.debug`、不影响会诊返回——结果落盘是尽力而为的副作用，不阻塞决策。

## 5. 会诊 ≠ DAG：决策并行 vs 任务并行

两者都用并行，但并行的**对象**和**目的**完全不同，不要混淆：

| 维度 | 会诊（consultation） | DAG（任务编排） |
|---|---|---|
| 并行的是 | 对**同一个问题**的多个**视角/意见** | **不同的子任务**（拆解后的工作项） |
| 目的 | 收敛出一个**决策建议** | 推进**完成多项工作** |
| 输入相同？ | N 个人格看同一 topic | 各节点输入不同、有依赖边 |
| 汇聚方式 | LLM 把意见合成决策 | 按依赖图收集各节点产物 |
| 触发点 | outer loop L2 升级 | 任务规划/调度 |
| 设计文档 | 本篇 | [../agent/dag.md](../agent/dag.md) |

一句话：**会诊是「多人对一事的并行决策」，DAG 是「一人对多事的并行执行」**。
