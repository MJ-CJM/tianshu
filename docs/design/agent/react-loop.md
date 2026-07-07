# ReAct 主循环

## 1. 设计意图

单任务执行采用经典 ReAct（Reason+Act）循环：LLM 思考→调用工具→观察结果→再思考，直到无工具调用或触发终止条件。设计目标是把「每一轮的状态」做成可快照、可恢复、可解释的不可变对象，使续写、压缩、取消、checkpoint 都成为对状态的纯函数变换，而非对可变字段的小心翼翼修改。

## 2. 循环结构

```text
构建 system prompt (PromptBuilder)
  -> 合并 history / user_content -> LoopState 初始化
  -> while iteration < max_iterations:
       micro_compact（每轮预防性收缩）
       auto_compact if should_auto_compact（接近阈值）
       BEFORE_ITERATION hook（预算检查）
       LLM chat / chat_stream
       LLM_OUTPUT hook（记账）
       if tool_calls:
           BEFORE_TOOL_CALL hook（policy + approval，可 block）
           ToolRegistry.execute
           AFTER_TOOL_CALL hook
           append tool result -> state.next_turn(messages)
       else:
           finish（COMPLETED）或 length 续写
  -> AGENT_END hook -> AgentResult
```

## 3. LoopState：不可变单轮状态

`LoopState` 是 `@dataclass(frozen=True)`，分三类字段，演化只能经方法返回新对象：

| 字段类 | 字段 | 语义 |
|---|---|---|
| 核心 | `messages`(tuple)、`iteration`、`transition_reason` | 当前消息快照与轮次 |
| 每轮 guard | `compact_attempted`、`output_recovery_count` | 本轮内的一次性保护，`next_turn` 重置 |
| 会话累计 | `total_compact_count`、`total_prompt_tokens`、`total_completion_tokens` | 跨轮累加，永不重置 |

状态变换方法（每个都返回新 `LoopState`）：

| 方法 | 用途 | guard / iteration 处理 |
|---|---|---|
| `next_turn(messages)` | 进入下一轮 | 重置 guard，iteration+1 |
| `with_recovery(reason, messages)` | 续写 / micro 救急 | 保留 guard，iteration 不变 |
| `with_compacted(messages)` | auto 压缩后 | 标记 `compact_attempted`，`total_compact_count`+1 |
| `accumulate_usage(prompt, completion)` | 累计 token | 仅累加，其余不变 |

**契约**：调用方拿到的旧 `LoopState` 永远有效——这是 checkpoint 恢复与压缩失败回退的基础。

## 4. ExitReason：退出原因即分派依据

`ExitReason(StrEnum)` 把终态显式化，每个值对应一种后处理：

| 值 | 含义 |
|---|---|
| `completed` | 模型无工具调用，自然结束 |
| `max_iterations` | 达到 iteration 上限 |
| `context_overflow` | 上下文溢出且 reactive compact 也失败 |
| `timeout` | 执行超时 |
| `cancelled` | 外部 cancel（`CancellationToken`） |
| `hook_blocked` | Hook 返回 `block=True`（policy/审批） |
| `budget_exhausted` | CostManager 熔断 |
| `llm_error` | LLM 调用异常 |
| `output_truncated` | provider 截断输出 |
| `repeated_tool_failure` | 同一工具反复失败 |

## 5. Ambient Context（调用方身份绑定）

工具 handler 经常需要知道「是谁在调用我」——尤其 `memory_write` 要把内容写进**调用方私有池**而非别人的目录。直接让 Agent 把 `persona_id` 当参数透传给每个工具，既污染工具签名，又容易在多轮 / 多节点并发时串号。`kernel/ambient.py` 用 `ContextVar` 把当前 Edict 与 Persona 绑成 ambient 上下文解决这个问题。

**为何用 ContextVar 而非共享字段**：

