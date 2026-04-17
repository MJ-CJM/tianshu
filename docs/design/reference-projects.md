# 天枢参考项目借鉴分析

> 基于 `/Users/chenjiamin/ai-example/` 下 10 个 AI Agent 项目的深入分析，提炼对天枢有借鉴价值的设计与实现。

---

## 一、项目总览与关联度评分

| 项目 | 语言 | 关联度 | 定位 | 梯队 |
|------|------|--------|------|------|
| **NanoBot** | Python | ★★★★★ | 轻量级个人 AI 助手框架 | 第一梯队 |
| **DeepAgents** | Python (LangChain) | ★★★★★ | Sub-agent 多智能体框架 | 第一梯队 |
| **CoPaw** | Python (FastAPI) | ★★★★★ | 生产级个人 AI 助手平台 (~48K 行) | 第一梯队 |
| **PicoClaw** | Go | ★★★★★ | 超轻量 AI 助手 (<10MB RAM) | 第二梯队 |
| **ZeroClaw** | Rust | ★★★★★ | 极简 AI 助手运行时 (<5MB RAM) | 第二梯队 |
| **Pi-Mono** | TypeScript | ★★★★☆ | Agent 框架 monorepo | 第二梯队 |
| **OpenClaw** | TypeScript | ★★★★☆ | 生产级个人 AI 助手 | 第三梯队 |
| **OpenCode** | TypeScript | ★★★☆☆ | 开源编程 Agent | 第三梯队 |
| **Crush** | Go | ★★★☆☆ | 终端编程助手 | 第三梯队 |
| **Kimi-CLI** | Python | ★★☆☆☆ | Kimi 模型 CLI 客户端 | 第三梯队 |

**梯队划分依据**：
- **第一梯队**：Python 项目，代码可直接复用或参考
- **第二梯队**：Go/Rust/TS 项目，架构设计思想有高借鉴价值
- **第三梯队**：有局部参考价值，但整体关联度较低

---

## 二、第一梯队详细分析

### 2.1 NanoBot（Python）

**路径**：`/Users/chenjiamin/ai-example/nanobot/`
**规模**：~4,000 行核心代码
**技术栈**：Python + LiteLLM + Pydantic + WebSocket + asyncio

#### 项目结构

```
nanobot/
  agent/loop.py         # Agent 主循环（状态机）
  agent/subagent.py     # 后台任务执行（SubagentManager）
  agent/context.py      # 分层系统提示词构建
  agent/memory.py       # 双层记忆（MEMORY.md + HISTORY.md）
  agent/skills.py       # Skills 加载器（SKILL.md frontmatter）
  agent/tools/          # 工具注册（FileSystem, Shell, Web, Spawn, Cron, MCP）
  cron/service.py       # Cron 调度器（at/every/cron 三种模式）
  cron/types.py         # CronSchedule, CronPayload, CronJobState
  heartbeat/service.py  # 两阶段决策：HEARTBEAT.md → LLM 决策 → 执行
  channels/             # 11+ 通道（Telegram, Discord, 飞书, 钉钉, Slack, Email 等）
  bus/                  # MessageBus（asyncio.Queue 入站/出站解耦）
  config/schema.py      # Pydantic 配置，支持 20+ LLM 提供商
  session/manager.py    # JSONL 追加模式会话管理
```

#### 可借鉴的核心设计

**1. Subagent 系统** — 映射天枢兵部

文件：`agent/subagent.py`

SubagentManager 维护 `_running_tasks` 和 `_session_tasks` 两个追踪字典。子 Agent 使用隔离工具集（排除 message/spawn 工具防止递归）。结果通过 MessageBus 回报。支持通过 `cancel_by_session()` 级联取消。

天枢借鉴点：
- 兵部执行司的子任务隔离模型
- 工具权限裁剪防止递归失控
- 级联取消机制用于任务撤回

**2. Cron 调度服务** — 映射天枢定时调度

文件：`cron/service.py`, `cron/types.py`

三种调度模式：
- `at`：一次性时间戳触发
- `every`：固定间隔循环
- `cron`：croniter 表达式

状态持久化在 `jobs.json`。通过 `_get_next_wake_ms()` 计算最近唤醒时间实现高效调度。每个 job 错误隔离互不影响。

**3. Heartbeat 心跳服务** — 映射天枢都察院巡检

文件：`heartbeat/service.py`

两阶段决策模式：
1. 读取 HEARTBEAT.md 配置，LLM 通过 tool_call 决策（skip/run）
2. 仅在 action=="run" 时执行

设计亮点：用 tool_call 做结构化决策，避免自由文本解析的不可靠性。

**4. 分层 Context 构建** — 映射天枢御案台

文件：`agent/context.py`

四层系统提示词：Identity → Bootstrap 文件（AGENTS.md, SOUL.md, USER.md, TOOLS.md）→ Memory → Skills。运行时上下文注入到用户消息。大型工具结果自动截断（500 字符）。

**5. 双层记忆系统** — 映射天枢文渊阁

文件：`agent/memory.py`

- MEMORY.md：长期事实，LLM 自动归纳整理
- HISTORY.md：时间戳格式的可 grep 日志

当未整理记忆数量 ≥ memory_window 时触发归纳。

**6. MessageBus 消息总线** — 映射天枢通政司

文件：`bus/`

`asyncio.Queue[InboundMessage]` 和 `asyncio.Queue[OutboundMessage]` 实现通道与 Agent 的完全解耦。

