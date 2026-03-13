# 天枢（Tianshu）全局架构设计

> 异步 AI 多智能体执行平台 — 白天下旨，夜间办差，早上递折子。

---

## 一、系统总览

### 1.1 核心流程

```
┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐
│  诏令下发 │───▶│ 任务排队  │───▶│ 定时调度  │───▶│ 内阁拆解  │───▶│ 兵部执行 │───▶│ 都察院审计│───▶│ 通政司汇报│
│ (Edict)  │    │ (Queue)  │    │(Schedule)│    │(Planner) │    │(Executor)│    │ (Auditor)│    │(Notifier)│
└─────────┘    └─────────┘    └──────────┘    └─────────┘    └─────────┘    └──────────┘    └─────────┘
      │                                              │               │               │              │
      │                                              │               │               │              │
      ▼                                              ▼               ▼               ▼              ▼
 ┌─────────┐                                   ┌─────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐
 │ 御案台   │                                   │ 文渊阁   │    │  户部    │    │ EventBus │    │ Channels│
 │(Gateway) │                                   │ (Memory) │    │  (Cost) │    │ 事件总线  │    │ 通知通道 │
 └─────────┘                                   └─────────┘    └─────────┘    └──────────┘    └─────────┘
```

**一次完整的"下旨 → 办差 → 递折"流程：**

1. 用户通过 **御案台**（API / CLI / 飞书 / 钉钉）下发 **诏令（Edict）**
2. 御案台校验格式、注入上下文，将诏令推入 **任务队列**
3. **调度器（Scheduler）** 按时间策略（即时 / 定时 / 周期）触发任务
4. **内阁（Planner）** 理解诏令意图，拆解为结构化子任务列表，分派给兵部
5. **兵部（Executor）** 分配 Agent 并发执行子任务，调用工具链完成具体工作
6. **都察院（Auditor）** 通过 EventBus 实时监听执行过程，检查越权/偏离/幻觉/超额
7. 执行完毕，兵部提交 **奏折（Memorial）**，经都察院审计盖章
8. **通政司（Notifier）** 将结果格式化后推送到用户指定的通知通道
9. 用户查阅奏折，下达 **批红（Decree）**（通过 / 驳回 / 追加指令）
10. 全程数据沉淀到 **文渊阁（Memory）**，**户部（CostManager）** 记录成本

### 1.2 技术栈

| 层次 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.12+ | asyncio 原生支持，生态丰富 |
| 数据模型 | Pydantic v2 | 诏令 / 奏折 / 批红的强类型校验 |
| LLM 接入 | LiteLLM | 统一 100+ Provider 接口 |
| 定时调度 | APScheduler 4.x | cron 表达式 + 时区 + 并发控制 |
| API 网关 | FastAPI | 异步、自动文档 |
| 异步运行时 | asyncio | Task 池 + Queue 解耦 |
| 配置管理 | Pydantic Settings | 环境变量 + YAML + 热重载 |
| 存储（Phase 0） | 文件系统 (JSON/JSONL) | 零依赖，后续可迁移 |
| 存储（Phase 2+） | SQLite → PostgreSQL | 渐进式升级 |

### 1.3 命名原则

**外层古风，内层现代**：

| 外层（用户可见） | 内层（代码实现） | 说明 |
|-----------------|-----------------|------|
| 诏令 | `Edict` | 任务输入模型 |
| 奏折 | `Memorial` | 任务结果模型 |
| 批红 | `Decree` | 用户决策反馈 |
| 御案台 | `Gateway` | 任务接入层 |
| 内阁 | `Planner` | 任务拆解层 |
| 兵部 | `Executor` | 任务执行层 |
| 都察院 | `Auditor` | 审计监察层 |
| 通政司 | `Notifier` | 通知汇报层 |
| 文渊阁 | `Memory` | 知识记忆层 |
| 户部 | `CostManager` | 成本管控层 |

---

## 二、数据模型层

### 2.1 Edict（诏令）— 任务输入的统一模型

> **参考**：CoPaw `CronJobSpec`（dispatch + runtime 一体化设计，`app/crons/models.py`）+ Pi-Mono `EventTypes`（即时/定时/周期三模式，`packages/mom/src/events.ts` L43-79）

```python
class EdictPriority(str, Enum):
    URGENT = "urgent"       # 立即执行
    NORMAL = "normal"       # 正常排队
    LOW = "low"             # 空闲时执行

class EdictSchedule(BaseModel):
    """调度策略 — 参考 Pi-Mono 三种事件类型 + NanoBot Cron 三模式"""
    mode: Literal["immediate", "once", "cron"]  # 即时 / 一次性定时 / 周期
    at: datetime | None = None                  # once 模式的触发时间
    cron_expr: str | None = None                # cron 模式的表达式
    timezone: str = "Asia/Shanghai"              # IANA 时区

class EdictDispatch(BaseModel):
    """结果分发策略 — 参考 CoPaw DispatchSpec"""
    channel: str                                # 目标通道（feishu / dingtalk / email）
    mode: Literal["stream", "final"] = "final"  # 实时流式 or 仅最终结果
    notify_on_error: bool = True                # 失败时是否通知

class EdictRuntime(BaseModel):
    """运行时约束 — 参考 CoPaw JobRuntimeSpec + ZeroClaw 并发控制"""
    max_concurrency: int = 1                    # 最大并发子任务数
    timeout_seconds: int = 300                  # 单任务超时
    max_tokens: int | None = None               # Token 预算上限
    max_cost: float | None = None               # 成本预算上限（美元）
    retry_count: int = 0                        # 失败重试次数

class Edict(BaseModel):
    """诏令 — 天枢的任务输入统一模型"""
    id: str = Field(default_factory=lambda: ulid.new().str)
    goal: str                                   # 任务目标（自然语言）
    context: str | None = None                  # 补充上下文
    priority: EdictPriority = EdictPriority.NORMAL
    schedule: EdictSchedule = EdictSchedule(mode="immediate")
    dispatch: EdictDispatch
    runtime: EdictRuntime = EdictRuntime()
    constraints: list[str] = []                 # 禁止事项
    output_format: str | None = None            # 期望的输出格式
    requires_review: bool = False               # 是否需要人工复核
    created_at: datetime = Field(default_factory=datetime.now)
    created_by: str = "user"
```

