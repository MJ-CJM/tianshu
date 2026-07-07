# Multica 借鉴分析

> 候选借鉴项目分析。与 [reference-projects.md](./reference-projects.md) 不同，本文记录的是**尚未落地、正在评估**的借鉴对象。
> 具体的落地实施计划见 [../superpowers/plans/2026-07-02-multica-inspired-control-plane.md](../superpowers/plans/2026-07-02-multica-inspired-control-plane.md)。
>
> 分析日期：2026-07-02（feat_phase8）。参考源：`/Users/chenjiamin/ai-example/multica`。

---

## 一、Multica 是什么

**一句话**：开源的 **Managed Agents 协作平台**——把编码 Agent 变成"团队成员"，让人和 AI 在**同一个 Issue 看板**上协作。名字取自 Multics（分时系统），寓意"人 + AI 在同一系统里多路复用"。

**关键定性**：Multica **自己不调 LLM**。它是一个**调度器 / 控制平面**——真正干活的是跑在用户机器上的 `claude` / `codex` / `copilot` 等 12+ 种 coding CLI 子进程。Server 只做任务编排、状态同步、数据存储。

**技术栈**：Next.js 16（Web）+ Electron（桌面）+ iOS（移动）+ Go 后端（Chi + gorilla/websocket + sqlc）+ PostgreSQL + 本地 Daemon。

### Multica 的 8 个核心设计（可提取资产）

| # | 机制 | 一句话 |
|---|---|---|
| 1 | **Runtime + Daemon 分布式执行** | agent 不在 server 跑，而在用户机器的 daemon 里跑；server 只调度。poll（3s 认领）+ heartbeat（15s）+ 隔离工作目录 |
| 2 | **Sweeper 后台自愈** | 三个后台 goroutine：标记离线 runtime、**回收孤儿任务**（dispatched>5min / running>2.5h 判失败）、GC 长期离线节点 |
| 3 | **Polymorphic Actor（多态行动者）** | 几乎所有"谁做了什么"都是 `actor_type(member/agent) + actor_id`。这是 agent 能像人一样建 issue / 评论 / 被 @ / 被订阅的根基 |
| 4 | **Autopilot 自动化** | cron / webhook / api 触发 → 自动建 issue → 派给 agent。带**并发策略（skip/queue/replace）** + run 记录 + **origin 追溯** |
| 5 | **Session Resumption** | 同一 `(agent, issue)` 复用 `session_id` + `work_dir`，保留上下文和文件状态 |
| 6 | **Inbox + 自动订阅** | creator / assignee / 被@ / 评论过的人自动进 `issue_subscriber`，事件推到个人 inbox |
| 7 | **WebSocket 房间模型** | 按 workspace 分房间广播 + `SendToUser` 定向 + server ping/client pong；60+ `domain:action` 事件；客户端"关键数据 patch / 次要数据失效重拉" |
| 8 | **Squads 小队路由** | 把任务派给"由 leader agent 带队的小队"，leader 判断谁接，扩容不改路由（`@前端组` 代替 `@张三`） |

---

## 二、定位对比：Multica 是控制平面，天枢是执行引擎

这一步决定"什么该借、什么是陷阱"。两者**不是竞品，而是 agent 系统的两个不同层**：

| 维度 | **Multica** | **天枢 Tianshu** |
|---|---|---|
| 本质 | **控制平面 / 协作层**（调度器） | **执行引擎 / 数据平面** |
| 调 LLM 吗 | ❌ 委托给 coding CLI 子进程 | ✅ 自己跑 ReAct 循环、调 LiteLLM |
| 核心价值 | 多人 + 多 agent 协作、分布式执行、产品化 | 强治理、强记忆、自进化、长任务自检 |
| 执行形态 | 分布式（daemon 在多台机器） | **单机进程内**（`executor/agent.py`） |
| 租户 | 多租户（Workspace 隔离） | 单租户 / 单组织 |
| 已借鉴项目 | — | Claude Code / Hermes / NanoBot / DeepAgents（**全是 agent 引擎类**） |

**核心洞察**：天枢过去所有借鉴都在"怎么把一个 agent 跑好"（引擎层）。而 **Multica 恰好覆盖了天枢的盲区——控制平面 / 分布式 / 协作 / 产品化**，正好命中天枢 **Phase 3（多 Agent + 分布式）**。所以 Multica 是天枢现阶段**最对口**的参考对象——但要借的是它的**控制平面设计**，而不是"把外部 CLI 当 worker"那套（与天枢自执行定位冲突）。