**7. Skills 加载器** — 映射天枢能力注册

文件：`agent/skills.py`

三级来源（Workspace > Builtin），渐进式加载（先加载 XML 摘要，按需加载完整 SKILL.md），frontmatter 中声明 `requires.bins` 和 `requires.env` 进行依赖检查。

---

### 2.2 DeepAgents（Python / LangChain）

**路径**：`/Users/chenjiamin/ai-example/deepagents/`
**技术栈**：Python + LangChain + LangGraph

#### 项目结构

```
deepagents/libs/deepagents/deepagents/
  graph.py                      # create_deep_agent() 入口
  middleware/subagents.py        # Sub-agent 上下文隔离
  middleware/filesystem.py       # 文件系统工具
  middleware/memory.py           # AGENTS.md 记忆加载
  middleware/skills.py           # SKILL.md 渐进加载
  middleware/summarization.py    # Token 超限时自动摘要
  backends/protocol.py           # BackendProtocol 抽象（4 种实现）
  base_prompt.md                 # BASE_AGENT_PROMPT 模板
```

#### 可借鉴的核心设计

**1. SubAgent 上下文隔离** — 映射天枢内阁分派

文件：`middleware/subagents.py`

```python
_EXCLUDED_STATE_KEYS = {
    "messages", "todos", "structured_response",
    "skills_metadata", "memory_contents"
}
```

子 Agent 接收新消息 + 父级的非排除状态。仅过滤后的结果返回父级。最后一条 AIMessage 转换为 ToolMessage 回传。

天枢借鉴点：
- 内阁分派任务时的上下文裁剪策略
- 防止子任务上下文污染父级
- 结果格式标准化（ToolMessage 统一回传）

**2. 并行任务执行**

单轮 AI 响应中可包含多个 `task` tool_call，各自独立执行，结果通过 Command 对象合并。Prompt 中显式指导何时该并行。

天枢借鉴点：兵部可同时调度多个执行单元并行办差。

**3. Task 工具设计**

`_build_task_tool(subagents)` 动态构建 StructuredTool。Prompt 指导原则：task 用于复杂多步可委派工作，不用于简单的几次工具调用。调用者指定 subagent_type。

**4. 中间件栈** — 映射天枢处理管线

有序中间件栈：
1. TodoListMiddleware（规划）
2. MemoryMiddleware（AGENTS.md）
3. SkillsMiddleware（SKILL.md）
4. FilesystemMiddleware（文件操作）
5. SubAgentMiddleware（委派）
6. SummarizationMiddleware（自动压缩）
7. AnthropicPromptCachingMiddleware
8. PatchToolCallsMiddleware
9. HumanInTheLoopMiddleware（可选）

天枢借鉴点：每个"部"可以看作一个中间件，请求/响应依次流过处理管线。

**5. BackendProtocol 后端抽象**

4 种实现：
- StateBackend（会话内）
- FilesystemBackend（本地持久化）
- StoreBackend（LangGraph Store）
- LocalShellBackend（沙盒执行）

天枢借鉴点：执行环境的可插拔设计，支持从本地文件到分布式存储的平滑过渡。

---

### 2.3 CoPaw（Python / FastAPI）

**路径**：`/Users/chenjiamin/ai-example/copaw/`
**规模**：~48,348 行 Python 代码，214 个文件
**技术栈**：Python + FastAPI + AgentScope + APScheduler + ReMeLight + asyncio

#### 项目结构

```
src/copaw/
  app/
    _app.py                    # FastAPI 应用初始化 + lifespan
    runner/
      runner.py                # AgentRunner（主执行引擎）
      manager.py               # ChatManager（会话 CRUD）
      session.py               # SafeJSONSession 状态持久化
      command_dispatch.py      # /compact, /new 系统命令
      query_error_dump.py      # 错误日志转 JSON
    channels/
      base.py                  # BaseChannel 抽象
      manager.py               # ChannelManager（生命周期 + 队列）
      renderer.py              # 消息渲染管线（Markdown → 通道原生格式）
      dingtalk/                # 钉钉
      feishu/                  # 飞书
      discord_/                # Discord
      telegram/                # Telegram
      qq/                      # QQ
      imessage/                # iMessage (macOS)
      mqtt/                    # MQTT (IoT)
      voice/                   # Twilio 语音
      console/                 # 控制台
    crons/
      manager.py               # CronManager（APScheduler 封装）
      executor.py              # CronExecutor（通过 runner 执行任务）
      heartbeat.py             # Heartbeat（HEARTBEAT.md 定时执行）
      models.py                # CronJobSpec, ScheduleSpec, DispatchSpec
      repo/json_repo.py        # JSON 持久化存储
    mcp/
      manager.py               # MCPClientManager（热重载）
      watcher.py               # MCPConfigWatcher
  agents/
    react_agent.py             # CoPawAgent（继承 AgentScope ReActAgent）
    skills_manager.py          # Skill 发现与加载
    skills_hub.py              # 远程 Skill Hub 客户端
    model_factory.py           # 模型工厂
    routing_chat_model.py      # 多 Provider 路由
    prompt.py                  # System prompt 构建
    tools/                     # 内置工具（11+）
    memory/
      memory_manager.py        # ReMeLight 封装 + 自动压缩
    hooks/
      bootstrap.py             # 首次启动引导
      memory_compaction.py     # 自动上下文窗口管理
    skills/                    # 内置 Skills（PDF, DOCX, PPTX, XLSX 等）
  config/
    config.py                  # Pydantic 配置（通道、心跳等）
    watcher.py                 # ConfigWatcher（文件变更热重载）
  providers/
    provider_manager.py        # Provider 注册 + 模型列表
    provider.py                # BaseProvider 协议
    openai_provider.py         # OpenAI 兼容
    anthropic_provider.py      # Claude
    ollama_provider.py         # Ollama 本地模型
```

