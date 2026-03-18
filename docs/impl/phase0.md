# Phase 0 实现文档

> 分支: `feat_phase0` | 版本: `0.1.0` | 快照日期: 2026-03-19

## 1. 项目概览

**Tianshu（天枢）** 是一个异步 AI 执行平台，接收用户下达的"敕令"（Edict），通过 Agent ReAct 循环调用 LLM + 工具完成任务，并将执行过程记录为"奏折"（Memorial）。

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115+ / Python 3.12+ |
| LLM 接入 | LiteLLM（统一 OpenAI 兼容接口） |
| 重试策略 | tenacity |
| 数据验证 | Pydantic v2 / pydantic-settings |
| 持久化 | SQLite（WAL 模式） |
| CLI | Typer + Rich |
| 前端 | React + TypeScript + Ant Design + Vite |
| ID 生成 | ULID（时间有序） |
| 技能解析 | python-frontmatter |

### 依赖一览（pyproject.toml）

```
pydantic>=2.0, pydantic-settings>=2.0, python-ulid>=2.0
fastapi>=0.115, uvicorn[standard]>=0.30
litellm>=1.50, tenacity>=8.0
pyyaml>=6.0, python-frontmatter>=1.0
httpx>=0.27, jsonschema>=4.0
CLI: typer[all]>=0.12, rich>=13.0
```

---

## 2. 目录结构

### 后端 `src/tianshu/`

```
src/tianshu/
├── app.py              # FastAPI 工厂 + lifespan 生命周期
├── agent.py            # Agent ReAct 执行引擎
├── llm.py              # LiteLLM 封装 + 重试
├── models.py           # Pydantic 模型（Edict / Memorial / Config / API 信封）
├── config.py           # TianshuSettings 环境变量
├── config_manager.py   # 运行时多 LLM 配置管理（线程安全、不可变状态）
├── gateway.py          # API 路由（Edict / Memorial / Config 端点）
├── storage.py          # SQLite 持久层（CRUD + 事件追加）
├── web.py              # 前端静态文件挂载 + SPA fallback
├── cli/
│   ├── main.py         # Typer CLI 入口
│   ├── client.py       # HTTP 客户端封装（httpx）
│   └── commands/
│       ├── edict.py    # 敕令管理命令
│       ├── memorial.py # 奏折查看命令
│       ├── config.py   # LLM 配置管理命令
│       └── health.py   # 健康检查
├── tools/
│   ├── __init__.py     # 公共导出
│   ├── types.py        # ToolResult + ToolHook Protocol
│   ├── registry.py     # ToolRegistry + ToolDefinition
│   ├── builtins.py     # 内置工具注册入口
│   ├── path_utils.py   # 路径沙箱（防逃逸）
│   ├── edit_file.py    # edit_file 工具
│   ├── find_files.py   # find_files 工具
│   ├── grep.py         # grep 工具（rg 优先，Python 回退）
│   └── list_dir.py     # list_dir 工具
└── skills/
    ├── loader.py       # SKILL.md 发现 + frontmatter 解析 + 预算控制
    └── builtin/        # 内置技能目录
```

### 前端 `web/src/`

```
web/src/
├── App.tsx                         # 路由定义
├── main.tsx                        # 入口
├── api/
│   ├── client.ts                   # axios 封装
│   ├── types.ts                    # TypeScript 类型定义
│   ├── edicts.ts                   # Edict API
│   ├── memorials.ts                # Memorial API
│   ├── config.ts                   # Config API
│   └── health.ts                   # Health API
├── components/
│   ├── common/                     # GlowCard, HealthDot, MonoText, PageContainer
│   ├── edict/                      # EdictForm, EdictTable, StatusTag
│   ├── memorial/                   # EventTimeline, MemorialCard, UsageDisplay
│   └── layout/                     # AppHeader, AppLayout, AppSidebar
├── hooks/
│   ├── useConfig.ts                # 配置 hooks
│   ├── useEdictDetail.ts           # 敕令详情 hooks
│   ├── useHealth.ts                # 健康检查 hooks
│   └── useTheme.ts                 # 主题切换
├── pages/
│   ├── EdictCreatePage.tsx         # 创建敕令
│   ├── EdictDetailPage.tsx         # 敕令详情（含奏折 + 事件时间线）
│   └── EdictListPage.tsx           # 敕令列表
├── theme/
│   └── index.ts                    # Ant Design 主题定制
└── utils/
    ├── constants.ts                # 常量
    └── format.ts                   # 格式化工具
```

