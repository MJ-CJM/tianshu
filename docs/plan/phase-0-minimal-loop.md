# Phase 0：跑通最小闭环

> 对应架构设计 §1.5 Phase 0、§2.1、§8.1

---

## 目标

以最小代价跑通 HTTP 请求 → Edict → Agent ReAct Loop → 工具调用 → Memorial → HTTP 响应 + SQLite 持久化 的完整闭环，并支持 Docker 容器化部署。

## 本阶段制度映射

| 部院 | 模块 | 本阶段实现内容 |
|------|------|--------------|
| 御案台 | `Gateway` | FastAPI HTTP 接入，构建诏令（Edict） |
| 兵部 | `Agent` | ReAct 循环执行引擎 |
| 工部 | `Storage` / `ToolRegistry` / `ConfigManager` | SQLite 存储、工具注册、配置管理 |
| 礼部 | `Skills Loader` | SKILL.md 加载与系统提示注入 |
| 户部（预埋） | `Agent.usage` | 内嵌 usage 统计（不独立建模块） |
| 御案台（增强） | `Web Dashboard` | React + Ant Design Web 界面（基础 CRUD） |
| 御案台（增强） | `CLI` | typer + rich 命令行工具（基础指令） |

## 本阶段参考来源

| 参考 | 设计点 | 落点 Step |
|------|--------|----------|
| [NanoBot-1] | ReAct 循环状态机 | Step 0.7 |
| [DeepAgents-1] | ReAct 中间件思路 | Step 0.7 |
| [CoPaw] | schedule+dispatch+runtime 一体化任务模型 | Step 0.2（Edict 设计参考） |
| [PicoClaw-1] | 工作区隔离 | Step 0.5（file_ops 限制工作区） |
| [OpenClaw-6] | SKILL.md 格式兼容（frontmatter + 资格检查） | Step 0.6 |
| [OpenClaw-2] | 多层工具 Policy Pipeline（Phase 0 仅做标记） | Step 0.5（T0-T3 权限标记） |
| [ZeroClaw-1] | 安全策略违规不重试 | Step 0.9（错误恢复策略） |

## 运行方式

Web 服务进程（FastAPI + Uvicorn），同步串行执行。不引入 Planner、EventBus、Scheduler。

## 前置条件

- Python 3.12+ 环境
- LiteLLM 支持的至少一个 LLM Provider API Key

## Phase 验收标准（§8.1）

- [ ] `POST /api/edicts` 可以提交任务并完成一次完整执行
- [ ] `GET /api/edicts/{id}` 可以查询任务状态
- [ ] `GET /api/memorials/{id}` 可以查询执行结果
- [ ] 能生成 `Edict` 与 `Memorial`（SQLite 持久化）
- [ ] 工具调用采用标准 function calling
- [ ] Skills 系统能加载 SKILL.md 并注入 Agent 系统提示（与 OpenClaw 格式兼容）
- [ ] 失败、超时、进程终止都能写出终态结果
- [ ] `docker compose up` 可以启动服务并通过端口访问
- [ ] SQLite 中能看到可追踪的事件记录
- [ ] Web 界面可通过浏览器访问，支持任务提交、列表查看、详情查看
- [ ] `tianshu` CLI 可在容器内执行，支持 `health`、`edict submit/get/list`、`memorial get`

---

## Step 拆分

### Step 0.1 — 项目脚手架与配置基础

**目标**：搭建项目骨架，确保 `pip install -e .` 可用，配置系统可加载。

**涉及文件**
```
pyproject.toml
src/tianshu/__init__.py
src/tianshu/config.py
```

**依赖**：无

**验收条件**
- [ ] `pip install -e .` 成功安装
- [ ] `python -c "import tianshu"` 无报错
- [ ] 配置加载支持默认值 + 环境变量覆盖（§6.7）
- [ ] Pydantic Settings 模型定义完成，包含 LLM Provider、工作区路径、Web 服务端口等核心配置
- [ ] 单元测试覆盖配置加载与环境变量覆盖

**复杂度**：低

---

### Step 0.2 — 核心数据模型：诏令（Edict）+ 奏折（Memorial）

**目标**：定义 Phase 0 所需的 `Edict` 和 `Memorial` Pydantic 模型。

**涉及文件**
```
src/tianshu/models.py
```

**依赖**：Step 0.1