#### 可借鉴的核心设计

**1. ChannelManager 消息防抖** — 映射天枢通政司

文件：`app/channels/manager.py`

多线程队列处理，每通道可配置 worker 数（默认 4）。两种防抖策略：
- **时间防抖**：合并 X 毫秒内的连续消息
- **会话防抖**：持有内容直到文本到达后批量处理

天枢借鉴点：通政司处理高频消息时避免重复推送，尤其适用于钉钉/飞书群聊场景。

**2. 消息渲染管线** — 映射天枢通政司

文件：`app/channels/renderer.py`

将 AgentScope Message 解耦为通道无关内容，再转换为通道原生格式。可配置过滤器：隐藏工具细节、过滤思考块、添加 bot 前缀。

```python
BaseChannelConfig:
  filter_tool_messages: bool   # 是否隐藏工具调用细节
  filter_thinking: bool        # 是否过滤思考过程
  bot_prefix: str              # 机器人前缀标识
  dm_policy: "open" | "allowlist"   # 私聊访问控制
  group_policy: "open" | "allowlist" # 群聊访问控制
```

天枢借鉴点：通政司对不同通道定制输出格式和访问控制策略。

**3. ReMeLight 智能记忆压缩** — 映射天枢文渊阁

文件：`agents/memory/memory_manager.py`

基于 `reme-ai` 库的高级记忆管理：
- **向量搜索**：Embedding + 全文检索混合
- **自动压缩**：Token 感知的智能截断
  - 保留最近 N 条消息（默认 3 条）不压缩
  - 旧消息通过 LLM 摘要压缩
  - 压缩比例可配（默认 70%）
- **多后端**：auto/local/chroma

天枢借鉴点：文渊阁的记忆管理——自动压缩避免上下文窗口溢出，比 NanoBot 的双层记忆更精细。

**4. Cron 任务调度** — 映射天枢定时调度

文件：`app/crons/manager.py`, `app/crons/executor.py`, `app/crons/models.py`

基于 APScheduler 的完整调度系统：

```python
CronJobSpec:
  job_id: str                  # 唯一标识
  query: str                   # Agent 查询字符串
  schedule: ScheduleSpec       # cron 表达式 + 时区
  dispatch: DispatchSpec       # 目标通道、用户、会话、模式
  runtime: JobRuntimeSpec      # max_concurrency=1, timeout=120s, misfire_grace=60s
```

两种分发模式：
- **stream**：实时流式输出到通道
- **final**：仅发送最终结果

天枢借鉴点：CronJobSpec 的 dispatch + runtime 设计——任务不仅定义"做什么"，还定义"结果发给谁、怎么发、并发和超时限制"。

**5. 配置热重载** — 映射天枢运维

文件：`config/watcher.py`, `app/mcp/watcher.py`

ConfigWatcher 监控配置文件变更，触发异步更新无需重启。适用于通道配置、MCP 客户端、Cron 任务。

天枢借鉴点：线上变更通道/调度配置无需重启服务。

**6. Hook 生命周期** — 映射天枢都察院

文件：`agents/hooks/bootstrap.py`, `agents/hooks/memory_compaction.py`

通过 AgentScope 的 `register_instance_hook()` 注册 pre/post reasoning 钩子。BootstrapHook 处理首次启动引导，MemoryCompactionHook 自动管理上下文窗口。

天枢借鉴点：都察院可通过 Hook 在任务执行前后插入审计逻辑，无需修改执行器代码。

**7. Skills Hub 市场模式** — 映射天枢能力生态

文件：`agents/skills_hub.py`, `agents/skills_manager.py`

远程 Skill Hub（clawhub.ai）支持发现和安装。本地优先执行。Markdown 定义 + 内嵌 Python/Shell 脚本。启动时自动从代码同步到 working_dir。

天枢借鉴点：能力包的远程发现 + 本地执行模式，为天枢未来的 Skills 市场提供参考。

**8. QueryErrorDump 错误记录** — 映射天枢都察院

文件：`app/runner/query_error_dump.py`

执行失败时自动将详细错误信息（traceback、请求信息、Agent 状态、时间戳）写入临时 JSON 文件，用于事后分析。

天枢借鉴点：都察院的故障归因——结构化错误记录比日志更利于自动分析。

---

## 三、第二梯队设计借鉴

### 3.1 PicoClaw（Go）— Worker 队列与工作区隔离

**路径**：`/Users/chenjiamin/ai-example/picoclaw/`

#### 可借鉴设计

**1. Per-Channel 速率限制器**

文件：`pkg/channels/manager.go`

每个通道独立限速（Telegram: 20 msg/s, Discord: 1 msg/s）。Python 等价实现：asyncio 速率限制器。

天枢借鉴点：通政司对不同通知渠道实施独立限速。

**2. Worker 队列架构**

```
Manager → Dispatcher → ChannelWorker (per-channel goroutine) → Channel.Send()
```