---

## 3. 启动与生命周期

### FastAPI 工厂 (`app.py`)

```
create_app()
  ├── FastAPI(lifespan=lifespan)
  ├── CORSMiddleware（allow_origins=["*"]）
  ├── gateway_router → /api
  ├── GET /health
  └── mount_web()（条件挂载前端静态文件）
```

### Lifespan 初始化顺序

```
lifespan(app) → yield → shutdown
  1. TianshuSettings()         ← 环境变量 / .env
  2. Storage(db_path).init_db() ← SQLite 建表 + 迁移
  3. ToolRegistry + register_builtins() ← 7 个内置工具
  4. SkillsLoader(builtin_dir, workspace_dir) ← 技能发现
  5. ConfigManager(initial_state, agent_config) ← LLM 配置
  6. Agent(config_manager, tools, skills) ← 执行引擎
  7. app.state.running_tasks = set() ← 异步任务追踪
  ---
  shutdown:
  8. agent.request_shutdown() ← 设置停止信号
  9. cancel + gather running_tasks
  10. storage.close()
```

### CLI 入口

```
pyproject.toml: tianshu = "tianshu.cli.main:app"

Typer 子命令:
  tianshu edict    → submit / get / list
  tianshu memorial → get / list
  tianshu config   → list / get / add / set / rm / activate
  tianshu health   → 健康检查
```

CLI 通过 `httpx` 调用 `/api` 端点，默认连接 `http://localhost:8000`（可通过 `TIANSHU_API_URL` 环境变量覆盖）。

---

## 4. 核心模型

### 领域模型（`models.py`）

| 模型 | 用途 | 关键字段 |
|------|------|----------|
| `Edict` | 敕令（用户任务） | id(ULID), title, goal, context, status(EdictStatus), created_at |
| `Memorial` | 奏折（执行记录） | id(ULID), edict_id, instruction, status(TaskStatus), summary, result, usage(UsageSummary), error, started_at, completed_at |
| `UsageSummary` | Token 消耗统计 | prompt_tokens, completion_tokens, total_tokens |

### 枚举

| 枚举 | 值 |
|------|-----|
| `EdictStatus` | open, completed, cancelled |
| `TaskStatus` | submitted, running, completed, failed, cancelled |

### 请求/响应模型

| 模型 | 用途 |
|------|------|
| `EdictCreateRequest` | 创建敕令（goal 必填） |
| `FollowUpRequest` | 追加指令（instruction 必填） |
| `EdictUpdateRequest` | 编辑敕令（title/goal/context 可选） |
| `EdictStatusUpdateRequest` | 更新敕令状态 |
| `LLMConfig` | LLM 配置展示（api_key 脱敏） |
| `LLMConfigCreateRequest` | 创建 LLM 配置 |
| `LLMConfigUpdateRequest` | 更新 LLM 配置 |
| `LLMConfigListResponse` | 配置列表 + 当前活跃名称 |
| `AgentConfig` | Agent 参数展示 |
| `AgentConfigUpdateRequest` | 更新 Agent 参数 |
| `ApiResponse[T]` | 统一 API 信封（success, data, error, metadata） |

---

## 5. Agent 执行引擎

### ReAct 循环 (`agent.py`)