### 2.2 Memorial（奏折）— 任务结果的统一模型

> **参考**：DeepAgents 子 Agent 结果回传（`middleware/subagents.py`，AIMessage → ToolMessage 标准化格式）+ Pi-Mono 不可变消息链（时间旅行调试）

```python
class MemorialStatus(str, Enum):
    PENDING = "pending"         # 排队中
    PLANNING = "planning"       # 内阁拆解中
    EXECUTING = "executing"     # 兵部执行中
    AUDITING = "auditing"       # 都察院审计中
    COMPLETED = "completed"     # 已完成
    FAILED = "failed"           # 失败
    CANCELLED = "cancelled"     # 已取消
    NEEDS_REVIEW = "needs_review"  # 需人工复核

class SubTaskResult(BaseModel):
    """子任务结果 — 参考 DeepAgents ToolMessage 标准化回传"""
    task_id: str
    description: str
    status: Literal["success", "failed", "skipped"]
    output: str
    error: str | None = None
    tokens_used: int = 0
    cost: float = 0.0
    duration_seconds: float = 0.0

class AuditResult(BaseModel):
    """审计结果 — 参考 ZeroClaw Observer 事件 + CoPaw QueryErrorDump"""
    passed: bool
    issues: list[str] = []                      # 发现的问题
    warnings: list[str] = []                    # 警告信息
    audited_at: datetime = Field(default_factory=datetime.now)

class Memorial(BaseModel):
    """奏折 — 天枢的任务结果统一模型"""
    id: str = Field(default_factory=lambda: ulid.new().str)
    edict_id: str                               # 关联的诏令 ID
    status: MemorialStatus = MemorialStatus.PENDING
    summary: str | None = None                  # 结果摘要
    subtask_results: list[SubTaskResult] = []   # 子任务结果列表
    audit: AuditResult | None = None            # 审计结论
    total_tokens: int = 0
    total_cost: float = 0.0
    total_duration: float = 0.0
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    trace: list[dict] = []                      # 不可变事件链（参考 Pi-Mono）
```

### 2.3 Decree（批红）— 用户决策反馈

```python
class DecreeAction(str, Enum):
    APPROVE = "approve"         # 准奏
    REJECT = "reject"           # 驳回
    RETRY = "retry"             # 重新执行
    AMEND = "amend"             # 追加指令

class Decree(BaseModel):
    """批红 — 用户对奏折的决策反馈"""
    id: str = Field(default_factory=lambda: ulid.new().str)
    memorial_id: str                            # 关联的奏折 ID
    action: DecreeAction
    comment: str | None = None                  # 批注
    amended_goal: str | None = None             # amend 时的追加指令
    created_at: datetime = Field(default_factory=datetime.now)
```

---

## 三、七大模块详细设计

> 每个模块遵循 ZeroClaw 的 **Trait 可插拔架构**（`src/providers/traits.rs` L257-380）：用 Python Protocol 定义统一接口，实现可替换。

### 模块 1：御案台（Gateway）

#### 职责

接收诏令、校验格式、注入运行时上下文、推入任务队列。是用户与天枢交互的唯一入口。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| NanoBot | `agent/context.py` | 四层 system prompt 构建（Identity → Bootstrap → Memory → Skills） |
| CoPaw | `app/_app.py` + FastAPI 路由 | FastAPI 应用初始化 + lifespan 管理 |
| CoPaw | `app/channels/manager.py` | 多通道入站消息统一接入 |
| Pi-Mono | `packages/mom/src/events.ts` | EventsWatcher 监控 JSON 文件作为任务定义 |

#### Protocol 接口

```python
class GatewayProtocol(Protocol):
    """御案台接口"""
    async def submit_edict(self, edict: Edict) -> str:
        """提交诏令，返回诏令 ID"""
        ...

    async def get_memorial(self, edict_id: str) -> Memorial | None:
        """查询奏折"""
        ...

    async def submit_decree(self, decree: Decree) -> None:
        """提交批红"""
        ...

    async def list_edicts(self, status: str | None = None) -> list[Edict]:
        """列出诏令"""
        ...
```

#### 内部结构

```
gateway/
  __init__.py
  protocol.py          # GatewayProtocol 定义
  api.py               # FastAPI 路由（POST /edicts, GET /memorials）
  cli.py               # CLI 入口（typer）
  context_builder.py   # 四层上下文构建（参考 NanoBot context.py）
  validator.py         # 诏令格式校验
```

#### 交互方式

- **入站**：接收来自 API / CLI / Channel 适配器的原始请求
- **出站**：校验后的 `Edict` 推入 EventBus（`edict.submitted` 事件），由 Scheduler 监听

---

### 模块 2：内阁（Planner）

#### 职责

理解诏令意图，将复杂目标拆解为可独立执行的子任务列表，分派给兵部。**内阁不执行，只规划。**

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| DeepAgents | `middleware/subagents.py` | 上下文隔离分派：`_EXCLUDED_STATE_KEYS` 裁剪策略，防止子任务上下文污染父级 |
| DeepAgents | `middleware/subagents.py` | `write_todos` 工具：LLM 输出结构化子任务列表 |
| DeepAgents | `base_prompt.md` | 规划 Prompt 模板：指导 LLM 何时该并行、何时该串行 |
| NanoBot | `agent/context.py` | 四层 Context 注入，为规划提供充分背景 |