**验收条件**
- [ ] `Edict` 包含 Phase 0 字段：`id`、`goal`、`context`、`created_at`（§3.2）
- [ ] `Memorial` 包含 Phase 0 字段：`id`、`edict_id`、`status`、`summary`、`result`、`usage`、`error`、`created_at`、`started_at`、`completed_at`（§3.3）
- [ ] `TaskStatus` 枚举包含 Phase 0 状态：`SUBMITTED`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELLED`（§3.1）
- [ ] `UsageSummary` 子模型定义完成（token 统计）
- [ ] 所有模型可正确序列化 / 反序列化 JSON
- [ ] 单元测试覆盖模型创建、序列化、状态枚举

**复杂度**：低

---

### Step 0.3 — 工部：SQLite 存储层

**目标**：实现基于 SQLite 的存储层，作为系统真相来源。

**涉及文件**
```
src/tianshu/storage.py
```

**依赖**：Step 0.2

**验收条件**
- [ ] SQLite 数据库初始化，创建 `edicts`、`memorials`、`events` 表
- [ ] `save_edict(edict)` 写入 `edicts` 表
- [ ] `save_memorial(memorial)` 写入 `memorials` 表
- [ ] `append_event(edict_id, event)` 追加写入 `events` 表
- [ ] `load_edict(edict_id)` / `load_memorial(memorial_id)` 正确反序列化
- [ ] 使用 SQLite WAL 模式，支持 Web 并发读写
- [ ] `.tianshu/logs/` 目录保留用于调试日志和大产物
- [ ] 写入使用事务保证原子性，防止中途崩溃导致数据损坏
- [ ] 单元测试覆盖读写、查询与错误处理

**复杂度**：中

---

### Step 0.4 — LLM Client 封装

**目标**：封装 LiteLLM 调用，提供统一的对话和 function calling 接口。

**涉及文件**
```
src/tianshu/llm.py
```

**依赖**：Step 0.1

**验收条件**
- [ ] 封装 `LiteLLM` 的 `completion` 调用（§6.3）
- [ ] 支持普通对话和 function calling 两种模式
- [ ] 提取并返回 `usage` 信息（prompt_tokens、completion_tokens、total_tokens）
- [ ] 支持流式响应（可选，但接口预留）
- [ ] 消息角色统一为 `system` / `user` / `assistant` / `tool`（§4.2）
- [ ] 保留 `reasoning_content`（如果 Provider 返回）（§4.2）
- [ ] LLM 调用失败时指数退避重试，最多 3 次（§4.5）
- [ ] 单元测试使用 mock 覆盖正常调用、失败重试、usage 提取

**复杂度**：中

---

### Step 0.5 — 工部/吏部：工具注册与基础工具

**目标**：实现 `ToolRegistry` 和三个基础工具（web_search、shell、file_ops）。

**涉及文件**
```
src/tianshu/tools/__init__.py
src/tianshu/tools/registry.py
src/tianshu/tools/web_search.py
src/tianshu/tools/shell.py
src/tianshu/tools/file_ops.py
```

**依赖**：Step 0.1

**验收条件**
- [ ] `ToolRegistry` 支持注册、查找、列出工具（§6.4）
- [ ] 每个工具包含名称、描述、JSON Schema 参数定义（§4.3）
- [ ] 工具输出为 LLM function calling 协议的 `tools` 参数格式
- [ ] 工具权限分级标记（T0/T1/T2/T3），Phase 0 仅做标记不做拦截（§4.4）
- [ ] `web_search`：调用搜索 API，返回结构化结果（T0）
- [ ] `shell`：执行 shell 命令，返回 stdout/stderr（T2）
- [ ] `file_ops`：读/写/列目录，默认限制工作区（T0/T1）（§4.4）
- [ ] 工具执行异常作为 Observation 返回，不直接崩溃（§4.5）
- [ ] 单元测试覆盖注册、调用、错误处理

**复杂度**：中

---

### Step 0.6 — 礼部：Skills Loader

**目标**：实现 SKILL.md 发现、frontmatter 解析、资格检查、系统提示注入。与 OpenClaw 格式兼容。

**涉及文件**
```
src/tianshu/skills/__init__.py
src/tianshu/skills/loader.py
src/tianshu/skills/builtin/web-search/SKILL.md
src/tianshu/skills/builtin/file-ops/SKILL.md
src/tianshu/skills/builtin/shell/SKILL.md
```

**依赖**：Step 0.1

**验收条件**
- [ ] 按优先级扫描 Skills 目录：工作区级 > 项目级 > 用户级 > 内置级（§6.4）
- [ ] 解析 YAML frontmatter，支持 `metadata.openclaw` 和 `metadata.tianshu` 双命名空间（§6.4）
- [ ] 资格检查：`requires.bins`（`shutil.which`）、`requires.anyBins`、`requires.env`（`os.environ`）、`requires.config`、`os`、`always`（§6.4）
- [ ] 加载限制：单目录最大 300 候选、最大 150 个注入、30,000 字符预算、单文件 256KB（§6.4）
- [ ] 高优先级目录的同名 Skill 覆盖低优先级
- [ ] 格式化后拼入 Agent 系统提示
- [ ] 内置 3 个 SKILL.md（web-search、file-ops、shell）
- [ ] 单元测试覆盖扫描、解析、资格检查、加载限制

**复杂度**：中

---

### Step 0.7 — 兵部：Agent ReAct Loop

**目标**：实现 Agent 核心 ReAct 循环（Thinking → Acting → Observing → Done/Failed）。

**涉及文件**
```
src/tianshu/agent.py
```

**依赖**：Step 0.4 + Step 0.5 + Step 0.6

**验收条件**
- [ ] 实现 ReAct 状态机：Thinking → Acting → Observing → Done / Failed（§4.1）
- [ ] Thinking：将完整消息历史发给 LLM，等待决策
- [ ] Acting：LLM 返回 `tool_calls` 时，通过 `ToolRegistry` 顺序执行（§4.3）
- [ ] Observing：工具结果以 `tool` 消息追加到消息历史
- [ ] Done：LLM 返回最终答案（无 `tool_calls`）时生成输出
- [ ] Failed：超过 `max_iterations`、超时、不可恢复异常时返回失败
- [ ] Skills 注入到系统提示（通过 Skills Loader）
- [ ] 消息历史只追加不压缩（Phase 0 策略）（§4.2）
- [ ] 提取并累计每次 LLM 调用的 usage 信息
- [ ] 工具异常作为 Observation 反馈给 LLM（§4.5）
- [ ] 集成测试覆盖完整 ReAct 循环（使用 mock LLM）

**复杂度**：高

---

### Step 0.8 — 御案台：FastAPI Gateway API

**目标**：实现 FastAPI Web 服务入口，串联所有组件完成端到端执行。

**涉及文件**
```
src/tianshu/app.py
src/tianshu/gateway/__init__.py
src/tianshu/gateway/api.py
src/tianshu/gateway/validator.py
```

**依赖**：Step 0.2 + Step 0.3 + Step 0.7

**验收条件**
- [ ] FastAPI 应用提供 RESTful API（§5.1）
- [ ] `POST /api/edicts` 提交任务：输入校验 → 构建 `Edict` → 保存到 SQLite → 调用 Agent ReAct Loop → 生成 `Memorial` → 返回结果
- [ ] `GET /api/edicts/{id}` 查询任务状态
- [ ] `GET /api/memorials/{id}` 查询执行结果
- [ ] API 响应使用统一信封格式（成功/错误/分页元数据）
- [ ] 输入校验使用 Pydantic 模型
- [ ] `GET /health` 健康检查端点
- [ ] `uvicorn` 启动 Web 服务
- [ ] SQLite 中 edicts 和 memorials 表有对应记录
- [ ] SQLite 中 events 表有对应事件记录
- [ ] 端到端集成测试（使用 mock LLM + TestClient）

**复杂度**：中

---

### Step 0.9 — 异常处理与 Graceful Shutdown

**目标**：确保失败、超时、进程终止等异常场景都能正确写出终态结果。

**涉及文件**
```
src/tianshu/agent.py（增强）
src/tianshu/app.py（增强）
```

**依赖**：Step 0.8

**验收条件**
- [ ] SIGINT/SIGTERM 触发 graceful shutdown：正在执行的任务写出 `CANCELLED` 状态的 Memorial（§3.1）
- [ ] Uvicorn shutdown 钩子正确清理资源
- [ ] 超时（`max_iterations` 或时间超限）：写出 `FAILED` 状态的 Memorial + error 原因
- [ ] LLM 调用失败（重试耗尽）：写出 `FAILED` 状态的 Memorial + error 原因
- [ ] 工具执行异常：不中断循环，作为 Observation 反馈（§4.5）
- [ ] 任何终态都会生成 `execution.completed` / `execution.failed` / `execution.cancelled` 事件记录
- [ ] 存储层写入失败时打日志但不覆盖原始异常
- [ ] 单元测试覆盖各异常场景

**复杂度**：中

---

### Step 0.10 — Docker 容器化

**目标**：支持 Docker 容器化部署，`docker compose up` 一键启动。

**涉及文件**
```
Dockerfile
docker-compose.yml
```

**依赖**：Step 0.8

**验收条件**
- [ ] Dockerfile：多阶段构建，最小化镜像体积
- [ ] docker-compose.yml：天枢 Web 服务一键启动
- [ ] 容器内 SQLite 数据通过 volume 持久化
- [ ] 配置通过环境变量注入（§6.7）
- [ ] 健康检查端点 `/health` 配置为 Docker healthcheck
- [ ] `docker compose up` 后通过 `http://localhost:<port>/api/edicts` 可访问
- [ ] 日志输出为结构化格式，便于容器日志采集
- [ ] 集成测试：docker compose 启动后完成端到端执行

