# 使用指南

从启动服务到颁发敕令、再到查看结果的完整流程。术语见 [../reference/glossary.md](../reference/glossary.md)，环境准备见 [getting-started.md](getting-started.md)。

## 1. 启动服务

天枢是单一 FastAPI 后端（默认 `http://localhost:8000`）。Web 和 CLI 走同一组 `/api`
契约；飞书和 Telegram bot 在同一进程内通过平台无关的应用服务提交，不会绕过同一套
SQLite 真相源与 durable outbox。启动方式见 [getting-started.md](getting-started.md)。
CLI 默认连环境变量 `TIANSHU_API_URL`（缺省 `http://localhost:8000`）。

默认 `trusted-local` 只用于本机回环。`secure-remote` 下先执行 `tianshu auth login`；普通
PAT/session 只能读取和操作自己的任务、奏折、DAG、裁决与定时记录，跨用户资源返回
`404`，避免泄露资源是否存在。带 `admin` scope 的主体可做全局读取和平台配置；成本、
网络审计、Worker、会话规则等全局面不会向普通 PAT 开放。

## 2. 首次引导与六个主入口

全新实例访问根路由时会进入 `/onboarding`。确认当前 demo/live profile 后，页面展示
内置官员和技能，并要求创建首个治理任务；创建成功后直接进入该任务详情。已有任务的
实例直接进入中枢。

默认侧栏按工作场景组织为六个一级入口；实验能力集中在天工院，不与正式办公入口混排：

| 主入口 | 用途 |
|---|---|
| **中枢** | 查看当前执行中、未归档敕令、待裁决总数、累计证据束（含归档）与独特能力 |
| **御书房** | 通过全部敕令查看任务，通过颁发敕令新建任务，通过钦天监管理定时任务，通过都察院查看审计 |
| **朝堂** | 在吏部管理百官，在廷议开展多方会商，在内阁查看规划统计与历史 |
| **百司** | 在翰林院管理知识与记忆，在鸿胪寺处理外部联络，在通政司管理消息与通知 |
| **天工院〔实验〕** | 进入演化司〔实验〕、诸界台〔实验〕、考功司〔试行〕和客卿馆〔实验〕 |
| **内府** | 在藏兵阁管理系统与扩展，在权印司管理权限与会话，在户部账房查看成本与预算 |

中枢的四项治理指标来自同一次 `/api/control` 权威快照，口径固定如下：

- **当前执行中**只统计尚未进入 `completed` 或 `failed` 终态的运行实例；它不等于
  “未归档敕令”，所以任务工作台仍有任务而此处为 `0` 是正常情况；
- **未归档敕令**统计当前可见且没有归档时间的任务，并显示“待后续指令”和“已撤回”
  两个分项；已完成但仍可续接的对话任务属于前者，不会冒充正在执行；
- **待裁决总数**统计仍未解决的持久裁决；**累计证据束（含归档）**统计可见任务历次
  生成的证据束，不因对应任务归档而从累计数中扣除；
- 普通主体只看本人范围，带 `admin` scope 的管理员看全局；四项指标、下面的运行/裁决/
  证据列表使用同一授权范围，不会混用本人和全局数据。

中枢在页面位于前台期间每 5 秒重拉一次快照作为兜底；执行、裁决、审计、长程任务等
相关 WebSocket 事件到达时会立即使快照失效并重拉。页面转入后台后停止轮询，回到前台
后恢复，因此正常使用时不需要手动刷新才能看到状态变化。

御书房默认不按状态缩窄结果，并提供状态筛选。任务可同时显示立即、单次定时、周期、
长程、对话和客卿等类型标签；长程、定时与实验性的客卿任务不会因入口合并而被隐藏。
“未结案”表示任务仍可继续批示，不等于当前正在执行；“运行中”只用于最新执行阶段，
已完成但可续接的对话任务显示为“待后续指令”。旧 `/edicts` 地址会兼容跳转到
`/approvals` 对应的御书房，不再维护第二套任务列表。

## 3. 颁发敕令（5 种渠道）

一切从「敕令（Edict）」开始。五种渠道最终都调用 `EdictApplicationService`：它在同一
事务中保存 Edict、首条 Memorial、提交幂等记录与 outbox，再由后台执行链消费。Web、
HTTP API 和 CLI 经 `POST /api/edicts` 提交，首次接受返回 `202`，相同幂等请求重放返回
`200`；飞书和 Telegram 由 bot bridge 直接调用同一应用服务。提交去重使用
`idempotency_key + submitter`；不同已认证主体不会共享同一个幂等身份。