#### Protocol 接口

```python
class SubTask(BaseModel):
    """子任务定义"""
    id: str = Field(default_factory=lambda: ulid.new().str)
    description: str                            # 子任务描述
    depends_on: list[str] = []                  # 依赖的子任务 ID（支持 DAG）
    tools_required: list[str] = []              # 需要的工具列表
    estimated_tokens: int | None = None         # 预估 Token 用量

class PlanResult(BaseModel):
    """规划结果"""
    edict_id: str
    subtasks: list[SubTask]
    reasoning: str                              # 拆解推理过程

class PlannerProtocol(Protocol):
    """内阁接口"""
    async def plan(self, edict: Edict, context: dict | None = None) -> PlanResult:
        """将诏令拆解为子任务列表"""
        ...
```

#### 内部结构

```
planner/
  __init__.py
  protocol.py          # PlannerProtocol 定义
  planner.py           # 默认实现：LLM 驱动的任务拆解
  prompts.py           # 规划 Prompt 模板（参考 DeepAgents base_prompt.md）
  context_filter.py    # 上下文裁剪（参考 DeepAgents _EXCLUDED_STATE_KEYS）
```

#### 上下文隔离策略（参考 DeepAgents）

```python
# 分派子任务时裁剪上下文，防止信息泄漏和上下文膨胀
EXCLUDED_CONTEXT_KEYS = {
    "messages",           # 历史消息不传递
    "previous_results",   # 其他子任务结果不传递
    "internal_state",     # 内部状态不传递
}
```

#### 交互方式

- **入站**：监听 EventBus 的 `edict.scheduled` 事件
- **出站**：发射 `plan.completed` 事件，携带 `PlanResult`，由兵部消费

---

### 模块 3：兵部（Executor）

#### 职责

接收内阁分派的子任务，分配 Agent 执行，管理并发池，处理超时与重试，收集结果组装奏折。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| NanoBot | `agent/subagent.py` | SubagentManager：隔离工具集（排除 message/spawn 防递归）+ `_running_tasks` / `_session_tasks` 双追踪 + 级联取消 `cancel_by_session()` |
| CoPaw | `app/crons/executor.py` | CronExecutor：stream/final 两种分发模式 |
| CoPaw | `app/runner/runner.py` | AgentRunner：主执行引擎 |
| PicoClaw | `pkg/channels/manager.go` L450-557 | Worker 队列：缓冲区大小 16 + 自动重试 3 次 + 指数退避 |
| PicoClaw | `pkg/tools/spawn.go` L62-64 | `ExecuteAsync()` + `AsyncCallback` 非阻塞模式 |
| PicoClaw | `pkg/agent/instance.go` | `resolveAgentWorkspace()` 工作区隔离 + 路径白名单 |
| DeepAgents | `middleware/subagents.py` | 单轮可包含多个 `task` tool_call 并行执行 |

#### Protocol 接口

```python
class ExecutorProtocol(Protocol):
    """兵部接口"""
    async def execute(self, plan: PlanResult, runtime: EdictRuntime) -> list[SubTaskResult]:
        """执行子任务列表，返回结果"""
        ...

    async def cancel(self, edict_id: str) -> None:
        """取消指定诏令的所有执行中子任务（级联取消）"""
        ...

    def get_running_tasks(self) -> dict[str, asyncio.Task]:
        """获取当前运行中的任务"""
        ...
```

#### 内部结构

```
executor/
  __init__.py
  protocol.py          # ExecutorProtocol 定义
  executor.py          # 主执行器：asyncio.Task 池管理
  worker.py            # Worker：单个子任务的执行单元
  tool_filter.py       # 工具权限裁剪（参考 NanoBot 隔离工具集）
  workspace.py         # 工作区隔离（参考 PicoClaw resolveAgentWorkspace）
  retry.py             # 重试策略：指数退避（参考 PicoClaw Worker 队列）
```

#### 核心设计

**1. asyncio Task 池**（参考 NanoBot SubagentManager）

```python
class TaskPool:
    _running: dict[str, asyncio.Task] = {}      # task_id → asyncio.Task
    _semaphore: asyncio.Semaphore                # 并发控制

    async def submit(self, subtask: SubTask, worker: Worker) -> None:
        async with self._semaphore:
            task = asyncio.create_task(worker.run(subtask))
            self._running[subtask.id] = task

    async def cancel_all(self, edict_id: str) -> None:
        """级联取消 — 参考 NanoBot cancel_by_session()"""
        for task_id, task in self._running.items():
            task.cancel()
```

**2. 工具权限裁剪**（参考 NanoBot 隔离工具集）

```python
# 子任务 Agent 使用裁剪后的工具集，防止递归失控
BLOCKED_TOOLS = {"spawn_agent", "submit_edict", "send_message"}

def filter_tools(all_tools: list[Tool], subtask: SubTask) -> list[Tool]:
    allowed = set(subtask.tools_required) if subtask.tools_required else None
    return [
        t for t in all_tools
        if t.name not in BLOCKED_TOOLS
        and (allowed is None or t.name in allowed)
    ]
```

#### 交互方式

- **入站**：监听 EventBus 的 `plan.completed` 事件
- **过程**：每个子任务执行时发射 `task.started` / `task.progress` / `task.completed` / `task.failed` 事件
- **出站**：所有子任务完成后发射 `execution.completed` 事件，携带 `list[SubTaskResult]`

---

### 模块 4：都察院（Auditor）

#### 职责

