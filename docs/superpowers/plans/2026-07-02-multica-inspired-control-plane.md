# Multica 启发的控制平面演进 —— 落地方案

> 状态：**#1 #2 已交付（含 tests/ 回归）；#4 #5 细化设计文档已出**（2026-07-02）；#3 待立项。分析背景见 [../../reference/multica-analysis.md](../../reference/multica-analysis.md)。
> 日期：2026-07-02 · 分支起点：feat_phase8 · 阶段归属：Phase 3（多 Agent 与分布式）

## 总原则

借 Multica 的**控制平面设计**（调度自愈 / 并发治理 / 分布式解耦），**不借**"把外部 CLI 当 worker"（与天枢自执行引擎定位冲突）。四项按价值/代价比排序，**每项可独立审批、独立交付**。

| 项 | 主题 | 代价 | 依赖 | 建议轮次 |
|---|---|---|---|---|
| #1 | Sweeper 孤儿任务回收 | S | 无 | 本轮 |
| #2 | 并发策略 + origin 追溯 | S–M | 无 | 本轮 |
| #4 | Polymorphic Actor | M | 无 | 择一 |
| #5 | WS 房间模型 | S–M | 无 | 择一 |
| #3 | Runtime/Worker 解耦 | L | #1 #2 | 后续 |

---

## #1 Sweeper 孤儿任务回收（推荐首做）

### 问题与证据

- `scheduler.py:175 _review_timeout_loop` 已是一个内嵌 sweeper：每 300s 扫 `needs_review` 且 `completed_at` 超 1h 的 memorial → emit `review.timeout` 升级。**模式已被天枢接受，但只覆盖一个状态**。
- `Memorial`（`models/memorial.py`）有 `started_at`/`completed_at`，**无 `last_heartbeat_at`**。一旦进程崩溃或长任务 outer loop 悬挂，`RUNNING/PLANNING/AUDITING` 状态的 memorial **无人回收**，永久卡住。
- `orchestrator/loop.py:180` 已支持 `checkpointed`/`background` 的 `_load_checkpoint` **resume** —— 卡死任务可"自动续跑"而非只能判失败。

### 设计

新增一条 sweeper 循环（与 `_review_timeout_loop` 并列，或合并为统一 `_janitor_loop`），周期扫描活跃态 memorial：

```
每 N 秒：
  for m in memorials(status in {RUNNING, PLANNING, AUDITING}):
    idle = now - (m.last_heartbeat_at or m.started_at)
    if idle > threshold(status):
      if edict.execution_profile in {checkpointed, background} 且有 checkpoint:
        → emit edict.scheduled 触发 resume（复用现有 checkpoint 恢复路径）
      else:
        → 标记 FAILED（error="orphaned: no heartbeat for {idle}s"）
        → 按 review_policy 决定是否升级人工 / 走 L0–L3
```

- **心跳打点**：在 orchestrator 每次 `emit_audit`（outer loop iteration）和 agent 每轮 iteration 处更新 `memorial.last_heartbeat_at`。低频、零 LLM 成本。
- **阈值分级**：RUNNING/PLANNING 较短（如 10min 无心跳），长任务 outer loop 因单轮可能很久，用心跳而非总时长判定，避免误杀。

### 改动点

| 文件 | 改动 |
|---|---|
| `models/memorial.py` | +`last_heartbeat_at: datetime \| None` |
| `storage.py` | memorial 表 +列 + migration；新增 `list_stale_memorials(statuses, idle_seconds)` 查询；`touch_memorial_heartbeat(id)` |
| `executor/orchestrator/loop.py` + `executor/agent.py` | iteration 处打心跳 |
| `scheduler/scheduler.py`（或新 `scheduler/janitor.py`） | 新增 sweeper 循环；`start()`/`stop()` 挂载 |

### 验证

- 单测：构造 `RUNNING` 且 `last_heartbeat_at` 超阈值的 memorial → sweeper 判 FAILED。
- 单测：`checkpointed` 卡死 + 有 checkpoint → sweeper emit `edict.scheduled`（resume），不判失败。
- 单测：心跳新鲜的 RUNNING 不被误杀。

**代价：S**（~1 迁移 + 1 循环 + 心跳打点 3 处）。即使不做分布式也应做——是长任务/DAG 的可靠性刚需。

---

## #2 并发策略 + origin 追溯

### 问题与证据

- `_cron_loop`（`scheduler.py:448`）/ `_interval_loop`（:478）到点直接 `_emit_scheduled(fresh_edict)`，**不检查上一次触发的 memorial 是否还在跑** → 慢任务叠罗汉。
- `register_system_jobs`（:84）的 profile/skill/universe evolve 三个系统 job 用 `asyncio.create_task(...)` **fire-and-forget**：无 run 记录、无去重、异常只在外层 `logger.exception`。若 `universe.daily_evolve` 跑超 24h，次日又 fire 一个。
- `Edict.source`（`models/edict.py`）只有 `cli/api/channel/scheduler` 大类；**无 `origin_id`**（哪个 job / evolve run 触发）。

### 设计

**A. 定时/interval edict 并发去重**
`_Job` 增 `concurrency_policy: skip | queue | replace`（默认 `skip`）。fire 前查该 edict 是否有活跃 memorial（RUNNING/PLANNING/AUDITING）：
- `skip`：有活跃 → 跳过本次（记 skipped run）
- `queue`：排队，等上次终态后再 emit
- `replace`：cancel 上次（发取消信号）再 emit

