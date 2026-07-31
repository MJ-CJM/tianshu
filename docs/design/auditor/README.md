# 审计子系统（Auditor）— 两层审计 + 三态裁决 + 人审分档

> 设计意图：在执行结束、奏折落库前插一道**自动质检门**，用「快规则筛 + LLM 复审」两层把关，把「结果是否可信、是否需要人看」变成可持久化、可订阅的裁决数据，而不是让所有任务都默默 COMPLETED。

**相关实现**：[../../impl/auditor/README.md](../../impl/auditor/README.md)

## 1. 触发时机与事件流

Auditor 同时订阅 `execution.completed` 和 `execution.failed`。成功进入可见的 AUDITING
阶段；失败保持 FAILED，再按 policy 判断是否审计：

```text
执行完成 → emit execution.completed
  → Auditor.handle_execution_completed   (无显式 priority，最先跑)
  → CostManager.handle_execution_completed (priority=150)
  → MemoryManager.handle_execution_completed (priority=200)

Auditor 内部：
  取出 edict + memorial
  → memorial.status==COMPLETED 时先翻 AUDITING（让前端看到"审计中"）
  → 按 review_policy 分档决定是否真审（见 §4）
  → audit() 两层裁决 → 写回 memorial.audit / status / review_status
  → emit audit.completed { verdict, reasons }
      → Notifier.handle_audit_completed        (按裁决决定是否外发)
      → MemoryManager.handle_audit_completed    (priority=200)
      → _update_universe_fitness               (priority=250，喂位面适应度)

执行失败 → emit execution.failed
  → Auditor.handle_execution_failed
  → 保留 FAILED，按 on_failure/on_flag/always 审计
  → audit.completed（不得把 executor failure 改写为成功）
```

关键设计点：

- **被动订阅而非主动调用**：执行器只管发 `execution.completed`，审计是否发生、怎么发生全由 Auditor + edict 的 `review_policy` 决定，执行器不感知审计逻辑。
- **AUDITING 是过渡态**：进入审计先把 COMPLETED 翻成 `AUDITING`，前端因此能区分「执行完」和「审计完」两个阶段。
- **audit.completed 是裁决广播**：payload 只带 `verdict` + `reasons`，下游（通知、记忆、位面适应度）各取所需，互不耦合。

## 2. RulesEngine — 第一层快规则

`RulesEngine.check(edict, memorial)` 是**同步、无 LLM、零网络**的快筛，逐条累加 `reasons` 并计 `rules_checked`：

| 规则 | 条件 | 命中后果 |
|---|---|---|
| Token 预算 | `edict.runtime.token_budget` 存在且 `memorial.usage.total_tokens` 超额 | 加一条 reason |
| 执行错误 | `memorial.error` 非空 | 加一条 reason |
| 空结果 | 既无 `result` 也无 `error` | 加一条 reason |
| 风险关键词 | `AuditRulesConfig.risk_keywords` 命中结果 | 加一条 reason |

裁决规则极简：**无 reason → `pass`；有任何 reason → `flag`**（当前实现里 error 分支与默认分支都返回 `flag`，RulesEngine 自身不产 `block`——`block` 只来自第二层 LLM）。

设计取舍：第一层只做**确定性、可解释、便宜**的检查，不做语义判断。它的职责是「快速放行明显 OK 的、把可疑的交给第二层」，而非自己下最终结论。

## 3. LLMReviewer — 第二层语义复审

只有第一层裁决为 `flag` **且** `review_policy != "never"` 时才触发——昂贵的 LLM 调用被快规则挡在门外。

`LLMReviewer.review(edict, memorial, rule_reasons)`：

- 用一段固定 system prompt 要求 LLM 以 JSON 回 `{"verdict": "pass"|"flag"|"block", "reasons": [...]}`；user 消息带 `goal`、截断到 2000 字的 `result`、第一层的 `rule_reasons`。
- LLM 配置（model/key/base）取自 `ConfigManager.state`；temperature/max tokens 取实际
  `AuditRulesConfig`。调用携带 edict/memorial/`audit_review` usage context，进入统一成本
  账本。
- **降级而非崩溃**：`state.enabled` 为假（未配 LLM）→ 直接回 `flag` + "LLM reviewer unavailable"；调用/解析异常 → 回 `flag` + "LLM review failed"。两种降级都 `llm_reviewed=False`，且裁决取 `flag`（保守地交人审），绝不把不确定当 `pass` 放过。

第二层是唯一能把裁决升级到 `block`（明显错误/有害）或反过来洗白成 `pass`（规则误报但语义达标）的环节。

## 4. pass / flag / block 三态与 review_policy 分档

裁决三态及其对 memorial 的影响：

| verdict | 含义 | memorial 状态机 |
|---|---|---|
| `pass` | 审计未发现问题 | 原执行成功才 `COMPLETED`；原执行失败仍 `FAILED` |
| `flag` | 有疑点，需人看 | `review_status=pending`，`status=NEEDS_REVIEW` |
| `block` | 明显错误/有害 | `status=FAILED`，`error="Blocked by audit: ..."` |

`edict.review_policy`（`Literal["never","on_failure","on_flag","always"]`，默认 `never`）决定**是否真跑 audit**：

| review_policy | 行为 |
|---|---|
| `never` | 跳过审计，直接 `AuditResult(verdict="pass")`；后续也不会触发 LLM |
| `on_failure` | 仅当 `memorial.status==FAILED` 才审，否则视作 pass |
| `on_flag` | 总是跑两层（第一层 flag 了才进第二层） |
| `always` | 跑两层审计，**且无论裁决如何都强制人审**（`review_status=pending` + `NEEDS_REVIEW`） |

`always` 是特例：它把裁决结果当参考，但最终一律落 `NEEDS_REVIEW`——适用于高风险敕令「机器审完也必须人点头」。

**周期性敕令豁免自动收口**：当裁决 `pass` 且无需人审、edict 仍 OPEN 时，Auditor 会顺手把 edict 翻 `COMPLETED`——但 `schedule.type ∈ {cron, interval}` 的敕令被排除在外，必须保持 OPEN，否则下一轮调度会因 edict 非 open 而停摆。

## 5. 与 outer loop completion audit 的关系

系统里有**两个名字都带 audit 的机制，职责与时机完全不同**，不要混淆：

| 维度 | 本子系统 Auditor | outer loop completion audit |
|---|---|---|
| 位置 | `auditor/`，事件订阅者 | `executor/orchestrator/audit.py`，长任务 outer loop 内 |
| 时机 | 执行**整体结束后**、奏折落库前 | critic pass **之后**、收工**之前**的 loop 内一关 |
| 触发 | 订阅 `execution.completed` | `loop.py` 主循环主动调 `run_completion_audit` |
| 判什么 | 结果是否可信、是否要人审（token/error/空结果 + 语义） | acceptance 每条验收要求是否都有**具体证据**（gap 检查） |
| 产物 | `AuditResult{verdict,reasons}` → `audit.completed` | `AuditResult{passed,gaps}` → `edict.audit.executed` |
| 失败后果 | block→FAILED / flag→人审 | gaps 反哺为下一轮 actor 续转 prompt，继续迭代 |

二者是**串行互补**：completion audit 是 outer loop 内部的「验收覆盖审」，确保任务自己迭代到位才肯收工；本 Auditor 是收工之后的「出门质检」，决定这份成品要不要拦下来或交人复核。一个对内驱动迭代，一个对外把关交付。
