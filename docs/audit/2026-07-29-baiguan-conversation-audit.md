# 百官对话链路审计（2026-07-29）

> 触发：统一模型注册表改造后，对「对话回放 / memory / 上下文压缩」做三路并行审计。
> 结论：**主链路可用但审计前不健康**——回放存在无上限增长与污染源，压缩存在
> 死循环缺陷，记忆存在归因错误。以下按处置状态列出。

## 已修复（随本次提交）

| # | 问题 | 修复 |
|---|------|------|
| 1 | 对话回放无上限：cron 周期敕令每次触发全量回放历次输出（成本线性增长直至撑爆窗口） | cron/interval 敕令不回放（executor.py 接线处）；回放加 20 轮 / 60K 字符双预算（conversation.py，从最近轮往前取） |
| 2 | DAG 子节点奏折被当用户对话轮回放；失败/重试奏折造成悬空 user 与同指令重复投喂 | 只回放 COMPLETED 且 dag_node_id 为空的根奏折 |
| 3 | 回放 assistant 带 reasoning_content：DeepSeek 官方约束输入含该字段 400，严格 openai-compat 端点拒收未知字段 | 回放一律纯文本 content |
| 4 | memory 召回插在对话历史之后：形成连续多条 user 消息（严格交替端点 400），陈旧记忆排得比上轮真实答复更近 | 注入顺序改为 memory_history + 对话历史（executor.py） |
| 5 | AGENT_END 记忆写错 persona（写 bingbu，执行官召不回自己写的记忆——实例上 116 条 observation 已实证归因错误） | `_resolve_persona_id` 以 memorial.persona_id 为最高优先，读写同源（memory/manager.py） |
| 6 | 消息 ≤8 条但超大体积时 reactive_compact 空操作 + iteration 不推进 → overflow 死循环烧 API 直到超时、误报 provider_timeout | reactive_compact 消息未变时返回 None；agent 对 overflow 恢复设 2 次硬上限后 CONTEXT_OVERFLOW 收工 |
| 7 | context_limit 硬编码 128000，目录里 90 个模型窗口 <128K（小窗先 400、大窗过早压缩） | LLMClient 携带目录 context_window（ProviderManager 从 catalog 注入），agent 压缩阈值按真实窗口计算，未知回落 128K |

## 待办（按优先级，未处置）

**high**
- auto_compact 固定尾长切分会切断 assistant(tool_calls)→tool 消息组 → 孤儿 tool 消息 400（compaction/auto.py:79；修法：尾部边界吸附到组首）
- 史官蒸馏只写 SQLite 索引，被 sync_index「清空重建」语义静默清掉且无 MD 副本（memory/historian.py:70；修法：改走 MemoryManager.store）

**medium**
- 长任务 outer loop 完全绕过 memory hooks（不注入不写入）；follow-up 带 acceptance 升级 outer loop 时 history/user_content 被静默丢弃（executor.py:994 / orchestrator/loop.py:526）
- 客卿路径 memory_history 被 **_ignored 静默丢弃（归客卿专项一并处理）
- follow-up 每轮 FTS 召回被本敕令历轮摘要自我复读挤占 top-5（manager.py:545；修法：召回排除当前 edict）
- 部门级记忆永远召不回：BEFORE_AGENT_START context 无 persona，department 恒 None（manager.py:543）
- MemoryCompactor/Reflector 生产中无自动触发（reflect_enabled 死配置）；/api/memory/compact 端点不落盘不归档；compact 失败会把 "Compaction failed" 覆盖写进 MEMORY.md 历史摘要
- 记忆压缩输入侧无预算/分批（30 天全量 join 进单 prompt）

**low**
- estimate_tokens 漏算 tool_calls 与 reasoning_content（工具密集会话低估数倍）
- memory 槽位后台调用（史官/起居注/压缩/反思）的花费不进户部账（可用 usage.cost_cny 轻量上报）
- Diarist memory_dir 硬编码 ~/.tianshu/memory，不随 TIANSHU_MEMORY_DIR
- BEFORE_AGENT_START 5s hook 超时会静默吞掉整次记忆注入（大库退化无信号）
- auto_compact 摘要对 tool_calls 不可见 + MAX_CONSECUTIVE_FAILURES 熔断未实现
- MemoryCompactor.guard_context 死代码（预算守卫闲置，与实际注入实现漂移）
- 测试缺口：get_client_for_slot/with_params、execute_edict 回放接线、压缩×回放交互、memory 四件套 provider_manager 分支

## 已验证健康的部分

- wiring 时序（provider → memory → digest）正确；四件套 provider_manager=None 回退完好；demo 档位零网络
- with_params 完整复制 router/定价/前缀/方言/缓存/窗口
- AGENT_END→记忆写入→FTS 索引链路在实例上有实证（116 条，随每次奏折完结增长）
- 单发 native 消息序 [system]+[记忆]+[历史]+[本轮 user] 正确；本轮指令不与历史重复