**复杂度**：低

---

### Step 0.11 — 御案台：Web Dashboard（基础 CRUD）

**目标**：提供 Web 界面，用户可以通过浏览器提交任务、查看列表、查看执行结果。

**涉及文件**
```
web/                              # 新目录：前端项目
  package.json / tsconfig.json / vite.config.ts / index.html
  src/
    main.tsx / App.tsx
    api/client.ts                 # HTTP 客户端封装
    api/types.ts                  # TypeScript 类型（对应 Pydantic 模型）
    api/edicts.ts / api/memorials.ts
    stores/edictStore.ts / stores/memorialStore.ts
    pages/EdictListPage.tsx       # 任务列表（御案台主页）
    pages/EdictDetailPage.tsx     # 任务详情 + Memorial 结果
    pages/EdictCreatePage.tsx     # 提交新任务（下旨）
    components/layout/AppLayout.tsx / Sidebar.tsx
    components/edict/EdictTable.tsx / EdictForm.tsx / EdictStatusTag.tsx
    components/memorial/MemorialCard.tsx
    hooks/usePolling.ts           # 状态轮询（Phase 0 无 WebSocket）
    utils/format.ts / constants.ts

src/tianshu/web/
  __init__.py
  static/                         # 构建产物（gitignore）
  routes.py                       # FastAPI StaticFiles 挂载 + SPA fallback

src/tianshu/app.py                # 修改：挂载 web routes
Dockerfile                        # 修改：增加 Node.js 构建阶段
```