缓冲区大小 16，自动重试 3 次，指数退避。

天枢借鉴点：兵部执行队列的缓冲与重试机制。

**3. 工作区隔离**

文件：`pkg/agent/instance.go`

`resolveAgentWorkspace()` 创建隔离目录。`RestrictToWorkspace` + `AllowReadOutsideWorkspace` 配置。编译时路径白名单 `compilePatterns()`。

天枢借鉴点：每个子任务的文件系统隔离，防止越权访问。

**4. SpawnTool 异步生成**

文件：`pkg/tools/spawn.go` (L62-64)

`ExecuteAsync()` + `AsyncCallback` 模式。白名单检查防止未授权 spawn。Label 用于任务追踪。非阻塞主循环。

**5. TTL Janitor**

10 秒轮询自动清理过期 PlaceholderRecorder 条目，防止内存泄漏。

天枢借鉴点：户部的资源回收机制。

#### 关键参考文件

- `pkg/channels/manager.go` (L450-464, L502-557, L723-762)
- `pkg/tools/spawn.go` (L62-64)
- `pkg/tools/registry.go` (L51-80)
- `pkg/bus/types.go`

---

### 3.2 ZeroClaw（Rust）— Trait 可插拔架构与可观测性

**路径**：`/Users/chenjiamin/ai-example/zeroclaw/`
**哲学**："Zero overhead. Zero compromise. 100% Rust. 100% Agnostic."

#### 可借鉴设计

**1. Trait 可插拔架构** — 映射天枢各部接口抽象

文件：`src/providers/traits.rs` (L257-380)

Provider trait 声明 `capabilities()`、`convert_tools()`、`chat()`。支持 4 种工具输出格式（Gemini/Anthropic/OpenAI/PromptGuided），不支持原生工具时自动降级。

Python 等价：Protocol/ABC 类。

```python
# 天枢可参考的接口设计
class ProviderProtocol(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    def convert_tools(self, tools: list[Tool]) -> list[dict]: ...
    async def chat(self, messages: list[Message]) -> Response: ...
```

天枢借鉴点：每个"部"定义统一的 Protocol 接口，实现可替换。

**2. Observer 可观测性模式** — 映射天枢都察院

文件：`src/observability/traits.rs` (L9-95)

```
ObserverEvent:
  - AgentStart
  - LlmRequest
  - LlmResponse (duration/tokens)
  - ToolCallStart
  - ToolCall
  - Error

ObserverMetric: gauge/counter 度量

Observer trait:
  - record_event()
  - record_metric()
```

支持 Prometheus、结构化日志、OpenTelemetry 后端。

天枢借鉴点：都察院的审计基础设施——统一事件类型 + 多后端输出。

**3. Memory Trait 系统** — 映射天枢文渊阁

文件：`src/memory/traits.rs` (L54-95)

接口：`store()`、`recall()`、`forget()`、`health_check()`
分类：Core / Daily / Conversation / Custom
会话隔离记忆
多后端：SQLite（默认）、PostgreSQL、Qdrant（向量）

天枢借鉴点：文渊阁的多后端存储抽象和记忆分类体系。

**4. Cron 调度器** — 映射天枢定时任务

文件：`src/cron/scheduler.rs` (L22-49)

- 健康检查集成 `mark_component_ok()`
- 指数退避重试（200ms → 30s）
- 安全策略违规不重试
- `max_concurrent` 并发限制
- 最小轮询间隔 5s

天枢借鉴点：调度器的健康检查、并发控制和安全策略集成。

**5. Provider 能力自报告**

每个 Provider 声明自身支持的能力（native_tool_calling, vision），系统对不支持的特性自动降级。

天枢借鉴点：工具/Agent 注册时声明自身能力，调度器据此分配任务。

---

### 3.3 Pi-Mono（TypeScript Monorepo）— 事件系统与不可变上下文

**路径**：`/Users/chenjiamin/ai-example/pi-mono/`

#### 可借鉴设计

**1. 事件系统** — 映射天枢诏令调度

文件：`packages/mom/src/events.ts` (L43-79)

三种事件类型：
- ImmediateEvent：立即执行
- OneShotEvent：ISO 8601 时间戳 + 时区，一次性触发
- PeriodicEvent：Cron 表达式 + IANA 时区，周期执行

EventsWatcher 监控 events/ 目录，JSON 文件作为任务定义。100ms 防抖。

天枢借鉴点：诏令的三种下发模式（即时/定时/周期）。

**2. 不可变消息链**

```typescript
const newMessages = [...prompts];
const currentContext = {
    ...context,
    messages: [...context.messages, ...prompts]
};
```

通过不可变上下文实现时间旅行调试。

天枢借鉴点：奏折链的不可变记录，支持回溯和复盘。

**3. Agent Loop 双模式**

- `agentLoop()`：带新提示启动
- `agentLoopContinue()`：从已有上下文恢复（故障恢复）

天枢借鉴点：任务的首次执行与断点续跑两种模式。

**4. EventStream 发射器**

AgentEvent 类型：agent_start, turn_start, message_start, agent_end。
事件发射与结果收集解耦。

天枢借鉴点：都察院监听执行事件流，无需侵入执行逻辑。

#### 关键参考文件

- `packages/agent/src/agent-loop.ts` (L28-54)
- `packages/mom/src/events.ts` (L43-79)

---

## 四、第三梯队概要

