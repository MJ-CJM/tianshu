# 会诊（consultation）实现现状

**相关设计**：[../../design/consultation/README.md](../../design/consultation/README.md)

> 代码位于 `src/tianshu/consultation/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图与「会诊 vs DAG」的区分见 design 篇。

## 1. 模块清单（`src/tianshu/consultation/`）

| 文件 | 关键类 / 方法 | 职责 |
|---|---|---|
| `models.py` | `ConsultationRequest` / `RoundRequest` / `PersonaOpinion` / `ConsultationRound` / `ConsultationResponse` / `ConsultationResult` | Pydantic v2 数据契约。id 用 ULID，`status` ∈ pending/running/completed/failed。`ConsultationResponse` 是一场廷议的容器（持言官名单、轮次、用户裁决），`ConsultationRound` 是其中一轮 |
| `session.py` | `ConsultationSession`（`create_pending` / `append_round` / `run` / `run_round` / `start` / `set_verdict` / `get` / `list_recent` / `mark_failed` / `_build_history`） | 编排：按轮 fan-out 收集 opinion → 调 Synthesizer 票拟 → 落 court 记忆。容器落 `consultations` 表、轮次落 `consultation_rounds` 表；未注入 storage 时（单测）退回进程内 `_sessions` dict |
| `synthesizer.py` | `Synthesizer.synthesize` | 每轮末一次 LLM 调用：把本轮 opinions 拼进 prompt，解析出 `{synthesis, decision}`——后者落成轮次的 `proposal`（票拟，仅供参考） |
| `__init__.py` | 重导出上述模型与 `ConsultationSession` | 包入口 |

## 2. 运行流程（多轮；`start` = `create_pending` + `run`）

```text
create_pending(request)
  建容器(status=pending) + 第 0 轮(prompt=topic, participants=persona_ids) 并落库
  → 返回 id（HTTP 面据此立即 202）

append_round(consultation_id, RoundRequest)
  上一轮未收尾则拒绝（"previous round is still in progress" → HTTP 409）
  participant_ids 为空 → 沿用首轮全体名单

run(consultation_id) → 跑最早一个未收尾的轮次；run_round 是单轮执行体：
  1. 轮次 status="running" 落库 + 广播 consultation.started（带 round_id/round_index）
  2. _build_history：把此前已完成轮次回放成文本（含票拟与用户裁决），
       字符预算从最近轮往前累计，超预算即停、至少保留最近一轮
  3. 每位参与者一个 asyncio.wait_for(_get_opinion(...), timeout=opinion_timeout)
       言官身份来自 request.censor_persona_ids（显式任命，不再按列表顺序）
  4. asyncio.as_completed 逐条收敛：每到一条即落库 + 广播 consultation.opinion
       单人超时/异常 → 收敛成归因文本进 failures，不拖垮整轮
  5. opinions 为空 → 轮次 failed + error=failures（不再假装 completed）
     否则 Synthesizer.synthesize（同样有 timeout）→ 回填 synthesis / proposal
  6. 轮次状态落库，容器状态跟随最新轮 + 广播 consultation.finished
  7. completed 且有 memory_manager/synthesis → 落 court（见 §3）
  整段 try/except：任何异常 → 轮次 failed + error 归因，吞掉不抛

set_verdict(consultation_id, verdict)
  用户裁决落容器；LLM 只出票拟，裁决权不外包（issue #55）
```

超时上限见 `DEFAULT_OPINION_TIMEOUT_SECONDS` / `DEFAULT_SYNTHESIS_TIMEOUT_SECONDS`（各 180s，构造参数可覆盖）。

- `_get_opinion`：按 persona 渲染 prompt（name/department + topic + context），单次 `llm.chat(messages)` → 构造 `PersonaOpinion`。
- **意见解析**（`_parse_opinion` + `_split_marked_sections`）：按 `STANCE:` / `CONDITIONS:` / `OPINION:` 三个标记分段，每段内容延续到下一个标记为止，因此正文换行、跨段都不会丢（issue #54 修复；旧实现只取 `OPINION:` 冒号后同一行，LLM 一换行整段正文就变空串）。完全不按格式输出时回落「全文即意见」。
- **占位字段**：`key_points=[]` 恒空——LLM 输出中的要点未被解析回填。`confidence` 已按 ADR-0008 废除，换成 `stance` + `conditions`。
- LLM client 解析顺序（session 与 synthesizer 一致）：`provider_manager.get_client()` 优先，缺失则按 `config_manager.state` 现起 `LLMClient(model, api_key, api_base)`。

## 3. 结果落盘（court Markdown + 写穿索引）

`start` 末尾（仅当注入了 `memory_manager` 且有 `synthesis`）：

| 步骤 | 调用 | 落点 |
|---|---|---|
| 写公共洞见 | `memory_manager.store(MemoryEntry(persona_id="court", category="insight", access_level="court"))` | Markdown（权威）+ write-through 索引 |
| 追加决策摘要 | `md_backend.read_core_memory("court")` → 拼 `## Consultation (date)` 段 → `write_core_memory("court", ...)` | `court/MEMORY.md` |

失败仅 `logger.debug("Failed to store consultation result to memory")`，不影响返回值。

## 4. 装配与调用方

| 调用方 | 位置 | 说明 |
|---|---|---|
| 应用装配 | `bootstrap/wiring_scheduler.py::wire_consultation`：`ConsultationSession(..., storage=, notifier=)` → `app.state.consultation`；同时建 `app.state.consultation_tasks` 并把上次进程遗留的 running/pending 判死 | 同一实例又作 `consultation_session=` 注入 `OrchestratorContext` |
| HTTP API | `gateway/api.py`：`POST /consultations`（202）、`POST /consultations/{id}/rounds`（202，追问）、`PUT /consultations/{id}/verdict`（落裁决）、`GET /consultations/{id}`（含 rounds[]）、`GET /consultations?status=&limit=&offset=`（列表不 join 轮次） | 前端按 URL 里的 id 轮询 + WS 增量；后台 task 存 `app.state.consultation_tasks` 防 GC，done callback 把逃逸异常落 failed |
| 长任务 L2 | `executor/orchestrator/loop.py`：`_run_consultation` 在升级到 L2 时调 `consultation_session.start`，取 `resp.synthesis` 作建议；异常则降级 L3 | persona 集来自 `acceptance.escalation.l2_consultation_personas`。`ConsultationResponse.opinions/synthesis/proposal` 是代理最新一轮的只读属性，L2 因此零改动——注意它们不可写 |

L2 集成与降级语义详见 [../../design/agent/orchestrator.md](../../design/agent/orchestrator.md)。

## 5. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 回填 key_points | 解析 LLM 输出的编号要点列表填入 `key_points`（当前恒为 `[]`）；可在 `_split_marked_sections` 的标记集合里加 `KEY_POINTS` |
| 换汇聚策略 | 改 `Synthesizer.synthesize` 的 prompt / 解析（当前靠 `### Synthesis` / `### Decision` 文本切分）。汇聚人格已接线：请求侧传 `synthesizer_persona_id`，session 解析后传入并回填 `response.synthesizer_*` 供前端署名 |
| 取消进行中的会诊 | 现有 `app.state.consultation_tasks` 已持有 task 引用，加一个 `DELETE /consultations/{id}` 取消并 `mark_failed` 即可 |
| 重试某一轮 | 轮次已是独立实体（`consultation_rounds`），把该轮置回 pending 再 `run_round` 即可 |
| 调整参与人格 | 请求侧传 `persona_ids`；L2 侧改 `acceptance.escalation.l2_consultation_personas` |