| 诉求 | ContextVar 如何满足 |
|---|---|
| 工具无需显式传 caller | handler 内 `get_current_persona()` / `get_current_edict()` 直接取，工具签名干净 |
| per-async-task 隔离 | `asyncio` 为每个 Task 拷贝独立 Context，DAG 并发节点各跑各的 `Agent.run`（独立 Task），绑定互不可见，杜绝串号 |
| 异常安全的生命周期 | `bind_*` 是 `@contextmanager`，`set()` 拿 `Token`、`finally` 里 `reset(token)` 精确回退，嵌套绑定也能正确弹栈 |

**绑定生命周期**：`agent.py` 在工具执行处用 `with bind_edict(edict), bind_persona(persona):` 包住 `ToolRegistry.execute`——绑定**只在单次工具调用期间生效**，执行完即 `reset`。`edict` 是本轮 Edict，`persona` 是 `Agent.run` 入参里被指派执行该 Edict 的 AgentPersona（可能为 None）。

**memory_write 如何 scope 写入**：`tools/memory_tools.py` 的 `memory_write` 不接受 `persona_id` 参数，存储路径完全由 `scope` + ambient persona 决定：

| scope | storage_key（目录） | caller 来源 |
|---|---|---|
| `self` | `persona.id` | `get_current_persona()`，None 则报错引导改用 court |
| `department` | `_dept/{persona.department}` | 同上，取 department |
| `court` | `court`（朝廷共享池） | 无需 caller |

即「写哪里」由 ambient 身份锚定，Agent 无法干预（防越权写入他人记忆池）。其他消费者：`schedule_edict` / `hongluisi` 工具用 `get_current_edict()` 拿父 Edict 做关联。

**与 DAG 每节点独立 bind 的交互**：`dag_scheduler.py` 把每个节点包成独立 `asyncio.Task`（`_run_node`）调用各自的 `Agent.run`。因 ContextVar 随 Task 拷贝，节点 A 的工具调用看到的是 A 的 persona，节点 B 看到 B 的——即便两节点并发执行、写各自的 `self` 记忆池也不会互相污染。绑定不向上跨 Task 泄漏，也不依赖任何全局可变状态。

## 6. 关键行为契约

- **空响应兼容**：LLM 返回空 content 但带 `reasoning_content`（DeepSeek-R1 / thinking-mode）时使用推理内容；该字段还会回写 Memorial，供 follow-up 多轮回传，避免上游 400。
- **工具结果截断**：超 `ToolDefinition.max_result_chars` 自动截断（Hermes 风格），防止单次工具输出撑爆上下文。
- **length 续写**：`finish_reason=length` 时，仅在 `state.output_recovery_count < 3` 才自动续写——即**最多续写 3 次**。续写以一条 user 消息「你的输出被截断了……请从中断处直接继续」提示模型衔接，并把 `output_recovery_count + 1`、`iteration` 不变。第 4 次仍为 `length` 则不再续写，直接以 `ExitReason.OUTPUT_TRUNCATED` 收尾。
- **fallback LLM**：配置备用 LLM 时，主模型失败可切换，否则返回 `llm_error`。
- **助手专属工具**：`ASSISTANT_ONLY_TOOLS`（submit_edict / list_edicts / get_edict_status / list_personas）仅「助手 persona」可见，业务执行 persona 即使开了 toggle 也看不到——防递归颁敕。

## 7. StreamCallback：流式输出契约

`StreamCallback` 是 `Protocol`，三个回调：

| 方法 | 触发 |
|---|---|
| `on_delta(text)` | 每个文本 token |
| `on_tool_call_start(name)` | 工具开始执行 |
| `on_tool_call_end(name, result)` | 工具执行完成 |

Agent 收到非空 `stream_callback` 时走 `chat_stream` 并按 delta 回调；实现方 `WebSocketStreamCallback`（notifier 子系统）桥接到前端 WS。`CancellationToken`（同文件）提供线程安全的取消信号，供外部中断。

**相关实现**：[../../impl/agent/](../../impl/agent/)