**B. 系统 job run 台账 + 去重**
给 profile/skill/universe evolve 三个 job 包一层 run 记录（`job_run` 表：`id, job_name, status[pending/running/completed/failed/skipped], started_at, finished_at, error`）+ 同名 job 上次 `running` 未结束时按策略 skip。取代当前的裸 `create_task`。

**C. origin 追溯**
- `Edict` / `Memorial` 增 `origin_id: str | None`（cron edict 的每次 memorial、系统 job 的每次执行都带上触发源 id）。
- 前端调度台 / 审计台可据此下钻"这次执行由哪个规则/演化触发"。

### 改动点

| 文件 | 改动 |
|---|---|
| `scheduler/scheduler.py` | `_Job` +`concurrency_policy`；`_cron_loop`/`_interval_loop` fire 前去重；系统 job 包 run 记录 + 去重 |
| `storage.py` | 新增 `job_run` 表 + CRUD；`list_active_memorials_for_edict(edict_id)` |
| `models/edict.py` / `models/memorial.py` | +`origin_id`（可选） |
| `web/`（调度台/审计台） | 展示 run 台账与 origin（可后置） |

### 验证

- 单测：cron edict 上次 memorial 仍 RUNNING + policy=skip → 本次不 emit，记 skipped。
- 单测：`universe.daily_evolve` 上次 run 仍 running → 次日 fire 被 skip，落 skipped run。
- 单测：origin_id 从 job → memorial 正确透传。

**代价：S–M**（A 轻、B 需一张 run 表、C 是加字段 + 透传）。可拆成 A→C→B 分步交付。

#### 实际落地（2026-07-02，已交付）

- **A**：`EdictSchedule.concurrency_policy` 落 `skip`(默认去重)/`allow`(放行) 两档。`queue`/`replace` 需「延迟队列 / cancel 单个运行中 memorial」基础设施——语义上属任务队列，**收敛到 #3 Worker 队列**统一实现，避免造半成品。去重用新增的 `storage.has_unfinished_memorials`（非终态，比 `has_active_memorials` 的 submitted/running 更全）。
- **B + C 合并为统一台账**：C（origin 追溯）与 B（系统 job 台账）本质同为「每次调度触发的运行记录」，用**一张 `schedule_run` 表**承载——cron/interval 触发记 `fired`/`skipped`，系统 job 记 `running`→`completed`/`failed`/`skipped`（`has_running_system_job` 去重）。系统 job 由 fire-and-forget 改为 awaited 以追踪完成。
- **memorial.origin_id 透传**（每条 memorial 反查触发 run）需改 planner，价值增量小，留作后续；当前 `schedule_run` 表已可查每个 edict/job 的触发历史（含被 skip 的）。
- 落点：`models/edict.py`、`storage.py`（表 + CRUD + `has_unfinished_memorials`）、`scheduler/scheduler.py`（`_fire_scheduled`/`_skip_for_concurrency` + 系统 job 台账）。
- 验证：功能自测全过（`scratchpad/test_schedule.py`）；`pytest -k "scheduler or storage or edict"` → 248 passed 无回归。

---

## #4 Polymorphic Actor（方向级，择一）

统一 `Actor(kind: human|persona|system, id)` 抽象，用于 Edict/Memorial/事件的 `creator`/`actor` 字段；支持"官员 A 执行中 @官员 B 发起子诏令"并生成可追溯协作链。与六部隐喻天然契合，consultation 已有多 persona 雏形可复用。**代价 M**，价值取决于是否强化"朝廷协作"产品叙事。→ 细化设计见 [polymorphic-actor-design](./2026-07-02-polymorphic-actor-design.md)。

## #5 WS 房间模型（方向级，择一）

前端按 `edict/session` 订阅"房间"减少无关推送；事件命名规范化为 `domain:action`；前端明确 patch（issue/memorial/task 类）vs 失效重拉（次要数据）分级；WS 加 server ping / client pong 保活。**代价 S–M**，主要提升前端体验/性能。→ 细化设计见 [ws-room-model-design](./2026-07-02-ws-room-model-design.md)。

## #3 Runtime/Worker 解耦（方向级，后续）

把 executor 抽象成可远程的 Worker/Runtime：任务队列语义（复用 Memorial + claim/heartbeat，#1 已铺 heartbeat）+ 认领协议（poll/push）+ per-task 隔离工作目录（复用现有 SSRF/host 白名单，扩展到工作目录）。**代价 L**，是 Phase 3 分布式主线，**依赖 #1 #2 稳定**后再正式立项设计。天枢参考的 PicoClaw「WorkerPool + 工作区隔离」同源。

---

## 不纳入本轮（甄别陷阱）

- ❌ CLI-as-worker（编排外部 coding CLI）—— 与自执行定位冲突
- ⚪ Skill 市场导入 —— 天枢 skills 已更强
- ⚪ 多租户 Workspace —— SaaS 化才需要

---

## 审批清单

请勾选批准范围（可多选/组合）：

- [ ] **#1 Sweeper**（推荐首做，代价 S）
- [ ] **#2 并发策略 + origin**（推荐次做，代价 S–M；可再选 A/B/C 子项）
- [ ] **#4 Polymorphic Actor**（先出细化设计，暂不写码）
- [ ] **#5 WS 房间模型**（先出细化设计，暂不写码）
- [ ] **#3 Runtime/Worker 解耦**（仅立项，待 #1 #2 后设计）

> 交付方式建议：按 `.claude/rules` 工程约定（简洁优先、外科手术式改动、功能先行后补测试）。批准后我先做 #1，交付并自测通过后再进 #2。
