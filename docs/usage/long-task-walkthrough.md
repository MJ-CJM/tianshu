# 长任务端到端走查

一篇贯穿全程的实操手册：带验收契约下诏 → 看 L1/L2/L3 跳变 → L3 人工审批 → 读监督报告 → 失败重跑。机制与设计意图见 [../design/agent/orchestrator.md](../design/agent/orchestrator.md)，通用流程见 [user-guide.md](user-guide.md)。

**相关实现**：[../impl/agent/](../impl/agent/)

> 触发条件：只要 `Edict.acceptance` 非空，执行就从普通 ReAct 切换到 orchestrator outer loop。不配 `acceptance` 的诏令走普通单轮路径，本篇不适用。
> 入口契约见 `AcceptanceCriteria`（`src/tianshu/models/acceptance.py`），HTTP 路由在 `gateway/api.py`。

## 1. 带 acceptance_criteria 下诏

走 `POST /api/edicts`（`create_edict`），在请求体 `acceptance` 字段塞入验收契约。注意：CLI `tianshu edict submit` 只接受 `--goal/--context/--priority`，**不带** acceptance，要配验收契约请直接走 HTTP（curl / Web）。

curl 示例（最小可跑，含一条 bash check + 一个监督官 + 升级策略）：

```bash
curl -X POST http://localhost:8000/api/edicts \
  -H 'Content-Type: application/json' \
  -d '{
    "goal": "把 docs/draft.md 改写成一篇可发布的技术博客，保留全部代码示例",
    "title": "博客改写",
    "execution_profile": "checkpointed",
    "acceptance": {
      "min_outer_iterations": 2,
      "max_outer_iterations": 5,
      "deadline_seconds": 7200,
      "on_exhaustion": "escalate",
      "on_critic_unavailable": "skip",
      "on_approval_timeout": "best_effort",
      "checks": [
        {"kind": "bash", "name": "markdown_lint", "command": "markdownlint docs/draft.md", "timeout_seconds": 60},
        {"kind": "rubric", "name": "覆盖度", "rubric": "正文是否完整保留了原文每个代码块", "pass_threshold": 0.8, "weight": 1.0}
      ],
      "critic": {
        "persona_ids": ["editor-in-chief"],
        "strictness": "balanced",
        "same_issue_threshold": 2
      },
      "escalation": {
        "enabled_levels": ["L1", "L2", "L3"],
        "l1_max_rounds": 2,
        "l2_max_rounds": 1,
        "l1_thinking_budget": 8000,
        "l2_consultation_personas": ["senior-editor", "fact-checker"]
      }
    }
  }'
```

返回 `202` + edict 数据（`ApiResponse`，`data.id` 即 edict_id）。`create_edict` 只在 `body.acceptance is not None` 时把它挂到 `Edict`，所以不传该字段就是普通诏令。

| 入口 | 怎么带验收契约 |
|---|---|
| **HTTP / curl** | `POST /api/edicts`，body 加 `acceptance` 对象（上例） |
| **Web UI** | 新建 Edict 表单里展开「验收契约」，填 checks / critic / 升级；前端类型见 `web/src/api/types.ts` 的 `AcceptanceCriteria` |
| **追加指令** | `POST /api/edicts/{id}/follow-up` 带 `acceptance_override`，对单次 follow-up 覆盖验收契约 |

## 2. 配 CriticSpec 与 CheckSpec

两层验收，各管一段（字段以 `src/tianshu/models/acceptance.py` 为准）：

| 对象 | 字段 | 要点 |
|---|---|---|
| `CheckSpec` | `kind`(bash/lint/rubric)、`name`、`command`、`rubric`、`pass_threshold`(默认0.8)、`weight`、`timeout_seconds`(默认60) | bash/lint 必填 `command`，跑子进程看 returncode；rubric 必填 `rubric`，调 LLM 评分对比 `pass_threshold` |
| `CriticSpec` | `persona_ids`、`model`、`same_issue_threshold`(默认2)、`strictness`(lenient/balanced/strict，默认 lenient) | 多个 `persona_ids` 并发监督；`strictness` 决定通过门槛（lenient=合格即过 / balanced=高标准 / strict=优秀才过） |