**依赖**：Step 0.8（Gateway API）+ Step 0.10（Docker）

**验收条件**
- [ ] `web/` 项目使用 Vite + React + TypeScript + Ant Design 初始化
- [ ] `npm run build` 产出 `web/dist/` SPA 包
- [ ] FastAPI 在 `/` 提供 SPA，`/api/*` 提供 API，同进程同端口
- [ ] 任务列表页：Ant Design Table 展示所有 Edict，状态标签按颜色区分
- [ ] 任务创建页：表单含 `goal`（必填）+ `context`（选填），提交后显示成功/失败
- [ ] 任务详情页：展示 Edict 信息 + 关联 Memorial（结果、状态、usage、错误）
- [ ] 状态轮询：Edict 处于 `SUBMITTED` / `RUNNING` 时每 2 秒轮询更新
- [ ] 健康指示器：页头根据 `GET /health` 显示绿/红状态
- [ ] Dockerfile 多阶段构建：Node.js 阶段编译前端，Python 阶段复制 `dist/`
- [ ] `docker compose up` 后同端口可访问 API 和 Web 界面
- [ ] TypeScript 类型与 Pydantic 模型对齐（Edict / Memorial / TaskStatus / UsageSummary）
- [ ] API 错误以 Ant Design notification 展示，不静默吞掉

**复杂度**：中

**Dockerfile 变更要点**：
```dockerfile
# Stage 1: 前端构建
FROM node:20-alpine AS frontend-build
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Python 应用（已有，补充 COPY）
COPY --from=frontend-build /app/web/dist /app/src/tianshu/web/static
```

---

### Step 0.12 — 御案台：CLI 基础指令

**目标**：提供容器内 CLI 工具，运维/开发者可通过 `tianshu` 命令提交任务、查询状态、健康检查。

**涉及文件**
```
src/tianshu/cli/
  __init__.py
  main.py                        # typer app 入口，注册子命令组
  client.py                      # httpx 封装（BaseURL、认证、错误处理、超时）
  output.py                      # rich 格式化（表格、JSON、状态着色）
  config.py                      # CLI 本地配置读写（~/.tianshu/cli.toml）
  commands/
    __init__.py
    edict.py                     # tianshu edict submit/get/list
    memorial.py                  # tianshu memorial get/list
    health.py                    # tianshu health
    config.py                    # tianshu config show/set

pyproject.toml                   # 修改：[project.scripts] tianshu = "tianshu.cli.main:app"
                                 # 修改：[project.optional-dependencies] cli = ["typer", "rich", "httpx"]
```