独立审计执行过程，不参与执行。检查越权调用、目标偏离、结果幻觉、成本超标。输出结构化审计报告。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| ZeroClaw | `src/observability/traits.rs` L9-95 | Observer 事件体系：AgentStart / LlmRequest / LlmResponse / ToolCallStart / ToolCall / Error 六种事件类型 + gauge/counter 度量 |
| NanoBot | `heartbeat/service.py` | 两阶段巡检：Phase 1 读配置 → LLM tool_call 决策（skip/run）→ Phase 2 仅在 run 时执行 |
| CoPaw | `agents/hooks/bootstrap.py` + `memory_compaction.py` | Hook 生命周期注入：`register_instance_hook()` 注册 pre/post reasoning 钩子 |
| CoPaw | `app/runner/query_error_dump.py` | QueryErrorDump：失败时自动写入结构化 JSON（traceback + 请求 + Agent 状态 + 时间戳） |

#### Protocol 接口

```python
class AuditorProtocol(Protocol):
    """都察院接口"""
    async def audit(self, edict: Edict, results: list[SubTaskResult], events: list[dict]) -> AuditResult:
        """审计执行结果"""
        ...

    async def pre_check(self, edict: Edict, plan: PlanResult) -> AuditResult:
        """执行前预审（可选）"""
        ...
```

#### 内部结构

```
auditor/
  __init__.py
  protocol.py          # AuditorProtocol 定义
  auditor.py           # 默认审计器：规则引擎 + LLM 辅助判断
  rules.py             # 内置审计规则（越权检查、Token 超限、目标偏离等）
  hooks.py             # pre/post 钩子注册（参考 CoPaw Hooks）
  error_dump.py        # 结构化错误记录（参考 CoPaw QueryErrorDump）
```

#### 核心设计

**1. EventBus 监听**（参考 ZeroClaw Observer）

```python
# 审计事件类型（参考 ZeroClaw ObserverEvent）
class AuditEventType(str, Enum):
    TASK_STARTED = "task.started"
    TOOL_CALLED = "tool.called"
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    COST_THRESHOLD = "cost.threshold"
```

**2. 两阶段巡检**（参考 NanoBot Heartbeat）

- **Phase 1**：收集事件流 + 执行结果，通过规则引擎做初步筛查
- **Phase 2**：对可疑项用 LLM tool_call 做结构化判断（pass/flag/block），避免自由文本解析

**3. pre/post 钩子**（参考 CoPaw Hooks）

```python
# 在执行前后注入审计逻辑，无需修改执行器代码
@hook(event="task.before_execute")
async def pre_execute_check(edict: Edict, subtask: SubTask) -> bool:
    """检查工具权限、预算余量"""
    ...

@hook(event="task.after_execute")
async def post_execute_audit(subtask: SubTask, result: SubTaskResult) -> AuditResult:
    """检查结果质量、成本合规"""
    ...
```

#### 交互方式

- **入站**：订阅 EventBus 上所有 `task.*` 和 `llm.*` 事件
- **出站**：发射 `audit.completed` 事件，附带 `AuditResult`；严重问题时发射 `audit.alert` 触发通政司告警

---

### 模块 5：通政司（Notifier）

#### 职责

任务状态推送、结果汇报、晨报/日报生成。将 Agent 输出适配为各通道的原生格式。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| CoPaw | `app/channels/manager.py` | ChannelManager：多线程队列 + 每通道可配 worker 数 + 两种防抖（时间防抖 + 会话防抖）|
| CoPaw | `app/channels/renderer.py` | 消息渲染管线：Agent 输出 → 通道无关内容 → 通道原生格式；可配过滤器（隐藏工具细节、过滤思考块、bot 前缀）|
| CoPaw | `app/channels/base.py` | BaseChannel：`dm_policy` / `group_policy` 访问控制（open / allowlist）|
| NanoBot | `bus/` | MessageBus：`asyncio.Queue[InboundMessage]` + `asyncio.Queue[OutboundMessage]` 完全解耦 |
| PicoClaw | `pkg/channels/manager.go` L723-762 | Per-channel 速率限制器（Telegram 20 msg/s, Discord 1 msg/s）+ 缓冲区 + 指数退避重试 |

#### Protocol 接口

```python
class ChannelProtocol(Protocol):
    """通知通道接口"""
    @property
    def name(self) -> str: ...

    async def send(self, message: RenderedMessage) -> None:
        """发送消息"""
        ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

class NotifierProtocol(Protocol):
    """通政司接口"""
    async def notify(self, memorial: Memorial, dispatch: EdictDispatch) -> None:
        """推送奏折到指定通道"""
        ...

    async def broadcast(self, message: str, channels: list[str] | None = None) -> None:
        """广播消息"""
        ...
```

#### 内部结构

```
notifier/
  __init__.py
  protocol.py          # NotifierProtocol + ChannelProtocol 定义
  notifier.py          # 通政司主逻辑
  renderer.py          # 消息渲染管线（参考 CoPaw renderer.py）
  rate_limiter.py      # Per-channel 速率限制（参考 PicoClaw）

channels/
  __init__.py
  base.py              # BaseChannel 抽象（参考 CoPaw base.py）
  console.py           # 控制台输出（Phase 0）
  feishu.py            # 飞书通道
  dingtalk.py          # 钉钉通道
  email.py             # 邮件通道
```

#### 核心设计

**1. MessageBus 解耦**（参考 NanoBot）

```python
class MessageBus:
    """消息总线 — 通道与业务逻辑完全解耦"""
    inbound: asyncio.Queue[InboundMessage]      # 外部 → 天枢
    outbound: asyncio.Queue[OutboundMessage]    # 天枢 → 外部
```

**2. 消息渲染管线**（参考 CoPaw）