关键顺序：**checks 全过才进 critic**。任一 check 不通过，该轮直接判 fail（`issue_class=checks_failed`），critic 一次都不调。`command` not found 等配置错会抛 `ChecksConfigError`，整个 outer loop abort。

兼容字段：`CriticSpec.persona_id`（单数）是旧版字段，`effective_persona_ids()` 在 `persona_ids` 为空时回退到 `[persona_id]`。新配置统一用 `persona_ids`。

## 3. 实时看 L1/L2/L3 跳变

订阅 WebSocket `/api/ws`（`websocket_endpoint`），每条消息封装为 `{type, edict_id, memorial_id, payload}`（`Notifier.handle_outer_loop_event`，不走 debounce，实时推）。按 `edict_id` 过滤即得本任务的 outer loop 时间线。

事件名（全集见 `app.py` 订阅列表，发射点在 `executor/orchestrator/loop.py`）：

| 事件 `type` | 含义 / 关键 payload |
|---|---|
| `outer_loop.started` | 进入外循环，`payload.max_outer` |
| `outer_loop.iteration.started` | 新一轮开始 |
| `outer_loop.checks.failed` | 本轮 checks 不过，跳过 critic |
| `outer_loop.iteration.finished` | 本轮结束（含 verdict / issue_class） |
| `outer_loop.escalated` | **升级跳变**，payload 里有目标 level（L1/L2/L3） |
| `outer_loop.continued_for_optimization` | `min_outer_iterations≥2` 时 critic 已 pass 仍强制续轮 |
| `outer_loop.approval.requested` | 升到 L3，进入等人审批（见第 4 节） |
| `outer_loop.approval.received` | 人工决策已提交，`payload.action` |
| `outer_loop.completed` / `outer_loop.exhausted` | 终态：达标 / 预算耗尽 |
| `outer_loop.paused` / `outer_loop.resumed` | 暂停 / 续跑（checkpoint） |
| `outer_loop.supervision_completed` | 监督报告已生成（见第 5 节） |

升级是纯函数 FSM（`decide_escalation`）：L0 基线注入 critic feedback 重试；同类问题 streak 达 `same_issue_threshold` 才升 L1（加 thinking budget / 可换模型）；再不行升 L2 触发多 persona 会诊；仍不行升 L3 请人。所以监控时盯 `outer_loop.escalated` 的 level 即可看清「机器自救 → 求助人」的跳变。

CLI 旁观：`tianshu watch <edict_id>` 也连 `/api/ws`，但它的状态表只识别 `execution.*` 终态事件、把 outer_loop.* 当普通事件逐条打印；要细看升级跳变直接订阅 WebSocket 或看 Web UI 更清楚。落库后的迭代记录可用 `GET /api/edicts/{id}/iterations` 回看。

## 4. L3 暂停 → 审阅 → 批准

升到 L3 时，loop 发 `outer_loop.approval.requested` 并**阻塞等待**人工决策（`_escalate_to_human` → `ApprovalManager.wait_for_outer_loop_decision`，默认等 `deadline_seconds` 或 24h）。

**审阅**：拉待审清单 `GET /api/edicts/outer-loop/pending`（`list_outer_loop_pending` → `list_pending_outer_loop`），每项含 approval payload：`edict_id / iteration / level / best_output（当前最佳产出）/ critic_feedback（监督官意见）/ history_length`。

**批准**：`POST /api/edicts/{edict_id}/outer-loop/decide`（`submit_outer_loop_decision_api`），body 是 `OuterLoopDecisionRequest` → `HumanDecision`：

| `action` | 行为 |
|---|---|
| `continue` | 继续迭代（可带 `feedback` 注入给 actor） |
| `accept_as_is` | 接受当前 `best_output` 为终态，成功结案 |
| `abort` | 终止，判失败 |
| `modify_acceptance` | 改验收契约后继续——必须带 `new_acceptance`（完整 `AcceptanceCriteria` JSON），后端 `AcceptanceCriteria.model_validate` 校验 |

