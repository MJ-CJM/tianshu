# 实现总览

> 本目录解释当前源码“在哪里、怎么连起来”。功能是否属于发布承诺，以
> [`../CURRENT-STATE.md`](../CURRENT-STATE.md) 和
> [`../launch/capability-matrix.md`](../launch/capability-matrix.md) 为准。历史分支名、
> 旧行号和阶段报告不作为实现身份。

## 1. 当前运行边界

- FastAPI 装配入口：`src/tianshu/app.py`。
- 具体依赖装配：`src/tianshu/bootstrap/wiring_*.py`。
- 控制面持久化：单进程、single-node SQLite；当前 migration ledger 为 V1–V24。
- 正式根执行路径：managed Native，使用 durable outbox、RunState、attempt lease、
  heartbeat 与 fencing token。
- Web：`web/src/router/AppRoutes.tsx`；默认侧栏为五个用户目的地。
- 外部 Keqing CLI、位面/eval 等路由保留实验兼容，不进入默认黄金路径。

## 2. 启动装配

`lifespan()` 按 wiring 函数装配，关键依赖顺序如下：

1. Storage、migration ledger、EventBus、HookRegistry；
2. ToolRegistry、Skills、Persona、Memory/Drawer、PromptBuilder；
3. ConfigManager、ProviderManager、LLM usage observer；
4. Agent、Workspace、Worker/Lane、Executor、DAG；
5. Decision/Approval/Policy、Auditor、Notifier、Evidence、Cost；
6. RunDispatcher/Reconciler、Planner、ScheduledRunPreparer、Scheduler；
7. Channels、plugins、profile/skill/universe system jobs；
8. Scheduler 恢复持久 job 后才进入 ready。

`/health/live` 只回答进程是否存活；`/health/ready` 还检查 Storage、迁移、scheduler
恢复和常驻后台任务。`/health` 仅是旧 liveness 兼容。

## 3. 任务与执行

```text
API / tool / channel
  -> Edict + SUBMITTED Memorial + submission/outbox（应用服务事务提交）
  -> OutboxDispatcher
  -> Scheduler
  -> ScheduledRunPreparer
  -> schedule-run + runnable Memorial + attempt/outbox（调度事务提交）
  -> RunReconciler
  -> RunDispatcher 赢得 lease，持续 heartbeat
  -> managed planning
  -> Native Executor: single Agent / DAG / outer loop
  -> fenced terminal completion
  -> audit / evidence / cost / memory / notification
```

### 普通任务

支持立即、once、cron 和 interval。每次周期触发建立独立 schedule-run/Memorial/attempt，
以游标 CAS 和 deterministic identity 防止重放重复创建根执行；single-node 不宣称分布式
exactly-once。

### 长程任务

outer loop 或 `checkpointed/background` 只允许 immediate/once，并要求
`concurrency_policy=skip`。深度任务新建时落为 checkpointed，恢复时先读 durable
attempt/continuation，再兼容旧 outer-loop checkpoint。

- pause：当前轮边界生效；checkpointed/background 先保存 checkpoint；
- resume：恢复 active，进程重启后由 durable attempt 恢复；
- steer：先持久化，下一轮 actor 吸收，只有 checkpoint 成功才确认删除；
- terminal：actor 的明确 failed/cancelled 不让 critic 改写；
- checkpoint：最终 Memorial 终态和监督收口持久化后才清理。

### Edict 删除

`DELETE /api/edicts/{id}` 是 archive/tombstone：有未结束运行时 `409`；成功时列表隐藏并
取消 scheduler job，保留 Edict、事件、决策和证据。它不是物理抹除。

## 4. Scheduler

`src/tianshu/scheduler/scheduler.py` 与
`src/tianshu/application/scheduled_runs.py` 共同实现：