### 4.1 OpenClaw（TypeScript）

**路径**：`/Users/chenjiamin/ai-example/openclaw/`
**规模**：77+ 目录，pnpm monorepo，生产级多通道 AI 网关
**定位**："an AI that actually does things"——在你的设备上、在你的通道里、按你的规则运行

主要特性：20+ 通信通道、42 扩展、52 预设 Skills、LanceDB 记忆、Hooks 系统、Canvas 实时渲染、MCP 集成。

**重点借鉴点**（已纳入 architecture.md）：

| 设计 | 核心思想 | 天枢落点 | 引入阶段 |
|------|---------|---------|---------|
| **SKILL.md 模式** | Skill = Markdown 指导文档（YAML frontmatter + 正文 + 资格检查），不是代码插件。零编程门槛扩展 Agent 能力 | §6.4 Skills 体系 | Phase 2 |
| **Tool Policy Pipeline** | 7 级管道式工具过滤：profile → provider → agent → group，支持 ownerOnly 和安全兜底 | §4.4 工具权限分级 | Phase 1 |
| **25 个命名生命周期钩子** | 覆盖 Agent 全生命周期（before/after 模式），`before_tool_call` 可拦截，`message_sending` 可取消 | §5.4 都察院核心钩子集 | Phase 1 |
| **执行中实时审批** | T3 工具暂停等待批红，支持 allow-once / allow-always，审批字段从用户输入中完全剥离防注入 | §3.4 批红决策链 | Phase 1 |
| **统一 PluginApi** | `registerTool/Hook/Channel/Provider/Skill/Command` 一个 API 注册所有扩展能力，含独占槽位机制 | §6.4 统一注册 API | Phase 2 |
| **Compaction 策略** | 分片压缩 + 标识符保留 + 合并摘要 + Context Window Guard | §4.2 消息历史管理 | Phase 1 |
| **Lane-based 并发** | session lane + global lane 双层队列，避免全局锁 | §5.3 兵部演进 | Phase 3 |

**延后或不采纳**：

| 设计 | 原因 |
|------|------|
| Canvas 实时渲染（Agent 生成 HTML 推送到 WebView） | 天枢无移动端/桌面端计划 |
| 五维路由绑定（channel→account→peer→guild→role） | 当前为单用户场景 |
| Scope-based Gateway 授权（5 个 operator scope） | Phase 2 Gateway API 时再评估 |
| 20+ 通道适配器体系 | 会稀释 MVP |
| Identity Links（跨通道身份关联） | Phase 3 多用户时参考 |

#### 关键参考文件

| 文件 | 借鉴内容 |
|------|----------|
| `src/agents/pi-embedded-runner/run.ts` | Agent Loop 状态机 + Lane 并发 + 多级重试 |
| `src/agents/pi-embedded-runner/compact.ts` | Compaction 分片压缩策略 |
| `src/agents/tool-policy.ts` + `tool-policy-pipeline.ts` | 多层工具过滤管道 |
| `src/agents/tool-loop-detection.ts` | 工具调用循环检测 |
| `src/agents/context-window-guard.ts` | 上下文窗口保护 |
| `src/plugins/types.ts` | PluginApi 统一注册 + 25 个生命周期钩子定义 |
| `src/gateway/node-invoke-system-run-approval.ts` | 执行审批安全隔离 |
| `skills/` (任意 SKILL.md) | Skill = Markdown 文档模式 |
| `src/channels/plugins/types.plugin.ts` | Channel 15+ 子适配器接口 |
| `src/routing/resolve-route.ts` | 7 级路由绑定解析 |
| `src/memory/` | SQLite + sqlite-vec 混合搜索 + 时间衰减 |

### 4.2 OpenCode（TypeScript）

**路径**：`/Users/chenjiamin/ai-example/opencode/`
**规模**：57 目录

主要特性：AI 编程 Agent，subagent 用于复杂搜索和多步任务，完整 Agent Client Protocol 集成。

局部借鉴：Subagent 模式用于多步编程任务。

### 4.3 Crush（Go）

**路径**：`/Users/chenjiamin/ai-example/crush/`

主要特性：多模型支持、LSP 集成代码上下文感知、MCP 支持（stdio/HTTP/SSE）、会话管理、工具权限管理。

局部借鉴：MCP 传输类型处理、LSP 集成模式。

### 4.4 Kimi-CLI（Python）

**路径**：`/Users/chenjiamin/ai-example/kimi-cli/`

主要特性：Shell 命令模式、VS Code 扩展 + ACP、Zsh 集成、MCP Server 管理。

局部借鉴：有限——主要绑定 Kimi 模型。ACP 集成方式可参考。

---

## 五、按天枢模块的借鉴映射表