| 入口 | 操作 |
|---|---|
| **Web** | 御书房 → 颁发敕令；先选快速/分析/编码/研究类型，通常只需填写目标与执行时间，细节在专家模式 |
| **HTTP API** | `POST /api/edicts`（创建）/ `POST /api/edicts/parse`（自然语言先解析成草案） |
| **CLI** | `tianshu edict submit …` 下旨；`tianshu edict list` 列表；`tianshu edict get <id>` 详情 |
| **飞书** | 助手模式（自然对话）/ 敕令模式（纯文本下旨），配置见 [../ops/feishu-setup.md](../ops/feishu-setup.md) |
| **Telegram** | 同飞书双模式，配置见 [../ops/telegram-setup.md](../ops/telegram-setup.md) |

定时任务也可直接从 Web“颁发敕令”选择单次或重复，或调用 API/对话内
`schedule_edict` 工具。普通任务支持立即、单次、cron 与 interval；长程任务只支持
立即或单次定时，周期长程组合会被 Web、API 和调度工具拒绝。

## 4. 任务怎么流转（可观测点）

下旨后链路为 排期(Scheduler)→规划(Planner)→执行(Agent/DAG/OuterLoop)→审计(Auditor)→通知(Notifier)，详见 [../design/runtime-flow.md](../design/runtime-flow.md)。每个环节都可观测：

| 观测项 | 入口 |
|---|---|
| 事件时间线 | `GET /api/edicts/{id}` 详情 / `tianshu event list` |
| 实时事件流 | WebSocket `/api/ws`（`stream.delta` / `audit.completed` / `outer_loop.*`） |
| 待裁决工具 | `GET /api/approvals/pending_tool_calls` / 御书房中的待人工介入状态（`approvals` 为兼容保留的历史 API 名） |
| 规划裁决 | `POST /api/edicts/{id}/plan/approve` 或 `/plan/reject` |
| 执行结果（奏折） | `GET /api/edicts/{id}/memorial` / `tianshu memorial get <id>` |
| 审计 | 任务自身的审计随任务所有权可见；全局 `GET /api/audit/*` 统计与导出仅管理员 |
| 成本 | `GET /api/cost/*` / `tianshu cost summary`；全局成本面仅管理员 |
| 记忆 | `GET /api/memory-palace/*` / 前端记忆宫殿页；平台级记忆管理仅管理员 |

## 5. 常见操作

| 操作 | 方式 |
|---|---|
| 续接对话（follow-up） | `POST /api/edicts/{id}/follow-up`（带本轮 override，不重走规划） |
| 暂停 / 恢复 | `POST /api/edicts/{id}/pause` / `/resume` |
| 运行中补充长任务要求 | `POST /api/edicts/{id}/steer`；在下一轮 actor 边界吸收 |
| 查 / 管定时任务 | Web 御书房 → 钦天监；`GET /api/scheduler/jobs` |
| 编辑 / 暂停 / 恢复定时任务 | `PATCH /api/scheduler/jobs/{job_id}`；`POST …/pause` / `…/resume` |
| 立即执行 / 查看历史 | `POST …/run-now`（要求 `Idempotency-Key`）；`GET …/runs` |
| 长任务人工决策（L3） | `GET /api/edicts/outer-loop/pending` → `POST …/outer-loop/decide`（continue / accept_as_is / abort / modify_acceptance） |
| 工具裁决 | `POST /api/decrees` / `tianshu decree submit`（`Decree` 为兼容保留的历史代码名） |
| 会诊 | `GET/POST /api/consultations*` / 前端会诊页 |
| 位面快照 / 代码评估（实验） | `/api/universes` 可分支、diff、归档/恢复和查看代码评估；旧 `/switch` 与 `/promote-code` 固定返回 409，不会改变 live |
| 受治理演化（实验） | `/api/evolution` 查看 Candidate/Gate；授权的 canary/promote/rollback 走 `/api/evolution/candidates/{id}/...`。当前生产激活仅支持 Skill Candidate，Code live activation 不可用 |

删除任务是可恢复审计语义的归档（tombstone）：正常列表隐藏任务，但治理事件和幂等记录
仍保留；存在未结束执行时必须先取消，不能强删历史。

通知采用逐渠道 durable 进度：某次发送部分成功时，重试只会发送未成功渠道。成本账本
记录 prompt、completion、cache-read token 与取消前已发生的成本；它仍是基于 provider
上报用量的 best-effort 计量，不是 provider 侧硬额度。记忆的 Markdown 是真相源，
SQLite/FTS 是可重建索引；同步和删除使用稳定 entry ID，避免误删内容相似的条目。

## 6. 各接口面详解

HTTP/WS、CLI、Web 页面、通知渠道的完整接口契约见 [../design/interfaces/](../design/interfaces/)。
