# 会诊（consultation）实现现状

**相关设计**：[../../design/consultation/README.md](../../design/consultation/README.md)

> 代码位于 `src/tianshu/consultation/`。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，设计意图与「会诊 vs DAG」的区分见 design 篇。

## 1. 模块清单（`src/tianshu/consultation/`）

| 文件 | 关键类 / 方法 | 职责 |
|---|---|---|
| `models.py` | `ConsultationRequest` / `PersonaOpinion` / `ConsultationResponse` / `ConsultationResult` | Pydantic v2 数据契约。`Response.id` 用 ULID，`status` ∈ pending/running/completed/failed |
| `session.py` | `ConsultationSession`（`start` / `get` / `_get_opinion`） | 编排：fan-out 并行收集 opinion → 调 Synthesizer 汇聚 → 落 court 记忆。`_sessions` dict 缓存进行中/已完成的会诊 |
| `synthesizer.py` | `Synthesizer.synthesize` | 第二次 LLM 调用：把 opinions 拼进 prompt，解析出 `{synthesis, decision}` |
| `__init__.py` | 重导出上述模型与 `ConsultationSession` | 包入口 |

## 2. 运行流程（`ConsultationSession.start`）

```text
start(request)
  1. 建 ConsultationResponse(status="running") 存入 _sessions[response.id]
  2. persona_ids 为空 → persona_loader.load_all() 取全部人格
  3. for pid: persona_loader.get(pid) → _get_opinion(persona, request) 入 tasks
  4. asyncio.gather(*tasks, return_exceptions=True)
       → 只保留 isinstance(o, PersonaOpinion) 的结果（异常被滤除）
  5. 有 opinion → Synthesizer.synthesize → 回填 response.synthesis / decision
  6. status="completed"，completed_at=now
  7. 有 memory_manager 且有 synthesis → 落 court（见 §3）
  整段 try/except：任何异常 → status="failed"，吞掉不抛
```

- `_get_opinion`：按 persona 渲染 prompt（name/department + topic + context），单次 `llm.chat(messages)` → 构造 `PersonaOpinion`。
- **占位字段**：`confidence=0.8` 硬编码、`key_points=[]` 恒空——LLM 自报的置信度与要点**未被解析回填**。这是当前实现的真实状态，扩展时优先补这里（见 §5）。
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
| 应用装配 | `app.py` lifespan：`ConsultationSession(persona_loader, config_manager, provider_manager, memory_manager)` → `app.state.consultation` | 同一实例又作 `consultation_session=` 注入 `OrchestratorContext` |
| HTTP API | `gateway/api.py`：`POST /consultations`（`create_consultation`，202 + asyncio.create_task 后台跑）、`GET /consultations/{id}`（`get_consultation`） | 前端通过 `app.state.consultation._sessions` 轮询结果 |
| 长任务 L2 | `executor/orchestrator/loop.py`：`_run_consultation` 在升级到 L2 时调 `consultation_session.start`，取 `resp.synthesis` 作建议；异常则降级 L3 | persona 集来自 `acceptance.escalation.l2_consultation_personas` |

L2 集成与降级语义详见 [../../design/agent/orchestrator.md](../../design/agent/orchestrator.md)。

## 5. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 让 confidence 真正生效 | 在 `_get_opinion` 里解析 `llm.chat` 返回文本中的置信度数字，回填 `PersonaOpinion.confidence`（替换硬编码 `0.8`），并在 `Synthesizer` prompt 中据此加权 |
| 回填 key_points | 同上，解析 LLM 输出的编号要点列表填入 `key_points`（当前恒为 `[]`） |
| 换汇聚策略 | 改 `Synthesizer.synthesize` 的 prompt / 解析（当前靠 `### Synthesis` / `### Decision` 文本切分）；`synthesizer_persona_id` 字段已预留但 session 尚未据此切换汇聚人格 |
| 持久化会诊历史 | 当前 `_sessions` 仅进程内 dict，重启即丢；需要持久化时落到 `storage` 或 court 记忆之外的专表 |
| 调整参与人格 | 请求侧传 `persona_ids`；L2 侧改 `acceptance.escalation.l2_consultation_personas` |
