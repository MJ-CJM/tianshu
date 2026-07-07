<div align="center">

# 天枢 · Tianshu

**一座会与你共同成长的异步 AI 执行平台**

*An async, governable AI execution platform — organized like an imperial court, growing with every task.*

[![CI](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml/badge.svg)](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

[文档导航](docs/README.md) · [快速开始](#-快速开始) · [架构一览](#️-架构一览) · [借鉴与致谢](#-借鉴与致谢)

</div>

---

## 这是什么

天枢是一个**异步、可治理、会成长**的 AI 执行平台。你通过 Web、API、CLI、飞书或 Telegram 下达一道「诏令(Edict)」，系统把目标转化为一条**可调度、可审批、可审计、可复盘**的执行链路，最终沉淀为执行记录、事件时间线、成本账本、长期记忆与监督报告。

它的组织方式借用了中国明代的「六部」官制作为隐喻：系统由若干各司其职的「官员(Persona)」构成——**内阁**规划、**兵部**执行、**都察院**审计、**通政司**通知、**文渊阁**掌记忆、**户部**管成本——在与你的协作中持续演化。隐喻只是外壳，落到代码就是清晰解耦的模块。

```text
下旨 Edict → 排期 Scheduler → 规划 Planner → 执行 Agent/DAG/长任务循环
   → 审计 Auditor → 通知 Notifier → 记忆与成长 Memory/Profile/Skill
```

> 与「即问即答」的对话式 Agent 不同，天枢面向**异步、长周期、需治理**的任务：下旨后由后台事件链推进，每一步都留痕、可干预、可复盘。

## ✨ 核心特性

- **🏛️ 六部官制** — 多官员各司其职：规划 / 执行 / 审计 / 通知 / 记忆 / 成本，由「朝廷」共享上下文协同。
- **🔄 主干事件解耦** — 主链路里程碑（`edict.submitted` → … → `audit.completed`）以事件解耦，带 `edict_id` 落库成时间线；子系统内部仍是直调，任务流转全程可追踪、可复盘。
- **🧠 记忆宫殿 + 成长飞轮** — 多层记忆（Markdown 真相源 + SQLite/FTS5 索引 + Drawer 快照）、技能渐进学习与修撰、人格画像合成，越用越懂你。
- **🛡️ 治理优先** — 工具分级(tier) + 策略管线 + 人工批红 + 会话规则；网络能力受 SSRF、host 白名单、凭证托管约束。能力强，但始终受控。
- **🌌 平行位面演化** — 把行为配置（乃至代码）捕获成可分支、可切换、可对比的快照；候选位面小流量探索，按**适应度**自动择优晋升——一套「宫殿版 git」式的自进化。
- **⚙️ 长任务自检** — 验收标准(AcceptanceCriteria) + critic 监督 + L0–L3 升级，长任务自己迭代到达标，必要时升级人工。
- **🔌 多入口同源** — Web / HTTP API / CLI / 飞书 / Telegram 共享同一后端契约。
- **💸 成本治理** — Token 计量、预算熔断、按模型/任务/官员多维归因。

## 🏛️ 架构一览

| 层 | 模块 | 职责 |
|---|---|---|
| 入口与接口 | `gateway/`（15 个域 router + `core/` 双通道共享层）`web/` `cli/` | HTTP/WS、Web 前端、CLI、飞书/Telegram |
| 跨层契约 | `kernel/` | Hook 类型与注册中心、ExitReason、ambient 上下文（不依赖业务模块） |
| 应用装配 | `bootstrap/` | FastAPI lifespan 按子系统拆分的 `wire_xxx()` 装配函数 |
| 领域契约 | `models/` | Edict / Memorial / Decree / Plan / AcceptanceCriteria |
| 流程编排 | `scheduler/` `planner/` `executor/` `dag/` | 事件主链路、LLM 规划、单任务/DAG/长任务执行 |
| Agent 核心 | `executor/agent.py` `llm.py` `providers/` | ReAct 循环、工具调用、上下文压缩、Provider fallback |
| 治理与安全 | `tools/` `executor/policy_hook.py` `auditor/` | 工具注册、策略引擎、人工审批、审计 |
| 成长系统 | `persona/` `memory/` `skills/` | 六部人格、记忆宫殿、技能学习与画像 |
| 可观测与存储 | `storage/`（`_base` + 15 领域 Mixin + facade）`bus/` `cost/` `notifier/` | SQLite 真相源、事件总线、成本账本、通知 |
| 自进化 | `universe/` | 平行位面、代码变体、适应度演化 |

> 完整设计见 [`docs/design/`](docs/design/)，实现现状见 [`docs/impl/`](docs/impl/)，两者按功能子系统一一对应。

## 🚀 快速开始

**前置**：Python ≥ 3.12，Node.js ≥ 20，（部署可选）Docker。

### 本地开发（前后端分离）

```bash
# 1) 后端
pip install -e ".[cli]"           # 或：uv sync
cp .env.example .env              # 编辑 .env，至少填 TIANSHU_LLM_API_KEY
uvicorn tianshu.app:create_app --factory --reload --port 8000

# 2) 前端（另开一个终端）
cd web && npm install && npm run dev
```

飞书/Telegram/网页抓取/MCP 等可选能力按 extras 拆分，按需安装：`pip install -e ".[feishu,telegram,web,mcp]"`，或一次装全 `pip install -e ".[all]"`。

前端开发服务器在 `http://localhost:7999`（自动代理 `/api` 到后端 8000）。**开发时访问 7999。**

### 一体化运行（单端口）

```bash
cd web && npm run build && cd ..
TIANSHU_STATIC_DIR=src/tianshu/web/static \
  uvicorn tianshu.app:create_app --factory --port 8000
```

访问 `http://localhost:8000`，API 与 Web UI 同端口提供。

### Docker 部署

```bash
docker build -t tianshu .
docker run -d --name tianshu -p 8000:8000 \
  -v tianshu-data:/data -v "$(pwd)/workspace:/workspace" \
  --env-file .env tianshu
```

两阶段构建，最终镜像只含 Python 运行时 + 前端静态文件。完整说明见 [`docs/usage/getting-started.md`](docs/usage/getting-started.md)。

### 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TIANSHU_LLM_API_KEY` | （必填） | LLM API 密钥 |
| `TIANSHU_LLM_MODEL` | `gpt-4o-mini` | 默认模型 |
| `TIANSHU_DB_PATH` | `.tianshu/tianshu.db` | SQLite 路径 |
| `TIANSHU_PORT` | `8000` | 监听端口 |
| `TIANSHU_WORKSPACE_DIR` | `.` | Agent 工作目录 |

## 📖 基本使用

下达诏令的五种入口殊途同归，都落到 `POST /api/edicts`：

```bash
# CLI
tianshu edict submit "帮我汇总本周的 PR 并生成周报"
tianshu edict list
tianshu memorial get <id>      # 查看执行结果（奏折）
```

```bash
# HTTP API
curl -X POST http://localhost:8000/api/edicts \
  -H 'Content-Type: application/json' \
  -d '{"goal": "帮我汇总本周的 PR 并生成周报"}'
```

下旨后可在 Web、`tianshu event list` 或 WebSocket `/api/ws` 观察任务流转（规划→执行→审计→通知）。完整流程与全部入口见 [`docs/usage/user-guide.md`](docs/usage/user-guide.md)。

## 🧩 二次开发

扩展工具、技能、人格、通知渠道、插件、LLM Provider 的最小步骤与代码落点，见 [`docs/usage/developer-guide.md`](docs/usage/developer-guide.md)。

## 📚 文档

| 入口 | 内容 |
|---|---|
| [docs/README.md](docs/README.md) | 文档总导航（六大分类 + 三条阅读路径） |
| [设计 design/](docs/design/) | 架构、领域模型，及 10 个功能子系统的设计 |
| [实现 impl/](docs/impl/) | 与设计同构的代码现状 |
| [使用 usage/](docs/usage/) | 快速开始、使用指南、开发者扩展指南 |
| [运维 ops/](docs/ops/) | 部署、凭证、飞书/Telegram 接入 |
| [参考 reference/](docs/reference/) | 借鉴的开源项目、术语表 |
| [战略 strategy/](docs/strategy/) | 竞争力复盘、发展战略与迭代排期、[决策台账](docs/strategy/DECISIONS.md) |
| [决策记录 adr/](docs/adr/) · [术语 CONTEXT.md](CONTEXT.md) | 不可逆决策的 why · 战略层 canonical 术语(中英对照) |
| [路线图 plan/](docs/plan/) · [特性 superpowers/](docs/superpowers/INDEX.md) | 分阶段计划与特性落地记录 |

## 🛠️ 技术栈

- **后端**：Python 3.12 · FastAPI · Pydantic v2 · LiteLLM · SQLite（WAL + FTS5）· APScheduler 式 croniter 调度
- **前端**：React 18 · TypeScript · Ant Design 5 · Vite · @xyflow（DAG 可视化）
- **集成**：MCP（Model Context Protocol）· 飞书（lark-oapi）· Telegram

## 🗺️ 路线图

| Phase | 目标 | 状态 |
|---|---|---|
| Phase 0 | 最小闭环：单 Agent + ReAct + 工具 + Skills + SQLite + Web/CLI | ✅ |
| Phase 1 | 治理与异步调度：EventBus + Scheduler + Planner + Auditor + 人工复核 + 官员人格 | ✅ |
| Phase 2 | 平台化：记忆宫殿 + 成本治理 + 多 Provider + 多通道 + 插件 + 平行位面 | ✅ |
| Phase 3 | 多 Agent 与分布式：DAG 并发 + 代码变体位面 + 分布式扩展 | 🚧 进行中 |

详见 [`docs/plan/`](docs/plan/)。

## 🙏 借鉴与致谢

天枢站在许多优秀开源项目的肩膀上。深度采纳：

- **[Claude Code]** — Agent Loop（ExitReason / 不可变 LoopState）、三层上下文压缩、Skills 渐进加载、Prompt 缓存、Hook 生命周期
- **Hermes Agent** — 技能安全 Guard、模糊匹配引擎、三层缓存（多处 `Ported from hermes-agent`）
- **NanoBot** — 双层 Markdown 记忆、分层 Context 注入、多模式调度
- **DeepAgents** — Subagent 上下文隔离、摘要策略、独立规划阶段

另有 PicoClaw / ZeroClaw / Pi-Mono / OpenClaw / Kimi-CLI / CoPaw 等提供了设计灵感。完整的借鉴矩阵、源码硬证据与天枢原创设计，见 [`docs/reference/reference-projects.md`](docs/reference/reference-projects.md)。

## 🤝 贡献

欢迎 Issue 与 PR——首发期实行「窄门贡献」：特性 PR 请先开 issue 对齐，详见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。工程约定见 [`CLAUDE.md`](CLAUDE.md) 与 [`.claude/rules/`](.claude/rules/)（简洁优先、外科手术式改动、80% 测试覆盖等）。

开发验证：

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint + 格式
.venv/bin/mypy && .venv/bin/lint-imports                    # 类型 + 分层契约
.venv/bin/pytest -m "not slow" -q                           # 测试
cd web && npm run lint && npm run typecheck && npm test -- --run && cd ..
```

## 📄 License

本项目以 [MIT License](LICENSE) 开源。