```
Agent.execute(edict, on_event, history, user_content)
  │
  ├── 读取 ConfigManager.state → LLMConfigState
  │   └── 如果 enabled=False → 立即返回 FAILED
  │
  ├── 创建 LLMClient（每次执行取最新配置）
  ├── _build_system_prompt(edict, skills_char_budget)
  │   └── 拼接: 身份定义 + 技能文本 + 当前任务 ID
  │
  ├── 构建 messages: [system, *history, user]
  │
  └── for iteration in range(max_iterations):
      ├── 检查 shutdown_event
      ├── emit("iteration.started")
      ├── llm.chat(messages, tools=openai_tools)
      ├── 累加 usage
      │
      ├── if tool_calls:
      │   ├── 追加 assistant message（含 tool_calls）
      │   └── for each tool_call:
      │       ├── tools.execute(name, args)
      │       ├── 截断 content > 8000 字符
      │       ├── 追加 tool message
      │       └── emit("tool.completed" / "tool.failed")
      │
      └── else（无 tool_calls = 最终回答）:
          └── return AgentResult(COMPLETED, summary, result, usage, events)
```

### AgentResult

```python
class AgentResult(BaseModel):
    status: TaskStatus          # COMPLETED / FAILED / CANCELLED
    summary: str | None
    result: str | None
    usage: UsageSummary
    error: str | None
    events: list[dict]          # 执行过程中的所有事件
```

---

## 6. LLM 客户端

### LiteLLM 封装 (`llm.py`)

```python
class LLMClient:
    async def chat(messages, tools) -> LLMResponse
```

**Model Routing 规则**：
- 当 `api_base` 非空且 model 不含 `/` 时，自动添加 `openai/` 前缀
- 这使得自定义 OpenAI 兼容端点（如 vLLM、Ollama）无需手动指定 provider

**重试策略**（tenacity）：
- 指数退避: min=1s, max=4s
- 最多重试 3 次
- 仅对可恢复错误重试: `RateLimitError`, `Timeout`, `ServiceUnavailableError`

### LLMResponse

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] | None   # [{id, name, args}]
    usage: UsageSummary
    reasoning_content: str | None    # 支持 reasoning model 输出
```

---

## 7. 配置管理

### 环境配置 (`config.py`)

```python
class TianshuSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIANSHU_", env_file=".env")
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TIANSHU_LLM_MODEL` | gpt-4o-mini | 默认 LLM 模型 |
| `TIANSHU_LLM_API_KEY` | "" | API 密钥 |
| `TIANSHU_LLM_API_BASE` | "" | 自定义 API 端点 |
| `TIANSHU_LLM_MAX_RETRIES` | 3 | 最大重试次数 |
| `TIANSHU_LLM_TEMPERATURE` | 0.7 | 温度 |
| `TIANSHU_LLM_TOP_P` | 1.0 | Top-P 采样 |
| `TIANSHU_LLM_MAX_TOKENS` | 4096 | 最大生成 Token |
| `TIANSHU_AGENT_MAX_ITERATIONS` | 20 | ReAct 最大迭代数 |
| `TIANSHU_AGENT_TIMEOUT_SECONDS` | 300 | 单次执行超时 |
| `TIANSHU_DB_PATH` | .tianshu/tianshu.db | SQLite 数据库路径 |
| `TIANSHU_HOST` | 0.0.0.0 | 监听地址 |
| `TIANSHU_PORT` | 8000 | 监听端口 |
| `TIANSHU_WORKSPACE_DIR` | . | 工具操作的工作目录 |
| `TIANSHU_SKILLS_CHAR_BUDGET` | 30000 | 技能注入字符预算 |
| `TIANSHU_STATIC_DIR` | /app/static | 前端静态文件目录 |

### 运行时配置 (`config_manager.py`)

`ConfigManager` 管理多份 LLM 配置，支持运行时热切换，**无需重启服务**。

**设计要点**：
- `LLMConfigState` 和 `AgentConfigState` 均为 `frozen=True` 的 dataclass（不可变）
- 更新操作通过创建新实例替换旧值
- 线程安全：所有操作通过 `threading.Lock` 保护
- API Key 脱敏：`mask_api_key()` 仅保留前后 4 位

**核心方法**：

| 方法 | 说明 |
|------|------|
| `state` | 获取当前活跃的 LLM 配置 |
| `list_configs()` | 列出所有配置 + 活跃名称 |
| `add_config(state)` | 添加新配置（名称不可重复） |
| `update_config(name, **kwargs)` | 更新指定配置 |
| `delete_config(name)` | 删除配置（不可删除活跃/最后一个） |
| `set_active(name)` | 切换活跃配置 |
| `agent_config` | 获取 Agent 参数 |
| `update_agent_config(**kwargs)` | 更新 Agent 参数 |

---

## 8. 存储层

### SQLite Schema (`storage.py`)

```sql
-- 敕令表
CREATE TABLE edicts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    goal TEXT NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

