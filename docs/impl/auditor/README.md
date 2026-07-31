# 审计子系统（Auditor）实现现状

**相关设计**：[../../design/auditor/README.md](../../design/auditor/README.md)

> 代码位于 `src/tianshu/auditor/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图（触发时机、三态裁决、与 outer loop completion audit 的区别）见 design 篇。

## 1. 模块清单（`src/tianshu/auditor/`）

| 文件 | 关键类 | 职责 |
|---|---|---|
| `auditor.py` | `Auditor` | 订阅 completed/failed，保留执行终态并发 `audit.completed` |
| `rules.py` | `RulesEngine` | 读取 `AuditRulesConfig` 的开关和 risk keywords |
| `reviewer.py` | `LLMReviewer` | 使用配置的 temperature/max tokens，并上报 LLM usage context |
| `rules_config.py` | `AuditRulesConfig` | 静态规则、风险关键词与复审参数 |
| `__init__.py` | — | 仅 re-export `Auditor` |

数据契约 `AuditResult`（`verdict / reasons / rules_checked / llm_reviewed`）定义在 `models/common.py`，**不在本包内**。

## 2. 装配（`app.py` lifespan）

```text
Auditor(event_bus, storage, config_manager, rules_config)
  ├─ RulesEngine(rules_config)
  └─ LLMReviewer(config_manager, rules_config)
app.state.auditor = auditor

# 事件订阅（app.py 内 event_bus.on 段）
event_bus.on("execution.completed", auditor.handle_execution_completed)   # 无 priority，最先
event_bus.on("execution.failed", auditor.handle_execution_failed)
# 下游 audit.completed 订阅者（Auditor 不感知，松耦合）：
event_bus.on("audit.completed", notifier.handle_audit_completed)
event_bus.on("audit.completed", memory_manager.handle_audit_completed, priority=200)
event_bus.on("audit.completed", _update_universe_fitness, priority=250)
```

构造只吃三个依赖：`EventBus`（订阅/发事件）、`Storage`（取 edict/memorial、写回）、`ConfigManager`（给 LLMReviewer 拿 LLM 配置）。`RulesEngine`/`LLMReviewer` 在构造函数内直接 new，未做注入——当前无替换需求。

## 3. 运行流程（成功与失败共用）

```text
event.edict_id / memorial_id 缺失 → return（防御）
storage.get_edict / get_memorial → 任一缺失：log error + return
成功 memorial → 翻 AUDITING；失败 memorial 保持 FAILED
按 review_policy 决定 audit_result：
  never                                  → AuditResult(verdict="pass", rules_checked=0)（跳过）
  always                                 → await audit()
  on_failure 且 execution_failed         → await audit()
  on_flag                                → await audit()
  其余                                   → AuditResult(verdict="pass")
memorial.audit = audit_result
落 memorial 状态：
  review_policy=="always" → review_status=pending, status=NEEDS_REVIEW（强制人审）
  verdict=="block"        → status=FAILED, error="Blocked by audit: ..."
  verdict=="flag"         → review_status=pending, status=NEEDS_REVIEW
  else                    → review_status=not_required，保持原 execution terminal
update_memorial
只在执行成功时自动收口 edict：status==COMPLETED 且 not_required 且 edict OPEN
  且 schedule.type 不在 (cron, interval) → edict.status=COMPLETED
emit audit.completed { verdict, reasons }
```

`audit(edict, memorial)` 内部：先 `RulesEngine.check`；当 `verdict=="flag"` 且
`review_policy!="never"` 才 `await LLMReviewer.review`。审计结果带
`execution_failed=true` 时，LLM pass 也不会把执行失败洗成 COMPLETED。

## 4. 三态裁决落点速查

| 入口 verdict | review_policy | 最终 memorial.status | review_status |
|---|---|---|---|
| pass | 非 always | COMPLETED | not_required |
| 任意 | always | NEEDS_REVIEW | pending |
| block | 非 always | FAILED | —（error 写入） |
| flag | 非 always | NEEDS_REVIEW | pending |

上表的 pass 行只适用于原执行成功。原执行失败且无需人工 review 时仍为 FAILED。

## 5. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 加一条静态规则 | 在 `RulesEngine.check` 里 `rules_checked += 1` 并按条件 append `reasons`；注意当前裁决逻辑「有 reason 即 flag」，若要新规则直接 block 需改 verdict 判定分支 |
| 让规则能直接 block | `RulesEngine` 现仅产 pass/flag；要支持规则级 block，需在 `check` 末尾对特定严重 reason 返回 `verdict="block"`（block 当前只来自 `LLMReviewer`） |
| 换复审参数 | 修改 `AuditRulesConfig`；`GET /api/audit/rules` 返回实际生效开关 |
| 新增 review_policy 分档 | 同步改两处：`models/edict.py` 的 `review_policy` Literal，和 `auditor.py` `handle_execution_completed` 的策略分支 |
| 新增 audit.completed 下游 | 在 `app.py` `event_bus.on("audit.completed", ...)` 追加订阅者，无需改 Auditor；payload 仅 `{verdict, reasons}`，需更多字段则扩 `make_event` 的 payload |
| 替换规则引擎/复审器实现 | 当前在 `Auditor.__init__` 内硬 new；若要 fake 注入测试，把 `RulesEngine`/`LLMReviewer` 改为构造参数 |

注意：本子系统与 `executor/orchestrator/audit.py` 的 completion audit 是两套独立代码，扩展时别改错文件——后者判 acceptance gap、发 `edict.audit.executed`，与这里无调用关系（区别见 design 篇 §5）。