| 天枢模块 | NanoBot | DeepAgents | CoPaw | PicoClaw | ZeroClaw | Pi-Mono | OpenClaw |
|----------|---------|------------|-------|----------|----------|---------|---------|
| **御案台**（任务入口） | AgentLoop + Context 四层构建 | `create_deep_agent()` 入口 | FastAPI + ChannelManager | CLI/API handlers | HTTP API | EventsWatcher | Gateway RPC + Scope 授权 |
| **内阁**（任务拆解） | — | SubAgent task tool + `write_todos` | CoPawAgent + Skills | ContextBuilder | Agent 预处理 | `agentLoop()` | — |
| **兵部**（执行调度） | SubagentManager + Tools | SubAgentMiddleware 并行执行 | AgentRunner + CronExecutor | Worker 队列 + SpawnTool | Provider + Agent | EventStream | **Lane-based 并发** + Compaction + Tool Loop Detection |
| **都察院**（审计） | HISTORY.md + Heartbeat | — | QueryErrorDump + Hooks | — | Observer 事件 + Metric | EventStream 监听 | **25 个生命周期钩子** + Exec Approval 安全隔离 |
| **通政司**（通知） | MessageBus + 11 Channels | — | ChannelManager 防抖 + 9 Channels + 渲染管线 | Per-Channel 限速 | — | — | 20+ Channels + Canvas 实时渲染 |
| **文渊阁**（知识库） | Skills + Memory 双层 | AGENTS.md + SKILL.md + Backend | ReMeLight 向量 + 压缩 + Skills Hub | SessionManager | Memory trait 多后端 | 不可变消息链 | SQLite+sqlite-vec 混合搜索 + 时间衰减 + **SKILL.md 模式** |
| **户部**（成本管控） | Config + Sessions | — | RoutingChatModel 路由 | TTL Janitor | TokenUsage + Cost | — | Context Window Guard + Auth Profile Rotation |
| **吏部**（注册治理） | — | — | — | — | — | — | **统一 PluginApi** + 独占槽位 |
| **刑部**（权限风控） | — | — | — | — | — | — | **7 级 Tool Policy Pipeline** + ownerOnly + 安全兜底 |

### 各模块首选借鉴来源

| 天枢模块 | 首选参考 | 核心借鉴点 |
|----------|----------|-----------|
| **御案台** | NanoBot `context.py` | 四层 system prompt 构建；诏令格式化 |
| **内阁** | DeepAgents `subagents.py` | 上下文隔离分派；`_EXCLUDED_STATE_KEYS` 策略 |
| **兵部** | NanoBot `subagent.py` + CoPaw CronExecutor + **OpenClaw** `run.ts` | 隔离工具集 + dispatch 模式 + 并发/超时控制 + **Lane-based 并发 + Compaction** |
| **都察院** | ZeroClaw Observer + NanoBot Heartbeat + CoPaw Hooks + **OpenClaw** `types.ts` | Observer 事件体系 + 两阶段巡检 + **25 个命名生命周期钩子** |
| **通政司** | CoPaw ChannelManager + NanoBot `bus/` | 消息防抖 + 渲染管线 + 访问控制 + MessageBus 解耦 |
| **文渊阁** | CoPaw ReMeLight + NanoBot `memory.py` + ZeroClaw Memory trait + **OpenClaw** `skills/` | 向量+全文混合检索 + 自动压缩 + 多后端存储抽象 + **SKILL.md 文档即能力** |
| **户部** | ZeroClaw TokenUsage + CoPaw RoutingChatModel | Token 追踪 + 多 Provider 成本路由 |
| **吏部** | **OpenClaw** `types.ts` (`PluginApi`) | **统一注册 API + 独占槽位机制** |
| **刑部** | **OpenClaw** `tool-policy-pipeline.ts` + ZeroClaw-1 | **多层工具 Policy Pipeline** + 安全策略执行 |
| **批红** | **OpenClaw** `node-invoke-system-run-approval.ts` | **执行中实时审批 + allow-once/always + 审批字段安全隔离** |

---

## 六、可复用的设计模式汇总

### 模式 1：上下文隔离委派

**来源**：DeepAgents SubAgent、NanoBot SubagentManager

子任务接收裁剪后的上下文（排除 messages、todos 等），使用受限工具集，结果以标准格式回传父级。防止上下文爆炸和递归失控。

```python
# 核心思想
EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response"}
child_state = {k: v for k, v in parent_state.items() if k not in EXCLUDED_STATE_KEYS}
child_tools = [t for t in parent_tools if t.name not in {"message", "spawn"}]
```

### 模式 2：MessageBus 解耦

**来源**：NanoBot bus/、PicoClaw bus/

asyncio.Queue 实现入站/出站消息的完全解耦。通道（Channel）只负责收发，不关心 Agent 逻辑。

```python
inbound: asyncio.Queue[InboundMessage]
outbound: asyncio.Queue[OutboundMessage]
```

### 模式 3：中间件栈

**来源**：DeepAgents Middleware

请求和响应依次流过有序中间件栈。每个中间件专注单一职责（规划、记忆、文件、委派、压缩）。新功能通过添加中间件扩展，不修改核心逻辑。

### 模式 4：Trait/Protocol 可插拔

**来源**：ZeroClaw Provider/Memory/Observer traits

用 Python Protocol 或 ABC 定义统一接口，实现可替换。每个模块声明自身能力（capabilities），调度器据此分配任务。

```python
class MemoryProtocol(Protocol):
    async def store(self, key: str, value: Any, category: str) -> None: ...
    async def recall(self, query: str, category: str) -> list[Memory]: ...
    async def forget(self, key: str) -> None: ...
    async def health_check(self) -> bool: ...
```

### 模式 5：两阶段决策

**来源**：NanoBot Heartbeat

Phase 1：读取配置/规则，LLM 通过 tool_call 做结构化决策（skip/run）。Phase 2：仅在 action=="run" 时执行。避免自由文本解析的不可靠性。

### 模式 6：不可变消息链 + EventStream

**来源**：Pi-Mono agent-loop + EventStream