```python
class MessageRenderer:
    """Agent 输出 → 通道无关 → 通道原生格式"""
    filter_tool_messages: bool = True           # 隐藏工具调用细节
    filter_thinking: bool = True                # 过滤 LLM 思考过程
    bot_prefix: str = "【天枢】"                # 消息前缀

    def render(self, memorial: Memorial, channel: str) -> RenderedMessage:
        ...
```

**3. 防抖策略**（参考 CoPaw ChannelManager）

- **时间防抖**：合并 X 毫秒内的连续状态更新，避免消息轰炸
- **会话防抖**：持有中间内容，直到最终结果到达后批量发送

#### 交互方式

- **入站**：监听 EventBus 的 `execution.completed` / `audit.completed` / `audit.alert` 事件
- **出站**：调用 Channel 适配器发送消息

---

### 模块 6：文渊阁（Memory）

#### 职责

长期记忆存储、历史任务检索、知识沉淀、经验复用。为内阁的规划和兵部的执行提供知识支撑。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| CoPaw | `agents/memory/memory_manager.py` | ReMeLight 封装：向量搜索 + 全文检索混合、Token 感知自动压缩（保留最近 N 条不压缩，旧消息 LLM 摘要，压缩比 70%）、多后端（auto/local/chroma）|
| NanoBot | `agent/memory.py` | 双层记忆：MEMORY.md（长期事实，LLM 自动归纳）+ HISTORY.md（时间戳日志，可 grep）；未整理数 ≥ window 时触发归纳 |
| ZeroClaw | `src/memory/traits.rs` L54-95 | Memory trait：`store()` / `recall()` / `forget()` / `health_check()`；分类 Core/Daily/Conversation/Custom；会话隔离记忆；多后端 SQLite/PostgreSQL/Qdrant |

#### Protocol 接口

```python
class MemoryCategory(str, Enum):
    CORE = "core"                 # 核心知识（不过期）
    TASK_HISTORY = "task_history"  # 历史任务记录
    EXPERIENCE = "experience"     # 经验总结
    CONVERSATION = "conversation" # 会话记忆
    CUSTOM = "custom"

class MemoryEntry(BaseModel):
    key: str
    content: str
    category: MemoryCategory
    metadata: dict = {}
    created_at: datetime
    expires_at: datetime | None = None

class MemoryProtocol(Protocol):
    """文渊阁接口 — 参考 ZeroClaw Memory trait"""
    async def store(self, key: str, content: str, category: MemoryCategory,
                    metadata: dict | None = None) -> None:
        """存储记忆"""
        ...

    async def recall(self, query: str, category: MemoryCategory | None = None,
                     limit: int = 5) -> list[MemoryEntry]:
        """检索记忆（向量 + 全文混合）"""
        ...

    async def forget(self, key: str) -> None:
        """删除记忆"""
        ...

    async def compact(self) -> None:
        """压缩旧记忆（参考 CoPaw 自动压缩策略）"""
        ...

    async def health_check(self) -> bool:
        """健康检查"""
        ...
```

#### 内部结构

```
memory/
  __init__.py
  protocol.py          # MemoryProtocol 定义
  manager.py           # MemoryManager：统一管理入口
  file_backend.py      # 文件后端（Phase 0：JSONL 存储）
  sqlite_backend.py    # SQLite 后端（Phase 2）
  compactor.py         # 记忆压缩器（参考 CoPaw ReMeLight 自动压缩）
  dual_layer.py        # 双层记忆（参考 NanoBot MEMORY.md + HISTORY.md）
```

#### 核心设计

**智能压缩策略**（参考 CoPaw ReMeLight）

```python
COMPACT_RATIO = 0.7            # 压缩比例
COMPACT_KEEP_RECENT = 3        # 保留最近 N 条不压缩
STORE_BACKEND = "file"         # file → sqlite → vector（渐进升级）
```

#### 交互方式

- **被调用方**：内阁规划时调用 `recall()` 检索相关历史；兵部执行完毕后调用 `store()` 沉淀经验
- **定期维护**：Scheduler 定期触发 `compact()` 压缩旧记忆

---

### 模块 7：户部（CostManager）

#### 职责

Token 预算管理、API 配额追踪、成本累计、预算熔断、Provider 成本路由、资源回收。

#### 参考来源

| 参考项目 | 文件 | 设计点 |
|----------|------|--------|
| ZeroClaw | `src/observability/traits.rs` | TokenUsage：per-request Token 统计（prompt_tokens / completion_tokens / total_tokens）+ gauge/counter 度量 |
| CoPaw | `agents/routing_chat_model.py` | RoutingChatModel：多 Provider 注册 + 按成本/能力路由（便宜模型做简单任务，贵模型做复杂任务）|
| CoPaw | `providers/provider_manager.py` | Provider 注册 + 模型能力列表查询 |
| PicoClaw | `pkg/channels/manager.go` L723-762 | TTL Janitor：10 秒轮询清理过期资源，防止内存泄漏 |
| ZeroClaw | `src/providers/traits.rs` L257-380 | Provider `capabilities()` 能力自报告 + 不支持特性自动降级 |

#### Protocol 接口

```python
class TokenUsage(BaseModel):
    """Token 用量 — 参考 ZeroClaw TokenUsage"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0

class CostManagerProtocol(Protocol):
    """户部接口"""
    async def record_usage(self, edict_id: str, usage: TokenUsage) -> None:
        """记录用量"""
        ...

    async def check_budget(self, edict_id: str) -> bool:
        """检查是否超预算（超预算返回 False，触发熔断）"""
        ...

    async def get_summary(self, edict_id: str | None = None) -> dict:
        """获取成本汇总"""
        ...

    async def select_provider(self, task_complexity: str) -> str:
        """根据任务复杂度选择最优 Provider（参考 CoPaw RoutingChatModel）"""
        ...
```

