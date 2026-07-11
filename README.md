<div align="center">

<img src="docs/launch/assets/logo.png" alt="天枢 · Tianshu" width="220">

# 天枢 · Tianshu

**天枢是一个可治理、可验证、持续成长的自进化 Agent OS**

*A governable, verifiable Agent OS designed to learn and evolve continuously.*

[![CI](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml/badge.svg)](https://github.com/MJ-CJM/tianshu/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Stars](https://img.shields.io/github/stars/MJ-CJM/tianshu?style=flat&logo=github&label=Star&color=CDA95C)](https://github.com/MJ-CJM/tianshu/stargazers)

[English](README.en.md) · [文档导航](docs/README.md) · [快速开始](#-快速开始) · [架构一览](#️-架构一览) · [借鉴与致谢](#-借鉴与致谢)

</div>

---

## 这是什么

天枢是一个可治理、可验证、持续成长的自进化 Agent OS。你通过 Web、API、CLI、飞书或 Telegram 下达一道「诏令(Edict)」，系统把目标转化为一条可调度、可裁决、可审计、可复盘的执行链路，最终沉淀为执行记录、事件时间线、成本账本、长期记忆与监督报告。

它的组织方式借用了中国明代的「六部」官制作为隐喻：系统由若干各司其职的「官员(Persona)」构成——**内阁**规划、**兵部**执行、**都察院**审计、**通政司**通知、**文渊阁**掌记忆、**户部**管成本——在与你的协作中持续演化。隐喻只是外壳，落到代码就是清晰解耦的模块。

```text
下旨 Edict → 排期 Scheduler → 规划 Planner → 执行 Agent/DAG/长任务循环
   → 审计 Auditor → 通知 Notifier → 记忆与成长 Memory/Profile/Skill
```

> **v0.4.2 当前边界：**面向可信本地、单机单节点使用。Native 执行路径具备事前工具策略与裁决；Claude Code/Codex 客卿仅为 `contained + experimental`。本地 HTTP、WebSocket 与 MCP 入口尚无统一身份认证，**不得直接暴露到不可信网络**。逐项保证与非保证见[能力事实矩阵](docs/launch/capability-matrix.md)。
>
> 与「即问即答」的对话式 Agent 不同，天枢面向**异步、长周期、需治理**的任务：下旨后由后台事件链推进，受支持的里程碑会形成可查询记录。

## ✨ 核心特性

- **🏛️ 六部官制** — 多官员各司其职：规划 / 执行 / 审计 / 通知 / 记忆 / 成本，由「朝廷」共享上下文协同。
- **🔄 本地主干事件链** — 主链路里程碑（`edict.submitted` → … → `audit.completed`）以事件解耦，带 `edict_id` 写入 SQLite 时间线；当前 EventBus 不是持久消息队列。
- **🧠 记忆与成长（实验）** — 多层记忆、技能候选修撰和人格画像可以积累经验；真实效果提升与自动晋升仍需后续门禁证明。
- **🛡️ Native 治理（有限稳定）** — 内建 Agent 的工具分级、策略管线、人工裁决和会话规则在工具执行前生效；该保证不穿透 opaque 外部 CLI 的内部工具调用。
- **🥷 本地运行时防护（有限稳定）** — 支持出站脱敏、bash 分段风险分级、子进程 clean-env 与分级急停；这些机制不等同于容器或 OS 安全沙箱。见 [SECURITY.md](SECURITY.md)。
- **🌌 平行位面（实验）** — 支持行为/代码快照、分支、diff 与人工切换；当前任务仍路由 champion，不提供真实在线 challenger 分流或可信自动晋升。
- **📏 配对评估（实验）** — `tianshu evals run` 以隔离端口和数据库配置的本地子进程比较历史样本并生成报告；子进程仍共享宿主 OS 权限与网络。
- **⚙️ 长任务自检（实验）** — AcceptanceCriteria + critic + L0–L3 升级支持部分 checkpoint；不保证任意故障点的完整重启恢复。
- **🔌 多入口** — Web / HTTP API / CLI / 飞书 / Telegram 复用后端模型；Telegram 支持按钮裁决，飞书当前使用命令回复。
- **🤝 双向互操作（边界不同）** — MCP host 可向天枢下旨；Keqing 可启动 Claude Code/Codex CLI，但后者当前只是 `contained + experimental` 外围适配。
- **💸 成本记录（有限稳定）** — 支持 Token 计量、按模型/任务/官员归因与 best-effort 预算门禁；因用量在执行后上报，阈值可能出现超调。

## 🏛️ 架构一览

| 层 | 模块 | 职责 |
|---|---|---|
| 入口与接口 | `gateway/`（15 个域 router + `core/` 双通道共享层）`web/` `cli/` | HTTP/WS、Web 前端、CLI、飞书/Telegram |
| 跨层契约 | `kernel/` | Hook 类型与注册中心、ExitReason、ambient 上下文（不依赖业务模块） |
| 应用装配 | `bootstrap/` | FastAPI lifespan 按子系统拆分的 `wire_xxx()` 装配函数 |
| 领域契约 | `models/` | Edict / Memorial / Decree / Plan / AcceptanceCriteria |
| 流程编排 | `scheduler/` `planner/` `executor/` `dag/` | 事件主链路、LLM 规划、单任务/DAG/长任务执行 |
| Agent 核心 | `executor/agent.py` `llm.py` `providers/` | ReAct 循环、工具调用、上下文压缩、Provider fallback |
| 治理与安全 | `tools/` `executor/policy_hook.py` `auditor/` | 工具注册、策略引擎、人工裁决、审计 |
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
docker run -d --name tianshu -p 127.0.0.1:8000:8000 \
  -v tianshu-data:/data -v "$(pwd)/workspace:/workspace" \
  --env-file .env tianshu
```

默认 `trusted-local` 仅信任回环入口。需要远程访问时必须显式启用
`secure-remote`，配置 HTTPS 公共地址、精确 Host/Origin、可信反代 CIDR 和
bootstrap token hash；匿名 REST、WebSocket 与 MCP 会在统一入口被拒绝。

两阶段构建会移除运行时中的 Node.js 和 node_modules；运行镜像仍包含 Python 应用源码、后端依赖及执行器所需的系统工具。完整说明见 [`docs/usage/getting-started.md`](docs/usage/getting-started.md)。

### 常用环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `TIANSHU_LLM_API_KEY` | （必填） | LLM API 密钥 |
| `TIANSHU_LLM_MODEL` | `gpt-4o-mini` | 默认模型 |
| `TIANSHU_DB_PATH` | `~/.tianshu/tianshu.db` | SQLite 路径 |
| `TIANSHU_HOST` | `127.0.0.1` | 默认仅监听回环地址 |
| `TIANSHU_PORT` | `8000` | 监听端口 |
| `TIANSHU_SECURITY_MODE` | `trusted-local` | `trusted-local` 或 fail-closed 的 `secure-remote` |
| `TIANSHU_API_TOKEN` | （空） | CLI/MCP 的 Bearer token；不要写入仓库 |
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

### 回归评测与失败归因

当前评测用于生成候选证据：它以独立端口和数据库配置启动本地子进程，回放历史样本并量化打分：

```bash
tianshu evals sample baseline --size 8   # 分层混采历史任务，固化为可重复回归集
tianshu evals run --set baseline         # 本地子进程跑批 → 报告（fitness 分项 / 逐条结果 / Δ vs 上次）
tianshu evals failures --days 30         # 失败归因分布（17 类失败分类学自动归因）
```

评测跑批只在 CLI（花钱的重活不开 HTTP 触发面）；报告在 Web「评测中心」与 `GET /api/evals/runs` 可查。评测凭证可用 `TIANSHU_EVAL_LLM_API_KEY` 与主配额隔离。这里的“隔离”是运行配置隔离，不是安全沙箱，也不会自动触发候选晋升。

### 急停与密钥轮换（迭代 3「深防御」）

出事时的急刹车、以及凭证主密钥的安全轮换：

```bash
# 分级急停（也可在 Web「系统管理 → 急停」操作）
curl -X POST http://localhost:8000/api/estop/engage -d '{"kill_all": true, "reason": "手动排查"}'
curl -X POST http://localhost:8000/api/estop/resume -d '{"all_clear": true}'

# 凭证主密钥轮换（旧密钥解密 → 新密钥重加密，干跑校验 + 自动备份）
tianshu secrets gen-key                                  # 生成新密钥
tianshu secrets rotate-master-key --new-key <新密钥>      # 轮换后更新 env 并重启
```

出厂默认每日预算护栏 ¥20（`TIANSHU_DAILY_BUDGET_GUARDRAIL_CNY`）、遥测默认关（`TIANSHU_TELEMETRY=on` 才启）、OTel 埋点默认关（设 `TIANSHU_OTEL_ENDPOINT` 才导出）。

### 客卿：反向派 Claude Code / Codex 出工（实验）

执行面可选外部 CLI。天枢可以管理任务外壳，但当前不能观察或拦截客卿内部的每一次工具调用：

```bash
tianshu keqing agents                       # 列可用客卿 backend
# 下旨时指定 runtime.executor = keqing:claude-code（web 边建敕表单也有下拉）
# 查询客卿运行后产生的影子快照；存在快照时可回滚对应文件状态：
tianshu shadow list <edict_id>              # 查该 edict 的影子快照
tianshu shadow revert <edict_id> <sha>      # 回滚工作区到某快照
```

当前 Keqing 的保证限于：**独立工作目录、clean-env、外围 timeout、事后结果归一与已捕获工具事件的外围转发**。它不保证 CLI 内部事件完整性、事前工具拦截、硬成本上限或运行前恢复点；stream-json 成本归因是 best-effort，不能作为硬熔断。影子快照使用独立 `GIT_DIR`，但快照发生在运行后，不能替代执行前的安全恢复点。完整 flags 见[能力事实矩阵](docs/launch/capability-matrix.md)。

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
| [宣发 launch/](docs/launch/) | 宣发工具包:英文 README、架构博文、隐喻对照、GIF/视频分镜、成本基线 |
| [能力事实矩阵](docs/launch/capability-matrix.md) | v0.4.2 的成熟度、默认值、可验证保证与明确非保证 |
| [决策记录 adr/](docs/adr/) · [术语 CONTEXT.md](CONTEXT.md) | 不可逆决策的 why · 战略层 canonical 术语(中英对照) |
| [路线图 plan/](docs/plan/) · [特性 superpowers/](docs/superpowers/INDEX.md) | 分阶段计划与特性落地记录 |

## 🛠️ 技术栈

- **后端**：Python 3.12 · FastAPI · Pydantic v2 · LiteLLM · SQLite（WAL + FTS5）· APScheduler 式 croniter 调度
- **前端**：React 18 · TypeScript · Ant Design 5 · Vite · @xyflow（DAG 可视化）
- **集成**：MCP（Model Context Protocol）· 飞书（lark-oapi）· Telegram

## 🗺️ 开源阶段门

| Gate | 目标 | 当前公开承诺 |
|---|---|---|
| G0 | 事实、术语、桌面原型真实性与迁移基线 | 只按本页与能力矩阵描述 v0.4.2 |
| G1 | Public-safe Foundation | 通过后才允许 Developer Preview |
| G2 | Durable Governance & Evidence | 通过后才承诺裁决/任务耐重启和 Evidence Bundle |
| G3 | Desktop Web Productization | 通过后才录制真实产品演示 |
| G4 | Governed Evolution | 通过后才宣称自进化闭环成立 |
| G5 | Open-source Launch | 通过后才正式开源宣发 |

详见[开源 Agent OS 总路线图](docs/superpowers/plans/2026-07-10-open-source-agent-os-master-roadmap.md)。阶段目标不是当前版本承诺。

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

## ⭐ 星图

如果天枢帮你把活干成了，点一颗 Star——你的星子会落进下面这张星图里，记下这个项目的成长。

<div align="center">

<a href="https://star-history.com/#MJ-CJM/tianshu&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date" />
    <img src="https://api.star-history.com/svg?repos=MJ-CJM/tianshu&type=Date" alt="天枢 Star 增长曲线" width="640">
  </picture>
</a>

</div>

## 📄 License

本项目以 [MIT License](LICENSE) 开源。
