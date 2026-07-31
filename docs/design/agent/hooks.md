# Hook 生命周期

## 1. 设计意图

Agent 主循环不应硬编码记账、审批、记忆、画像这些横切逻辑。`HookRegistry` 在 Agent 生命周期的固定钩点暴露扩展位，治理与横切能力以「注册 handler」的方式接入。设计目标：**主循环只跑 ReAct，治理通过钩点组合**，并对安全关键钩点做 fail-secure，避免超时/异常导致工具绕过审批。

## 2. HookType 钩点种类

`HookType` 共 10 种：

| 钩点 | 主要注册者 | 作用 |
|---|---|---|
| `session_start` | 预留/插件 | 会话开始 |
| `before_agent_start` | MemoryManager | 注入记忆 history（recall） |
| `before_iteration` | CostManager | 每轮预算检查 |
| `llm_input` | 预留/插件 | 修改 LLM 输入 |
| `llm_output` | CostManager | 记账 |
| `before_tool_call` | PolicyHook(priority=5) + ApprovalManager(priority=10) | 工具治理与审批 |
| `after_tool_call` | 预留/插件 | 工具结果处理 |
| `before_compaction` | 预留/插件 | 压缩前 |
| `agent_end` | MemoryManager / SkillReviewHandler / ProfileTrigger | 写记忆、画像合成触发；SkillReviewHandler 默认关闭且在 LLM 前 fail fast，不直接写 live Skill |
| `session_end` | 预留/插件 | 会话结束 |

## 3. priority 与 HookResult 契约

- **注册**：`register(hook_type, handler, priority=100)`，priority **小者先执行**（`entries.sort`）。
- **返回**：`HookResult(block, reason, modified_args)`。`block=True` 时 `run()` 立即返回该结果、不再执行后续 handler；`modified_args` 可改写参数（如审批后的工具入参）。
- **首个阻断即终止**：多 handler 链中任一返回 block 即短路，对应 `ExitReason.HOOK_BLOCKED`。

`before_tool_call` 的执行顺序由 priority 体现：PolicyHook(5) 先于 ApprovalManager(10)——先判 policy 规则，命中需审批再转人工。

## 4. 超时与 fail-secure

- 默认 `HOOK_TIMEOUT=5.0s` 硬超时。
- `before_tool_call` 特殊放宽到 `310s`（`HOOK_TIMEOUTS`），因 PolicyHook 的
  `wait_for_tool_decision` 默认最多等 300s。
- **fail-secure**：`_FAIL_SECURE_HOOKS = {BEFORE_TOOL_CALL}`。安全关键钩点超时或抛异常时**返回 block**（拦住工具），避免 fail-open 让工具绕过审批/policy；其余钩点超时仅记 warning 后继续。

## 5. 与 PolicyHook / ApprovalManager 的关系

| 组件 | 钩点 | priority | 行为 |
|---|---|---|---|
| `PolicyHook` | before_tool_call | 5 | 读 `PolicyEngine` 规则 + `SessionRuleStore` 会话级 allow/deny；命中禁止→block；命中需审批→转 ApprovalManager |
| `ApprovalManager` | before_tool_call | 10 | 兼容 no-op；持久化 Decision 的请求与等待由 `PolicyHook` 直接调用其服务接口 |

二者共同把「工具能不能跑」从硬编码变成可配置、可审批、可审计的治理链。

## 6. 审计可见性

`set_event_writer(storage)` 注入存储后，每次 hook 执行写入 `hook.{type}` 事件（含 handler 名、是否 blocked、error），供前端 hook 触发记录展示。事件只在 `edict_id` 可解析时写入。

**相关实现**：[../../impl/agent/](../../impl/agent/)