#### 内部结构

```
cost/
  __init__.py
  protocol.py          # CostManagerProtocol 定义
  manager.py           # 成本管理器
  budget.py            # 预算检查 + 熔断逻辑
  tracker.py           # Per-task 成本累计
  janitor.py           # 资源回收（参考 PicoClaw TTL Janitor）
```

#### 核心设计

**预算熔断机制**

```python
async def check_budget(self, edict_id: str) -> bool:
    usage = await self.get_summary(edict_id)
    edict = await self.get_edict(edict_id)

    # Token 熔断
    if edict.runtime.max_tokens and usage["total_tokens"] >= edict.runtime.max_tokens:
        await self.event_bus.emit("cost.budget_exceeded", {"edict_id": edict_id, "type": "tokens"})
        return False

    # 成本熔断
    if edict.runtime.max_cost and usage["total_cost"] >= edict.runtime.max_cost:
        await self.event_bus.emit("cost.budget_exceeded", {"edict_id": edict_id, "type": "cost"})
        return False

    return True
```

#### 交互方式

- **入站**：监听 EventBus 的 `llm.response` 事件，提取 Token 用量
- **出站**：预算超限时发射 `cost.budget_exceeded` 事件，兵部收到后取消执行
- **被调用方**：兵部每次 LLM 调用前调用 `check_budget()`；内阁规划时调用 `select_provider()`

---

## 四、基础设施层

### 4.1 EventBus（事件总线）

> **参考**：Pi-Mono EventStream（`packages/agent/src/agent-loop.ts`，事件发射与结果收集解耦）+ ZeroClaw Observer（`src/observability/traits.rs`，统一事件类型 + 多后端输出）

```python
class EventBus:
    """统一事件发射/监听 — 天枢的神经系统"""
    _handlers: dict[str, list[Callable]]

    async def emit(self, event_type: str, payload: dict) -> None:
        """发射事件"""
        ...

    def on(self, event_type: str, handler: Callable) -> None:
        """注册事件监听器"""
        ...

    def off(self, event_type: str, handler: Callable) -> None:
        """移除事件监听器"""
        ...
```

**事件类型全表：**

| 事件 | 生产者 | 消费者 | 说明 |
|------|--------|--------|------|
| `edict.submitted` | 御案台 | 调度器 | 新诏令入队 |
| `edict.scheduled` | 调度器 | 内阁 | 诏令被调度触发 |
| `plan.completed` | 内阁 | 兵部 | 规划完成，子任务列表就绪 |
| `task.started` | 兵部 | 都察院 | 子任务开始执行 |
| `task.progress` | 兵部 | 都察院、通政司 | 执行进度更新 |
| `task.completed` | 兵部 | 都察院 | 子任务完成 |
| `task.failed` | 兵部 | 都察院、通政司 | 子任务失败 |
| `llm.request` | 兵部 | 户部、都察院 | LLM 调用请求 |
| `llm.response` | 兵部 | 户部、都察院 | LLM 调用响应（含 token 用量）|
| `tool.called` | 兵部 | 都察院 | 工具被调用 |
| `execution.completed` | 兵部 | 都察院、通政司 | 所有子任务完成 |
| `audit.completed` | 都察院 | 通政司 | 审计完成 |
| `audit.alert` | 都察院 | 通政司 | 审计告警（严重问题）|
| `cost.budget_exceeded` | 户部 | 兵部 | 预算超限，触发熔断 |
| `memorial.ready` | 通政司 | 文渊阁 | 奏折生成完毕，可沉淀 |

### 4.2 Scheduler（调度器）

> **参考**：NanoBot Cron 三模式（`cron/service.py`，at/every/cron + `_get_next_wake_ms()` 高效调度）+ CoPaw APScheduler（`app/crons/manager.py`，并发/超时/misfire 控制）+ ZeroClaw Cron（`src/cron/scheduler.rs` L22-49，健康检查 `mark_component_ok()` + 指数退避 200ms→30s + 安全策略违规不重试 + `max_concurrent` 并发限制 + 最小轮询 5s）

```python
class SchedulerProtocol(Protocol):
    async def schedule(self, edict: Edict) -> str:
        """注册调度任务，返回 job ID"""
        ...

    async def cancel(self, job_id: str) -> None:
        """取消调度任务"""
        ...

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

```
scheduler/
  __init__.py
  protocol.py          # SchedulerProtocol 定义
  scheduler.py         # APScheduler 封装 + 三模式支持
  health.py            # 健康检查（参考 ZeroClaw mark_component_ok）
```

### 4.3 ConfigManager（配置管理）

> **参考**：CoPaw ConfigWatcher（`config/watcher.py`，文件变更检测 → 异步热更新无需重启）+ NanoBot Pydantic 配置（`config/schema.py`，20+ Provider 配置）

```
config/
  __init__.py
  schema.py            # Pydantic Settings 配置模型
  watcher.py           # 文件变更监控 + 热重载（参考 CoPaw）
  defaults.py          # 默认配置
```

### 4.4 ProviderManager（LLM Provider 管理）

> **参考**：ZeroClaw Provider trait（`src/providers/traits.rs` L257-380，`capabilities()` 能力自报告 + 不支持特性自动降级）+ CoPaw 多 Provider 注册（`providers/provider_manager.py`，注册 + 模型列表查询）

```python
class ProviderCapabilities(BaseModel):
    """Provider 能力声明 — 参考 ZeroClaw"""
    native_tool_calling: bool = False
    vision: bool = False
    streaming: bool = True
    max_tokens: int = 4096
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0