每轮对话产生新的不可变上下文快照，支持时间旅行调试。事件流（agent_start → turn_start → message_start → agent_end）解耦事件发射与结果收集。

### 模式 7：Worker 队列 + 速率限制

**来源**：PicoClaw ChannelWorker

Per-channel 工作协程，独立速率限制，缓冲区 + 指数退避重试。适用于通政司对外通知和兵部任务调度。

### 模式 8：三模式定时调度

**来源**：NanoBot Cron + Pi-Mono Events

三种触发模式：即时（at/Immediate）、间隔（every/Periodic）、Cron 表达式（cron/PeriodicEvent）。状态持久化 + 高效最近唤醒计算。

### 模式 9：消息防抖与渲染管线

**来源**：CoPaw ChannelManager + MessageRenderer

时间防抖合并高频消息，会话防抖批量处理。渲染管线将 Agent 输出解耦为通道无关内容，再按通道特性转换格式。可配置过滤器控制输出细节。

### 模式 10：配置热重载

**来源**：CoPaw ConfigWatcher + MCPConfigWatcher

文件变更检测触发异步更新，无需重启服务。适用于通道配置、调度任务、MCP 客户端等运行时可变组件。

### 模式 11：Manager + Repository 模式

**来源**：CoPaw ChatManager/CronManager/ChannelManager

统一生命周期管理（init → start → stop）。Repository 抽象持久化层（JSON/数据库可替换）。asyncio Lock 保证线程安全。Manager 封装业务逻辑，Repository 只管读写。

### 模式 12：智能记忆压缩

**来源**：CoPaw ReMeLight MemoryManager

Token 感知的自动压缩策略：保留最近 N 条消息不压缩，旧消息通过 LLM 摘要，压缩比例可调。向量检索 + 全文检索混合模式。

```python
# 核心配置
MEMORY_COMPACT_RATIO = 0.7        # 压缩比例
MEMORY_COMPACT_KEEP_RECENT = 3    # 保留最近 N 条不压缩
MEMORY_STORE_BACKEND = "auto"     # auto/local/chroma
```

### 模式 13：Skill = Markdown 指导文档

**来源**：OpenClaw skills/

Skill 不是代码插件，而是纯 Markdown 文件（YAML frontmatter + 指导正文）。Agent 在运行时判断适用的 Skill，读取 SKILL.md 并遵循其中的步骤指引。Skill 有资格检查机制（requires.bins / env / config / os），确保运行环境满足要求。零编程门槛，任何人写一份 Markdown 就能扩展 Agent 能力。

### 模式 14：多层工具 Policy Pipeline

**来源**：OpenClaw tool-policy-pipeline.ts

工具过滤不是单层黑白名单，而是 7 级管道依次过滤：全局 profile → provider 级 → agent 级 → group 级。每层可以收窄也可以放宽上层的策略。安全兜底：如果过滤后只剩插件工具，自动回退到全局默认策略，防止误禁核心能力。

### 模式 15：命名生命周期钩子体系

**来源**：OpenClaw plugins/types.ts

25 个命名钩子覆盖 Agent 完整生命周期，每个钩子有强类型的 Event / Context / Result。`before_*` 钩子可返回拦截指令（block / cancel），`after_*` 钩子只能记录。钩子支持优先级排序，多个处理器按优先级顺序执行。相比事件总线的松耦合，钩子提供了更强的控制力和类型安全。

### 模式 16：执行中实时审批

**来源**：OpenClaw gateway/node-invoke-system-run-approval.ts

高风险工具调用时暂停等待人工批红，支持 allow-once（单次）和 allow-always（永久白名单）两种决策。关键安全设计：审批字段从用户输入中完全剥离后重建，防止权限提升攻击。审批请求可转发到聊天通道，支持 agentFilter 和 sessionFilter 精细控制。

### 模式 17：统一 PluginApi 注册

**来源**：OpenClaw plugins/types.ts (OpenClawPluginApi)

所有扩展能力通过一个统一 API 注册：registerTool / registerHook / registerChannel / registerService / registerProvider / registerCommand / registerContextEngine。独占槽位机制（exclusive slot）确保某些能力类型同一时间只有一个活跃实例（如 memory backend）。每个插件声明 manifest（id + configSchema + kind），运行时动态加载。

---

## 七、关键参考文件路径索引

### NanoBot（Python，第一梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `nanobot/agent/subagent.py` | Subagent 隔离执行、级联取消 |
| `nanobot/agent/context.py` | 四层 system prompt 构建 |
| `nanobot/agent/memory.py` | 双层记忆（MEMORY.md + HISTORY.md） |
| `nanobot/agent/skills.py` | Skills 渐进式加载 |
| `nanobot/agent/loop.py` | Agent 主循环状态机 |
| `nanobot/cron/service.py` | 三模式 Cron 调度 |
| `nanobot/cron/types.py` | 调度类型定义 |
| `nanobot/heartbeat/service.py` | 两阶段心跳决策 |
| `nanobot/channels/` | 11+ 通道实现 |
| `nanobot/bus/` | MessageBus 解耦 |
| `nanobot/config/schema.py` | Pydantic 配置管理 |