> 更高层判断：**Multica 印证了"控制平面（调度/治理/审计）与 数据平面（执行/调 LLM）分离"是 agent 平台走向规模化的成熟形态**。天枢当前把两者揉在一个进程里——治理和记忆做得很深，但执行层还是单机。沿这条线逐步把控制平面剥出来，与 Phase 3 方向完全一致。

---

## 三、借鉴矩阵

### ★★★ 强烈推荐（契合 Phase 3，代价可控，价值高）

| 项 | Multica 机制 | 天枢现状（证据） | 借鉴要点 |
|---|---|---|---|
| **#1 Sweeper 孤儿任务回收** | 后台 sweeper 回收卡死任务 | `scheduler.py:175` 的 `_review_timeout_loop` 只覆盖 `needs_review` 超时；Memorial 无 heartbeat 字段；`RUNNING/PLANNING/AUDITING` 卡死无自愈 | 扩展 sweeper 到活跃态卡死检测；接现有 orchestrator checkpoint resume / L0–L3 升级 |
| **#2 并发策略 + origin 追溯** | Autopilot `concurrency_policy` + `origin_id` | `_cron_loop`/`_interval_loop` 每次直接 `_emit_scheduled` **无去重**；系统 job（profile/skill/universe evolve）`asyncio.create_task` **fire-and-forget 无 run 台账** | 给定时/系统 job 加 skip/queue/replace + run 记录 + `origin_id` |
| **#3 Runtime/Worker 解耦** | server ↔ daemon 认领协议 | executor 进程内自执行，单机 | Phase 3 分布式主线；#1 #2 是其地基 |

### ★★ 值得考虑（取决于是否走"协作"方向）

| 项 | Multica 机制 | 天枢现状 | 借鉴要点 |
|---|---|---|---|
| **#4 Polymorphic Actor** | `actor_type + actor_id` 贯穿全表 | persona 是内部角色；Edict 有 `assigned_persona_id`；consultation 有多 persona 雏形 | 统一 Actor(human/persona/system) 抽象，支持官员互相委派并留痕，契合六部隐喻 |
| **#5 WS 房间模型** | 房间广播 + 定向 + 心跳 + 事件规范 | 有 `/api/ws` + EventBus（带 `edict_id`） | 按 edict/session 分房间；`domain:action` 命名；前端 patch/重拉分级；WS 保活 |
| **#6 Chat 层 + @提及触发** | 不依附 issue 的持久对话 | 有飞书/Telegram assistant mode | 用 `chat_session/chat_message` 建模统一轻量对话 |

### ★ 不建议照搬（甄别陷阱）

| 项 | 结论 |
|---|---|
| 把 Claude Code/Codex 当 worker 编排 | ❌ 与天枢"自己是引擎"定位冲突；LiteLLM 已解决模型中立 |
| Skill 从 URL/市场导入 | ⚪ 天枢 skills（渐进学习/修撰/guard/fuzzy）已远强于 Multica 静态文档 skill |
| Squads 小队路由 | ⚪ 天枢"吏部·铨选" + 内阁决策已有雏形，可作叙事参考 |
| 多租户 Workspace 隔离 | ⚪ 天枢当前单租户，SaaS 化才需要 |
| Electron 桌面 / iOS 客户端 | ⚪ 工程成熟度参考，要做客户端时再看 |

---

## 四、推荐优先级

按"价值/代价比"：

1. **#1 Sweeper 孤儿任务回收** —— 可靠性刚需，复用现有 `_review_timeout_loop` 模式，立刻收益。
2. **#2 并发策略 + origin 追溯** —— 补强现有 scheduler 明显缺口，代价低。
3. **#4 / #5**（择一）—— 视是否强化"多官员协作"叙事。
4. **#3 Runtime/Worker 解耦** —— Phase 3 分布式主线，前两步是地基，待 #1 #2 稳定后正式设计。

落地实施细节见 [../superpowers/plans/2026-07-02-multica-inspired-control-plane.md](../superpowers/plans/2026-07-02-multica-inspired-control-plane.md)。