class ProviderProtocol(Protocol):
    """LLM Provider 接口 — 参考 ZeroClaw Provider trait"""
    def capabilities(self) -> ProviderCapabilities: ...
    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict: ...
```

```
providers/
  __init__.py
  protocol.py          # ProviderProtocol 定义
  manager.py           # Provider 注册 + 路由（参考 CoPaw）
  litellm_provider.py  # LiteLLM 统一 Provider 实现
```

---

## 五、Python 包结构

```
src/tianshu/
  __init__.py
  app.py                # 应用入口 + lifespan 管理

  models/               # ===== 数据模型 =====
    __init__.py
    edict.py            # 诏令（Edict）
    memorial.py         # 奏折（Memorial）
    decree.py           # 批红（Decree）
    events.py           # 事件类型定义

  gateway/              # ===== 御案台 =====
    __init__.py
    protocol.py
    api.py              # FastAPI 路由
    cli.py              # CLI 入口
    context_builder.py  # 上下文构建
    validator.py        # 诏令校验

  planner/              # ===== 内阁 =====
    __init__.py
    protocol.py
    planner.py          # LLM 任务拆解
    prompts.py          # 规划 Prompt
    context_filter.py   # 上下文裁剪

  executor/             # ===== 兵部 =====
    __init__.py
    protocol.py
    executor.py         # 主执行器
    worker.py           # 执行单元
    tool_filter.py      # 工具权限裁剪
    workspace.py        # 工作区隔离
    retry.py            # 重试策略

  auditor/              # ===== 都察院 =====
    __init__.py
    protocol.py
    auditor.py          # 审计器
    rules.py            # 审计规则
    hooks.py            # pre/post 钩子
    error_dump.py       # 错误记录

  notifier/             # ===== 通政司 =====
    __init__.py
    protocol.py
    notifier.py         # 通知管理
    renderer.py         # 消息渲染
    rate_limiter.py     # 速率限制

  memory/               # ===== 文渊阁 =====
    __init__.py
    protocol.py
    manager.py          # 记忆管理
    file_backend.py     # 文件后端
    sqlite_backend.py   # SQLite 后端
    compactor.py        # 记忆压缩
    dual_layer.py       # 双层记忆

  cost/                 # ===== 户部 =====
    __init__.py
    protocol.py
    manager.py          # 成本管理
    budget.py           # 预算熔断
    tracker.py          # 成本追踪
    janitor.py          # 资源回收

  bus/                  # ===== 事件总线 =====
    __init__.py
    event_bus.py        # EventBus
    message_bus.py      # MessageBus（通道解耦）

  scheduler/            # ===== 调度器 =====
    __init__.py
    protocol.py
    scheduler.py        # APScheduler 封装
    health.py           # 健康检查

  config/               # ===== 配置管理 =====
    __init__.py
    schema.py           # 配置模型
    watcher.py          # 热重载
    defaults.py         # 默认值

  providers/            # ===== LLM Provider =====
    __init__.py
    protocol.py
    manager.py          # 注册 + 路由
    litellm_provider.py # LiteLLM 实现

  channels/             # ===== 通知通道 =====
    __init__.py
    base.py             # BaseChannel
    console.py          # 控制台
    feishu.py           # 飞书
    dingtalk.py         # 钉钉
    email.py            # 邮件

  tools/                # ===== 内置工具 =====
    __init__.py
    registry.py         # 工具注册
    web_search.py       # 网页搜索
    file_ops.py         # 文件操作
    shell.py            # Shell 执行

  skills/               # ===== Skills =====
    __init__.py
    loader.py           # Skill 加载器
    builtin/            # 内置 Skills
```

---

## 六、Phase 0 MVP 范围

### 6.1 目标

**最小闭环：证明"下旨 → 办差 → 递折"能跑通。**

```
用户 CLI 输入 → 御案台接收 → 内阁拆解 → 兵部单 Agent 执行 → 控制台输出结果
```

### 6.2 模块实现范围

| 模块 | Phase 0 范围 | 简化策略 |
|------|-------------|---------|
| **御案台** | CLI 入口 + 基础校验 | 暂无 API / Channel 接入 |
| **内阁** | LLM 任务拆解 | 单轮规划，无 DAG 依赖 |
| **兵部** | 单 Agent + 基础工具 | 无并发池，顺序执行子任务 |
| **都察院** | ❌ 不实现 | Phase 1 加入 |
| **通政司** | 控制台输出 | 仅 ConsoleChannel |
| **文渊阁** | ❌ 不实现 | Phase 2 加入 |
| **户部** | 基础 Token 统计 | 仅统计，无熔断 |
| **EventBus** | 基础实现 | 内存 dict + asyncio |
| **Scheduler** | 仅即时模式 | 无定时/周期调度 |
| **Config** | Pydantic Settings | 环境变量 + YAML |
| **Provider** | LiteLLM 单 Provider | 无路由 |

### 6.3 Phase 0 技术栈

```
Python 3.12+ / asyncio / Pydantic v2 / LiteLLM / typer (CLI)
存储：本地 JSON/JSONL 文件
```

### 6.4 Phase 0 交付物

1. `tianshu` CLI 工具，支持 `tianshu run "任务描述"` 命令
2. 任务自动拆解为子任务并顺序执行
3. 执行结果在控制台输出
4. Token 用量统计输出

### 6.5 后续 Phase 规划

| Phase | 新增模块 | 关键能力 |
|-------|---------|---------|
| **Phase 1**（2-4 周） | 都察院 + 通政司增强 | 自动审计 + 飞书/钉钉通知推送 |
| **Phase 2**（1-2 月） | 文渊阁 + 户部增强 | 历史检索 + 预算熔断 + 定时/周期调度 |
| **Phase 3**（2-3 月） | 多 Agent 协作 + API 网关 | 并发执行 + DAG 依赖 + FastAPI + Temporal |

---

## 七、模块间交互流程图

### 7.1 完整的"下旨 → 办差 → 递折"流程

```
用户
 │
 │ ① 下发诏令（自然语言任务）
 ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 御案台 (Gateway)                                                     │
