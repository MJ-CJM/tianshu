# 会诊（consultation）实现现状

**相关设计**：[../../design/consultation/README.md](../../design/consultation/README.md)

> 代码位于 `src/tianshu/consultation/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图与「会诊 vs DAG」的区分见 design 篇。

## 1. 模块清单（`src/tianshu/consultation/`）

| 文件 | 关键类 / 方法 | 职责 |
|---|---|---|
| `models.py` | `ConsultationRequest` / `PersonaOpinion` / `ConsultationResponse` / `ConsultationResult` | Pydantic v2 数据契约。`Response.id` 用 ULID，`status` ∈ pending/running/completed/failed |
| `session.py` | `ConsultationSession`（`create_pending` / `run` / `start` / `get` / `list_recent` / `mark_failed` / `_get_opinion`） | 编排：fan-out 并行收集 opinion → 调 Synthesizer 汇聚 → 落 court 记忆。状态经 `storage.save_consultation` 落 `consultations` 表；未注入 storage 时（单测）退回进程内 `_sessions` dict |
| `synthesizer.py` | `Synthesizer.synthesize` | 第二次 LLM 调用：把 opinions 拼进 prompt，解析出 `{synthesis, decision}` |
| `__init__.py` | 重导出上述模型与 `ConsultationSession` | 包入口 |

## 2. 运行流程（`create_pending` + `run`，`start` 是二者串联）

```text
create_pending(request)
  建 ConsultationResponse(status="pending") 并落库 → 返回 id（HTTP 面据此立即 202）

run(consultation_id)
  1. 从库读回记录，status="running" 落库 + 广播 consultation.started
  2. persona_ids 为空 → persona_loader.load_all() 取全部人格
  3. 每位人格一个 asyncio.wait_for(_get_opinion(...), timeout=opinion_timeout)
  4. asyncio.as_completed 逐条收敛：每到一条即落库 + 广播 consultation.opinion
       单人超时/异常 → 收敛成归因文本进 failures，不拖垮整场
  5. opinions 为空 → status="failed" + error=failures（不再假装 completed）
     否则 Synthesizer.synthesize（同样有 timeout）→ 回填 synthesis / decision
       部分失败时 failures 仍写入 error 字段供前端提示
  6. status 落库 + 广播 consultation.finished
  7. completed 且有 memory_manager/synthesis → 落 court（见 §3）
  整段 try/except：任何异常 → status="failed" + error 归因，吞掉不抛
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
| HTTP API | `gateway/api.py`：`POST /consultations`（202，先落库再 `_spawn_consultation` 后台跑）、`GET /consultations/{id}`、`GET /consultations?status=&limit=&offset=` | 前端按 URL 里的 id 轮询 + WS 增量；后台 task 存 `app.state.consultation_tasks` 防 GC，done callback 把逃逸异常落 failed |
| 长任务 L2 | `executor/orchestrator/loop.py`：`_run_consultation` 在升级到 L2 时调 `consultation_session.start`，取 `resp.synthesis` 作建议；异常则降级 L3 | persona 集来自 `acceptance.escalation.l2_consultation_personas` |

L2 集成与降级语义详见 [../../design/agent/orchestrator.md](../../design/agent/orchestrator.md)。

## 5. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 回填 key_points | 解析 LLM 输出的编号要点列表填入 `key_points`（当前恒为 `[]`）；可在 `_split_marked_sections` 的标记集合里加 `KEY_POINTS` |
| 换汇聚策略 | 改 `Synthesizer.synthesize` 的 prompt / 解析（当前靠 `### Synthesis` / `### Decision` 文本切分）。汇聚人格已接线：请求侧传 `synthesizer_persona_id`，session 解析后传入并回填 `response.synthesizer_*` 供前端署名 |
| 取消进行中的会诊 | 现有 `app.state.consultation_tasks` 已持有 task 引用，加一个 `DELETE /consultations/{id}` 取消并 `mark_failed` 即可 |
| 调整参与人格 | 请求侧传 `persona_ids`；L2 侧改 `acceptance.escalation.l2_consultation_personas` |