**依赖**：Step 0.1（项目脚手架）+ Step 0.8（Gateway API）

**验收条件**
- [ ] `pyproject.toml` 配置 `[project.scripts]` 入口：`tianshu = "tianshu.cli.main:app"`
- [ ] `pip install -e .` 后终端可直接执行 `tianshu --help`
- [ ] `tianshu health` 调用 `GET /health`，显示服务状态（绿色/红色）
- [ ] `tianshu edict submit --goal "..." [--context "..."]` 调用 `POST /api/edicts`
- [ ] 提交成功后打印 Edict ID + 状态，提示 `tianshu memorial get <id>` 查看结果
- [ ] `tianshu edict get <id>` 调用 `GET /api/edicts/{id}`，rich 表格展示
- [ ] `tianshu edict list [--status STATUS] [--limit N]` 列出任务
- [ ] `tianshu memorial get <id>` 展示结果（摘要、正文、usage、错误）
- [ ] 所有命令支持 `--format json|table` 全局输出格式选项
- [ ] 状态着色：COMPLETED=绿色、RUNNING=蓝色、FAILED=红色、NEEDS_REVIEW=黄色
- [ ] API 连接失败时给出友好错误提示（"无法连接到天枢服务"）
- [ ] 环境变量 `TIANSHU_API_URL`（默认 `http://localhost:8000`）、`TIANSHU_API_KEY` 可配置
- [ ] `tianshu config show/set` 管理本地配置
- [ ] Docker 容器内 `tianshu health` 可连通本地 FastAPI（同容器 localhost）
- [ ] 单元测试覆盖客户端错误处理、输出格式化（typer.testing.CliRunner + respx）

**复杂度**：中

**Entry Point 说明**：
```toml
# pyproject.toml
[project.scripts]
tianshu = "tianshu.cli.main:app"
```
容器内 `pip install .` 后 `tianshu` 自动注册到 PATH，无需额外 Dockerfile 改动。

---

## Step 依赖关系图

```
0.1 项目脚手架
 ├──> 0.2 数据模型 ──> 0.3 SQLite 存储 ──┐
 ├──> 0.4 LLM Client ───────────────────┤
 ├──> 0.5 工具注册 ──────────────────────┼──> 0.7 Agent Loop ──> 0.8 Gateway API ──┬──> 0.9 异常处理
 └──> 0.6 Skills Loader ────────────────┘                                          ├──> 0.10 Docker ──> 0.11 Web Dashboard
                                                                                   └──> 0.12 CLI 基础指令
```

**可并行组**：
- 组 A：Step 0.2 → 0.3（数据 + 存储）
- 组 B：Step 0.4（LLM）、Step 0.5（工具）、Step 0.6（Skills）
- 组 C：Step 0.9（异常处理）、Step 0.10（Docker）
- 组 D：Step 0.11（Web Dashboard）、Step 0.12（CLI 基础指令）— 可并行

组 A 和组 B 之间无依赖，可并行开发。Step 0.7 是汇聚点，依赖组 B 全部完成。Step 0.9 和 0.10 只依赖 0.8，可并行。Step 0.11 依赖 0.10（Docker），Step 0.12 依赖 0.8（Gateway API），两者之间无依赖可并行。

## 测试策略

| 层次 | 覆盖范围 | 工具 |
|------|---------|------|
| 单元测试 | 每个 Step 独立验证 | pytest |
| 集成测试 | Step 0.8 端到端（mock LLM + TestClient） | pytest + httpx |
| 容器化测试 | Step 0.10 Docker Compose 启动后端到端 | docker-compose + pytest |
| 冒烟测试 | 真实 LLM 调用（可选） | 手动 / CI 可选 |

## 风险

| 风险 | 缓解措施 |
|------|---------|
| LiteLLM 不同 Provider 的 function calling 行为差异 | 选定一个主力 Provider 先跑通，其余列为已知差异 |
| SKILL.md 格式与 OpenClaw 实际格式可能有细微差异 | 以 OpenClaw 仓库的真实 SKILL.md 文件做兼容性测试 |
| 工具执行的安全边界（shell 命令） | Phase 0 先做工作区限制，不实现完整 Policy Pipeline |
| SQLite 并发写入在高负载下的性能 | 使用 WAL 模式；Phase 0 为单用户场景，并发有限 |