│  · 校验诏令格式（validator.py）                                       │
│  · 构建运行时上下文（context_builder.py，参考 NanoBot 四层 Context）    │
│  · 发射 edict.submitted 事件                                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼ edict.submitted
┌─────────────────────────────────────────────────────────────────────┐
│ 调度器 (Scheduler)                                                   │
│  · 即时任务：立即触发                                                  │
│  · 定时任务：注册 APScheduler job（参考 CoPaw + NanoBot 三模式）        │
│  · 周期任务：注册 cron 表达式                                          │
│  · 发射 edict.scheduled 事件                                         │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼ edict.scheduled
┌─────────────────────────────────────────────────────────────────────┐
│ 内阁 (Planner)                                                       │
│  · 检索文渊阁历史经验（memory.recall()）                               │
│  · LLM 理解意图 + 拆解子任务（参考 DeepAgents write_todos）            │
│  · 上下文裁剪（参考 DeepAgents _EXCLUDED_STATE_KEYS）                  │
│  · 户部查询成本路由（cost.select_provider()）                          │
│  · 发射 plan.completed 事件                                          │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼ plan.completed
                   ┌─────────┴─────────┐
                   │                   │
                   ▼                   ▼
┌──────────────────────────┐  ┌──────────────────────────┐
│ 都察院 (Auditor)          │  │ 兵部 (Executor)           │
│  · pre_check() 预审规划   │  │  · 分配 Agent 到子任务    │
│  （参考 NanoBot Heartbeat │  │  · 工具权限裁剪            │
│   两阶段决策）            │  │   （参考 NanoBot 隔离工具集）│
└──────────────────────────┘  │  · asyncio Task 池并发执行  │
                              │  · 超时控制 + 指数退避重试   │
              ┌───────────────│   （参考 PicoClaw Worker）  │
              │               │  · 每步发射 task.* 事件      │
              ▼               └──────────┬─────────────────┘
┌──────────────────────────┐             │
│ 都察院 (Auditor)          │             │ task.* 事件
│  · 订阅 EventBus 实时监听 │◀────────────┘
│  （参考 ZeroClaw Observer）│
│  · 规则引擎初筛            │             ▼ execution.completed
│  · LLM tool_call 判断     │  ┌──────────────────────────┐
│  · 结构化错误记录          │  │ 户部 (CostManager)        │
│  （参考 CoPaw ErrorDump）  │  │  · 累计 Token 用量        │
│  · 发射 audit.completed   │  │  · 预算检查 → 熔断         │
└────────────┬─────────────┘  │  （参考 ZeroClaw TokenUsage）│
             │                └──────────────────────────┘
             ▼ audit.completed
┌─────────────────────────────────────────────────────────────────────┐
│ 通政司 (Notifier)                                                    │
│  · 组装奏折（Memorial）                                               │
│  · 消息渲染管线（参考 CoPaw MessageRenderer）                          │
│  · 防抖合并（参考 CoPaw ChannelManager 时间防抖/会话防抖）              │
│  · Per-channel 限速（参考 PicoClaw 速率限制器）                        │
│  · 推送到用户指定通道                                                  │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼ memorial.ready
┌─────────────────────────────────────────────────────────────────────┐
│ 文渊阁 (Memory)                                                      │
│  · 沉淀任务记录（memory.store()）                                     │
│  · 自动压缩旧记忆（参考 CoPaw ReMeLight 70% 压缩比）                  │
│  · 归纳经验总结（参考 NanoBot MEMORY.md 自动归纳）                     │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
用户查阅奏折 → 下达批红（Decree）→ 御案台 → 循环
```

### 7.2 异常处理流程

```
兵部执行中发生异常
 │
 ├─ 超时 → 任务取消（级联取消，参考 NanoBot cancel_by_session）
 ├─ Token 超限 → 户部发射 cost.budget_exceeded → 兵部熔断
 ├─ 工具调用失败 → 指数退避重试（参考 PicoClaw，max 3 次）
 ├─ 安全策略违规 → 不重试，直接标记失败（参考 ZeroClaw）
 └─ 未知错误 → QueryErrorDump 记录（参考 CoPaw）→ 都察院审计 → 通政司告警
```

---

## 附录：参考项目索引

| 缩写 | 项目 | 语言 | 梯队 | 核心借鉴 |
|------|------|------|------|---------|
| NanoBot | nanobot | Python | 第一 | SubagentManager、Cron 三模式、Heartbeat 两阶段、四层 Context、双层记忆、MessageBus |
| DeepAgents | deepagents | Python | 第一 | 上下文隔离分派、_EXCLUDED_STATE_KEYS、并行 task、中间件栈、BackendProtocol |
| CoPaw | copaw | Python | 第一 | ChannelManager 防抖、渲染管线、CronJobSpec、ReMeLight 压缩、Hooks、ConfigWatcher、RoutingChatModel、QueryErrorDump |
| PicoClaw | picoclaw | Go | 第二 | Worker 队列、Per-channel 限速、工作区隔离、TTL Janitor |
| ZeroClaw | zeroclaw | Rust | 第二 | Provider/Memory/Observer trait 可插拔架构、TokenUsage、Cron 健康检查+并发控制 |
| Pi-Mono | pi-mono | TypeScript | 第二 | 三种事件类型、不可变消息链、EventStream、Agent Loop 双模式 |
