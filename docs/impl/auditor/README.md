# 审计子系统（Auditor）实现现状

**相关设计**：[../../design/auditor/README.md](../../design/auditor/README.md)

> 代码位于 `src/tianshu/auditor/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图（触发时机、三态裁决、与 outer loop completion audit 的区别）见 design 篇。

## 1. 模块清单（`src/tianshu/auditor/`）

| 文件 | 关键类 | 职责 |
|---|---|---|
| `auditor.py` | `Auditor` | 编排器：订阅 `execution.completed`，按 `review_policy` 跑两层、写回 memorial、发 `audit.completed` |
| `rules.py` | `RulesEngine` | 第一层快规则：`check(edict, memorial)` 同步检查 token 预算 / 执行错误 / 空结果，返回 `AuditResult` |
| `reviewer.py` | `LLMReviewer` | 第二层语义复审：`review(edict, memorial, rule_reasons)`，仅在 flag 时由 `Auditor` 调用 |
| `__init__.py` | — | 仅 re-export `Auditor` |

数据契约 `AuditResult`（`verdict / reasons / rules_checked / llm_reviewed`）定义在 `models/common.py`，**不在本包内**。

## 2. 装配（`app.py` lifespan）

```text
Auditor(event_bus, storage, config_manager)
  ├─ 内部 new RulesEngine()       # 无依赖
  └─ 内部 new LLMReviewer(config_manager)
app.state.auditor = auditor

# 事件订阅（app.py 内 event_bus.on 段）
event_bus.on("execution.completed", auditor.handle_execution_completed)   # 无 priority，最先
# 下游 audit.completed 订阅者（Auditor 不感知，松耦合）：
event_bus.on("audit.completed", notifier.handle_audit_completed)
event_bus.on("audit.completed", memory_manager.handle_audit_completed, priority=200)
event_bus.on("audit.completed", _update_universe_fitness, priority=250)
```

构造只吃三个依赖：`EventBus`（订阅/发事件）、`Storage`（取 edict/memorial、写回）、`ConfigManager`（给 LLMReviewer 拿 LLM 配置）。`RulesEngine`/`LLMReviewer` 在构造函数内直接 new，未做注入——当前无替换需求。

## 3. 运行流程（`handle_execution_completed`）

```text
event.edict_id / memorial_id 缺失 → return（防御）
storage.get_edict / get_memorial → 任一缺失：log error + return
memorial.status==COMPLETED → 翻 AUDITING 并 update_memorial
按 review_policy 决定 audit_result：
  never                                  → AuditResult(verdict="pass", rules_checked=0)（跳过）
  always                                 → await audit()
  on_failure 且 status==FAILED           → await audit()
  on_flag                                → await audit()
  其余                                   → AuditResult(verdict="pass")
memorial.audit = audit_result
落 memorial 状态：
  review_policy=="always" → review_status=pending, status=NEEDS_REVIEW（强制人审）
  verdict=="block"        → status=FAILED, error="Blocked by audit: ..."
  verdict=="flag"         → review_status=pending, status=NEEDS_REVIEW
  else                    → review_status=not_required, status=COMPLETED
update_memorial
自动收口 edict：status==COMPLETED 且 not_required 且 edict OPEN
  且 schedule.type 不在 (cron, interval) → edict.status=COMPLETED
emit audit.completed { verdict, reasons }
```

`audit(edict, memorial)` 内部：先 `RulesEngine.check`；当 `verdict=="flag"` 且 `review_policy!="never"` 才 `await LLMReviewer.review`。即 LLM 调用被快规则 + 策略双重门控。

## 4. 三态裁决落点速查

| 入口 verdict | review_policy | 最终 memorial.status | review_status |
|---|---|---|---|
| pass | 非 always | COMPLETED | not_required |
| 任意 | always | NEEDS_REVIEW | pending |
| block | 非 always | FAILED | —（error 写入） |
| flag | 非 always | NEEDS_REVIEW | pending |

## 5. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 加一条静态规则 | 在 `RulesEngine.check` 里 `rules_checked += 1` 并按条件 append `reasons`；注意当前裁决逻辑「有 reason 即 flag」，若要新规则直接 block 需改 verdict 判定分支 |
| 让规则能直接 block | `RulesEngine` 现仅产 pass/flag；要支持规则级 block，需在 `check` 末尾对特定严重 reason 返回 `verdict="block"`（block 当前只来自 `LLMReviewer`） |
| 换复审 prompt / 模型参数 | 改 `reviewer.py` 的 `_REVIEW_PROMPT` 或 `LLMClient(...)` 入参（temperature/max_tokens/max_retries） |
| 新增 review_policy 分档 | 同步改两处：`models/edict.py` 的 `review_policy` Literal，和 `auditor.py` `handle_execution_completed` 的策略分支 |
| 新增 audit.completed 下游 | 在 `app.py` `event_bus.on("audit.completed", ...)` 追加订阅者，无需改 Auditor；payload 仅 `{verdict, reasons}`，需更多字段则扩 `make_event` 的 payload |
| 替换规则引擎/复审器实现 | 当前在 `Auditor.__init__` 内硬 new；若要 fake 注入测试，把 `RulesEngine`/`LLMReviewer` 改为构造参数 |

注意：本子系统与 `executor/orchestrator/audit.py` 的 completion audit 是两套独立代码，扩展时别改错文件——后者判 acceptance gap、发 `edict.audit.executed`，与这里无调用关系（区别见 design 篇 §5）。