### CoPaw（Python，第一梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `copaw/src/copaw/app/channels/manager.py` | ChannelManager 防抖 + 多 worker 队列 |
| `copaw/src/copaw/app/channels/base.py` | BaseChannel 抽象 + 访问控制 |
| `copaw/src/copaw/app/channels/renderer.py` | 消息渲染管线 |
| `copaw/src/copaw/app/crons/manager.py` | CronManager（APScheduler 封装） |
| `copaw/src/copaw/app/crons/executor.py` | CronExecutor（stream/final 分发） |
| `copaw/src/copaw/app/crons/models.py` | CronJobSpec 完整任务模型 |
| `copaw/src/copaw/app/runner/runner.py` | AgentRunner 主执行引擎 |
| `copaw/src/copaw/app/runner/query_error_dump.py` | 结构化错误记录 |
| `copaw/src/copaw/agents/memory/memory_manager.py` | ReMeLight 智能记忆压缩 |
| `copaw/src/copaw/agents/hooks/memory_compaction.py` | 自动上下文窗口管理 |
| `copaw/src/copaw/agents/skills_manager.py` | Skill 发现与加载 |
| `copaw/src/copaw/agents/skills_hub.py` | 远程 Skill Hub 客户端 |
| `copaw/src/copaw/config/watcher.py` | 配置热重载 |
| `copaw/src/copaw/providers/provider_manager.py` | Provider 注册 + 路由 |
| `copaw/src/copaw/app/mcp/manager.py` | MCP 客户端热重载 |

### DeepAgents（Python，第一梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `deepagents/libs/deepagents/deepagents/graph.py` | Agent 创建入口 |
| `deepagents/libs/deepagents/deepagents/middleware/subagents.py` | 上下文隔离分派 |
| `deepagents/libs/deepagents/deepagents/middleware/summarization.py` | Token 超限自动摘要 |
| `deepagents/libs/deepagents/deepagents/backends/protocol.py` | 4 种 Backend 抽象 |
| `deepagents/libs/deepagents/deepagents/base_prompt.md` | Agent 基础 Prompt 模板 |

### PicoClaw（Go，第二梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `picoclaw/pkg/channels/manager.go` (L450-557, L723-762) | Worker 队列 + 限速 + TTL Janitor |
| `picoclaw/pkg/tools/spawn.go` (L62-64) | 异步 Spawn + AsyncCallback |
| `picoclaw/pkg/tools/registry.go` (L51-80) | 工具注册与上下文执行 |
| `picoclaw/pkg/bus/types.go` | 消息总线类型定义 |
| `picoclaw/pkg/agent/instance.go` | 工作区隔离 |

### ZeroClaw（Rust，第二梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `zeroclaw/src/providers/traits.rs` (L257-380) | Provider trait 可插拔架构 |
| `zeroclaw/src/observability/traits.rs` (L9-95) | Observer 事件与度量体系 |
| `zeroclaw/src/memory/traits.rs` (L54-95) | Memory trait 多后端 |
| `zeroclaw/src/cron/scheduler.rs` (L22-49) | Cron 健康检查 + 并发控制 |

### Pi-Mono（TypeScript，第二梯队）

| 文件 | 借鉴内容 |
|------|----------|
| `pi-mono/packages/agent/src/agent-loop.ts` (L28-54) | Agent Loop 双模式 |
| `pi-mono/packages/mom/src/events.ts` (L43-79) | 三种事件类型 + EventsWatcher |

### OpenClaw（TypeScript，第三梯队 → 重点借鉴）

| 文件 | 借鉴内容 |
|------|----------|
| `openclaw/src/agents/pi-embedded-runner/run.ts` | Agent Loop 状态机 + Lane-based 并发 + 多级重试 + Auth Profile Rotation |
| `openclaw/src/agents/pi-embedded-runner/compact.ts` | Compaction 分片压缩 + 标识符保留 + 合并摘要 |
| `openclaw/src/agents/tool-policy.ts` | 工具风险分级 + ownerOnly + Plugin-Only 安全兜底 |
| `openclaw/src/agents/tool-policy-pipeline.ts` | 7 级管道式工具过滤（profile → provider → agent → group） |
| `openclaw/src/agents/tool-loop-detection.ts` | 工具调用循环检测 |
| `openclaw/src/agents/context-window-guard.ts` | 上下文窗口保护（Token 监控 + 硬性最低值） |
| `openclaw/src/plugins/types.ts` | OpenClawPluginApi 统一注册 + 25 个命名生命周期钩子 |
| `openclaw/src/plugins/manifest.ts` | 插件清单（id + configSchema + kind + slot 机制） |
| `openclaw/src/gateway/node-invoke-system-run-approval.ts` | 执行审批安全隔离（allow-once / allow-always + 字段剥离） |
| `openclaw/src/gateway/method-scopes.ts` | 5 个 Operator Scope 的 RPC 授权 |
| `openclaw/src/channels/plugins/types.plugin.ts` | ChannelPlugin 15+ 子适配器接口 |
| `openclaw/src/routing/resolve-route.ts` | 7 级路由绑定（peer → guild+roles → team → account → channel） |
| `openclaw/src/memory/` | SQLite + sqlite-vec 混合搜索 + FTS5 + 时间衰减 |
| `openclaw/src/agents/skills/` | SKILL.md 模式（YAML frontmatter + 指导正文 + 资格检查） |
| `openclaw/skills/` (任意 SKILL.md) | 52 个 Skill 示例（coding-agent, github, notion, slack 等） |

---

> 所有路径相对于 `/Users/chenjiamin/ai-example/`。
> 本文档基于 2026-03 分析，随项目演进可能需要更新。