curl 示例（接受当前产出）：

```bash
curl -X POST http://localhost:8000/api/edicts/<edict_id>/outer-loop/decide \
  -H 'Content-Type: application/json' \
  -d '{"action": "accept_as_is"}'
```

放宽验收后续跑：

```bash
curl -X POST http://localhost:8000/api/edicts/<edict_id>/outer-loop/decide \
  -H 'Content-Type: application/json' \
  -d '{"action": "modify_acceptance", "feedback": "代码块覆盖度降到 0.7 即可",
       "new_acceptance": { "max_outer_iterations": 3,
         "checks": [{"kind": "rubric", "name": "覆盖度", "rubric": "代码块是否大致保留", "pass_threshold": 0.7}],
         "critic": {"persona_ids": ["editor-in-chief"], "strictness": "lenient"} }}'
```

提交后 loop 唤醒、发 `outer_loop.approval.received`。若没有 edict 在等审批，decide 返回 `404`。审批超时按 `on_approval_timeout`：`best_effort` 等价于 `accept_as_is`，`fail` 等价于 `abort`。

Web UI：御书房 / 审批队列页消费 `outer-loop/pending` 渲染审批卡片，弹出 L3 Modal 提交上述四种决策。

## 5. 读监督报告

终态（completed / exhausted / failed）后，若配了 critic persona，每个监督官各生成一份 4 章节复盘（`generate_supervision_report`，`executor/orchestrator/supervision.py`），并发 `outer_loop.supervision_completed`。

拉取：`GET /api/edicts/{edict_id}/supervision-reports`（`get_supervision_reports`，返列表，多监督官各一份）。报告 JSON 字段：

| 字段 | 含义 |
|---|---|
| `issues_observed` | 全程观察到的问题（具体到哪几轮） |
| `well_done` | actor 做得好的地方 |
| `poorly_done` | actor 做得不够的地方 |
| `recommendation` | 给后续类似任务的可操作建议 |

监督官 LLM 调用失败时仍返回报告（4 章节空 + `raw_feedback` 兜底）。旧的单报告端点 `GET /api/edicts/{id}/supervision-report` 已废弃，统一用复数版。

## 6. 失败怎么重跑

先判定终态原因：看 `outer_loop.exhausted`（预算/迭代耗尽）还是 L3 `abort`，再读监督报告的 `poorly_done` / `recommendation` 定位症结。

| 重跑方式 | 怎么做 | 适用 |
|---|---|---|
| **同诏追加指令** | `POST /api/edicts/{id}/follow-up`，带 `instruction` + 可选 `acceptance_override` | edict 仍 `OPEN` 且无在跑奏折；想在原诏令上下文里带新验收标准再来一轮 |
| **L3 现场救** | 任务卡在 L3 时直接 `modify_acceptance` 放宽契约续跑（第 4 节） | 不想重头跑，只是验收过严 |
| **重下新诏** | 重新 `POST /api/edicts`，按监督建议调低 `pass_threshold` / `strictness` 或加大 `max_outer_iterations` / `deadline_seconds` | 原诏已结案，或要换验收策略 |
| **断点续跑** | `execution_profile` 为 `checkpointed`/`background` 时保存 checkpoint，中断后由受控触发续跑（发 `outer_loop.resumed`） | 长耗时任务、想避免重头算 |

调参速查：

- 总在 `outer_loop.exhausted` → 调大 `max_outer_iterations`，或 `on_exhaustion` 改 `best_effort`（耗尽时把当前最佳产出判成功）。
- critic 反复挑同一类问题 → 看是否该升级，或降 `strictness` / 调高 `same_issue_threshold`。
- 经常超时停在 L3 → 加 `deadline_seconds`，或把 `on_approval_timeout` 设 `best_effort` 让无人值守时自动收尾。