- create/list/reschedule/pause/resume/cancel/run-now/run history；
- IANA timezone，持久化 UTC；
- `misfire_policy=coalesce`，只补最近错过槽位；
- job 状态 `active/paused/completed/failed/cancelled`；
- `run-now` 要求 Idempotency-Key，不改变原 schedule；
- 启动立即把上个进程残留的 system-job `running` 台账标为 `failed`；
- once 正常触发标 `completed`，不再误标 cancelled。

详见 [`scheduling/`](scheduling/)。

## 5. 任务归属和管理面

`src/tianshu/authz.py` 和 `gateway/ownership.py` 统一使用 `Edict.submitter`：

- 普通 principal 仅能访问自己的 Edict、Memorial、Scheduler job、DAG、Decision 和
  Evidence；
- 越权资源返回 `404`，不泄露是否存在；
- `admin` 可跨提交者读取/控制；
- `submitter IS NULL` 的旧行对普通 PAT fail closed；
- SystemAudit、全局 audit/network、Worker、配置、记忆和全局成本是 admin 管理面。

## 6. SQLite V23/V24

迁移由 `storage/migration_ledger.py` 以
`schema_migrations(version,name,checksum,applied_at)` 管理。callback 不拥有事务控制权；
旧库只有在物理形状与权威 schema 语义等价时才可 adoption。

- V23 `0023_cost_cache_read_tokens`：`cost_ledger.cache_read_tokens`；
- V24 `0024_notification_channel_progress`：
  `internal_notification_deliveries.accepted_channels_json`。

通知 worker 每成功一个渠道就持久化一次 acceptance；后续 retry 跳过已成功渠道。全部渠道
accepted 后才标 delivered。accepted 只证明 adapter/provider 接受，不证明用户阅读。

详见 [`storage/`](storage/) 和 [`interfaces/`](interfaces/)。

## 7. LLM、成本与记忆

`LLMClient` 的 process-local usage observer 覆盖 Agent、Planner、Critic、Auditor、
Rubric 和会诊等调用。业务调用按 `(edict_id, memorial_id)` 聚合；取消也结算；多
provider/model 记为 `multiple`，cache-read tokens 持久化。无业务上下文的调用归
`__platform__`。

Markdown 是记忆真相源。新日志保存稳定 entry ID，多行可无损重建；删除先删 Markdown，
找不到真相源行时拒绝 index-only 删除。access policies 存在 app settings，重启保留。

详见 [`llm/`](llm/) 和 [`memory/`](memory/)。

## 8. Web 与实验路由

默认侧栏是中枢、御书房、朝堂、百司、天工院〔实验〕、内府六个一级入口。御书房包含全部敕令、
颁发敕令、钦天监、都察院；朝堂包含吏部、廷议、内阁；百司包含翰林院、鸿胪寺、
通政司；天工院包含演化司〔实验〕、诸界台〔实验〕、考功司〔试行〕、客卿馆〔实验〕；
内府包含藏兵阁、权印司、户部账房。颁发敕令隐藏专家参数；钦天监页面暴露完整管理与
run history；未知 URL 有三语 404；查询/路由错误可重试；DAG 到终态停止轮询。

`/keqing`、`/evolution`、`/universes`、`/evals` 在天工院中可达，`/session-rules` 在
内府的权印司中可达。Keqing 状态只报告 self-managed CLI credential；凭证网关固定
unavailable，开启请求返回 `409`。

## 9. 子系统索引

| 文档 | 实现范围 |
|---|---|
| [`agent/`](agent/) | Agent harness |
| [`auditor/`](auditor/) | 审计规则与复核 |
| [`bus/`](bus/) | EventBus |
| [`interfaces/`](interfaces/) | HTTP/WS、通知、IM、Web、CLI |
| [`llm/`](llm/) | LLM 与成本 |
| [`memory/`](memory/) | Markdown/SQLite/Drawer 记忆 |
| [`scheduling/`](scheduling/) | Scheduler 与 Planner |
| [`storage/`](storage/) | schema、ledger、领域 repositories |
| [`tools/`](tools/) | 工具、Policy、MCP |
| [`secrets/`](secrets/) | 密钥存储与迁移 |