-- 奏折表
CREATE TABLE memorials (
    id TEXT PRIMARY KEY,
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
    instruction TEXT,
    status TEXT NOT NULL,
    summary TEXT,
    result TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

-- 事件表（审计日志）
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
    memorial_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- 索引
CREATE INDEX idx_memorials_edict_id ON memorials(edict_id);
CREATE INDEX idx_events_edict_id ON events(edict_id);
```

**PRAGMA 设置**：
- `journal_mode=WAL` — 写前日志，提升并发读写性能
- `foreign_keys=ON` — 启用外键约束

**迁移机制**：
```python
migrations = [
    "ALTER TABLE edicts ADD COLUMN status ...",
    "ALTER TABLE memorials ADD COLUMN instruction ...",
    "ALTER TABLE edicts ADD COLUMN title ...",
]
```
采用顺序执行 + `duplicate column name` 异常捕获实现幂等迁移。

**线程安全**：所有 CRUD 操作通过 `threading.Lock` + `with self._conn` 上下文管理器保护。

---

## 9. API 路由

所有端点挂载在 `/api` 前缀下。统一响应信封：`ApiResponse { success, data, error, metadata }`。

### Edict 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/edicts` | 创建敕令 → 立即返回 202，后台启动 Agent |
| `GET` | `/api/edicts` | 列出敕令（支持 status/search/limit/offset） |
| `GET` | `/api/edicts/{id}` | 获取单个敕令 |
| `PATCH` | `/api/edicts/{id}` | 编辑敕令（仅 open 状态可编辑） |
| `DELETE` | `/api/edicts/{id}` | 删除敕令（仅 open/cancelled 可删除） |
| `PATCH` | `/api/edicts/{id}/status` | 更新敕令状态（结案） |
| `POST` | `/api/edicts/{id}/follow-up` | 追加指令（携带历史对话上下文） |
| `GET` | `/api/edicts/{id}/events` | 获取敕令的事件时间线 |

### Memorial 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/edicts/{id}/memorial` | 获取敕令的最新奏折 |
| `GET` | `/api/edicts/{id}/memorials` | 获取敕令的全部奏折 |
| `GET` | `/api/memorials` | 列出所有奏折（支持 status/limit/offset） |
| `GET` | `/api/memorials/{id}` | 获取单个奏折 |

### Config 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/config` | 获取当前活跃 LLM 配置（legacy） |
| `PUT` | `/api/config` | 更新当前活跃 LLM 配置（legacy） |
| `GET` | `/api/configs` | 列出所有 LLM 配置 + 活跃名称 |
| `POST` | `/api/configs` | 创建新 LLM 配置 |
| `PUT` | `/api/configs/{name}` | 更新指定 LLM 配置 |
| `DELETE` | `/api/configs/{name}` | 删除指定 LLM 配置 |
| `PUT` | `/api/configs/{name}/activate` | 切换活跃配置 |
| `GET` | `/api/agent-config` | 获取 Agent 参数 |
| `PUT` | `/api/agent-config` | 更新 Agent 参数 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |

### 异步任务执行模式

```
POST /api/edicts → 创建 Edict + Memorial → asyncio.create_task(_run_agent)
                                              ↓
                                    Memorial 状态: SUBMITTED → RUNNING → COMPLETED/FAILED
                                              ↓
                                    事件追加到 events 表
```

`_run_agent` 包装了 `Agent.execute()`，处理超时、取消和异常，并在 `finally` 中更新 Memorial 状态。

---

## 10. 工具系统

### ToolRegistry (`tools/registry.py`)

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict        # JSON Schema
    tier: int = 0           # T0-T3（Phase 0 仅标记，无运行时拦截）

class ToolRegistry:
    register(name, func, definition)   # 注册工具
    get_openai_tools() -> list[dict]   # 导出为 OpenAI function calling 格式
    execute(name, args) -> ToolResult  # 执行工具（含参数验证 + hook 链）
```

**执行流程**：
1. 查找工具（不存在 → 返回 error_result）
2. JSON 解析参数（如果是 string）
3. JSON Schema 验证参数（jsonschema）
4. Before hooks → 执行工具函数 → After hooks
5. 返回 `ToolResult`

### ToolResult (`tools/types.py`)

```python
@dataclass(frozen=True)
class ToolResult:
    content: str                    # 工具输出文本
    details: dict | None = None     # 元数据（exit_code, truncated 等）
    is_error: bool = False          # 是否为错误

class ToolHook(Protocol):
    before_tool_call(name, args) -> dict | None    # 修改参数或 None
    after_tool_call(name, args, result) -> ToolResult | None  # 修改结果或 None
```

### 7 个内置工具

| 工具 | Tier | 说明 |
|------|------|------|
| `read_file` | T0 | 读取文件内容（截断 10KB） |
| `list_dir` | T0 | 列出目录内容（上限 500 条） |
| `find_files` | T0 | 按 glob 模式搜索文件（上限 1000 条） |
| `grep` | T0 | 内容搜索（优先 ripgrep，回退 Python re） |
| `write_file` | T1 | 写入文件（自动创建父目录） |
| `edit_file` | T1 | 精确文本替换（唯一匹配 + unified diff） |
| `shell_exec` | T2 | 执行 shell 命令（60s 超时，截断 2KB） |

**安全措施**：
- `safe_path()` (`path_utils.py`)：所有路径操作经过沙箱检查，防止目录逃逸
- `shell_exec` 超时 60 秒，输出截断 2000 字符
- `read_file` 截断 10000 字符
- `grep` 搜索超时 30 秒
- 跳过 `.git`, `__pycache__`, `node_modules`, `.venv` 等目录

---

## 11. 技能系统

### SkillsLoader (`skills/loader.py`)

**发现机制**：扫描目录中的子文件夹，查找 `SKILL.md` 文件。

```
skills/
└── my-skill/
    └── SKILL.md    ← frontmatter + markdown 内容
```

**加载优先级**：
1. 内置技能（`src/tianshu/skills/builtin/`）— 低优先
2. 工作区技能（`{workspace}/skills/`）— 高优先，同名覆盖

**预算控制**：
- 默认 `char_budget = 30000` 字符
- 按顺序拼接技能内容，超过预算则截断
- 可通过 Agent Config API 运行时调整

**Frontmatter 要求检查**：
```yaml
---
metadata:
  openclaw:
    always: false          # true 则跳过所有检查
    requires:
      bins: [rg, fd]       # 全部必须存在
      anyBins: [npm, yarn] # 至少一个存在
      env: [API_KEY]       # 全部必须设置
    os: [linux, darwin]    # 操作系统白名单
---
```

**注入方式**：技能文本在每次 Agent 执行时拼接到 system prompt 中。

---

## 12. CLI 命令

### edict 子命令

| 命令 | 说明 |
|------|------|
| `tianshu edict submit -g "目标" [-c "上下文"]` | 提交敕令 |
| `tianshu edict get <id>` | 查看敕令详情 |
| `tianshu edict list [-s status] [-l limit]` | 列出敕令 |

### memorial 子命令

| 命令 | 说明 |
|------|------|
| `tianshu memorial get <id>` | 查看奏折详情（含 token 用量） |
| `tianshu memorial list [-s status] [-l limit]` | 列出奏折 |

### config 子命令

| 命令 | 说明 |
|------|------|
| `tianshu config list` | 列出所有 LLM 配置 |
| `tianshu config get <name>` | 查看指定配置 |
| `tianshu config add --name X --model Y` | 创建新配置 |
| `tianshu config set <name> [--model ...] [--enabled/--disabled]` | 更新配置 |
| `tianshu config rm <name> [-y]` | 删除配置 |
| `tianshu config activate <name>` | 切换活跃配置 |

### health 命令

| 命令 | 说明 |
|------|------|
| `tianshu health` | 检查服务健康状态 |

所有命令支持 `--format json` 输出。

---

## 13. Web 前端

### 技术栈

React 18 + TypeScript + Ant Design + Vite，使用 React Router 做客户端路由。

### 页面

| 页面 | 路径 | 功能 |
|------|------|------|
| `EdictListPage` | `/` | 敕令列表，支持状态筛选和搜索 |
| `EdictCreatePage` | `/edicts/new` | 创建新敕令 |
| `EdictDetailPage` | `/edicts/:id` | 敕令详情 + 奏折列表 + 事件时间线 + 追加指令 |

### 组件

| 目录 | 组件 | 说明 |
|------|------|------|
| `common/` | GlowCard | 发光卡片容器 |
| | HealthDot | 服务健康状态指示灯 |
| | MonoText | 等宽字体文本 |
| | PageContainer | 页面容器 |
| `edict/` | EdictForm | 敕令创建表单 |
| | EdictTable | 敕令列表表格 |
| | StatusTag | 状态标签（带颜色） |
| `memorial/` | MemorialCard | 奏折卡片 |
| | EventTimeline | 事件时间线 |
| | UsageDisplay | Token 用量展示 |
| `layout/` | AppHeader | 顶部导航栏 |
| | AppLayout | 整体布局 |
| | AppSidebar | 侧边导航 |

### Hooks

| Hook | 说明 |
|------|------|
| `useConfig` | LLM 配置管理 |
| `useEdictDetail` | 敕令详情 + 轮询状态 |
| `useHealth` | 服务健康检查 |
| `useTheme` | 深色/浅色主题切换 |

### 静态文件集成

`web.py` 中的 `mount_web()` 实现容器化部署时的前端集成：
- 将构建产物挂载到 `/assets` 路径
- 其余路径通过 SPA fallback 返回 `index.html`
- 包含路径遍历保护
- 如果静态文件目录不存在，以 API-only 模式运行

---

## 14. 关键设计决策

### 不可变配置

`LLMConfigState` 和 `AgentConfigState` 使用 `frozen=True` dataclass。更新操作创建新实例，保证线程安全且无隐式副作用。

### 异步任务执行

敕令提交后立即返回 202，Agent 在后台 `asyncio.Task` 中执行。前端通过轮询获取执行状态。`app.state.running_tasks` 追踪所有活跃任务，shutdown 时优雅取消。

### 事件审计

每个关键动作（提交、执行开始、工具调用、完成/失败）都记录到 `events` 表，形成完整的执行审计轨迹，支持前端事件时间线展示。

### 多轮对话（Follow-up）

`POST /api/edicts/{id}/follow-up` 端点支持在同一敕令下追加指令，自动将历史奏折构建为对话上下文传入 Agent，实现多轮交互。

### 工具分级（Tier）

工具定义包含 `tier` 字段（T0 只读 → T2 副作用），Phase 0 仅作标记，为后续审批机制预留。

### 路径沙箱

所有文件/目录操作工具共享 `safe_path()` 函数，通过 `resolve()` + 前缀检查确保路径不逃逸出 workspace。

### LLM Provider 路由

LiteLLM 统一接入层 + 自动 `openai/` 前缀逻辑，使系统可无缝对接 OpenAI、Anthropic、本地模型等多种 provider。

### 统一 API 信封

所有端点使用 `ApiResponse[T]` 泛型响应，包含 `success` / `data` / `error` / `metadata` 字段，前端可统一处理。
