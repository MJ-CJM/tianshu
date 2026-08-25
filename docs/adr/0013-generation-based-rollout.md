# ADR-0013:以典制与朝实施代际发布

- 状态:已接受
- 日期:2026-08-25
- 相关:[ADR-0011](0011-keqing-external-executor-shadow-snapshot.md)(客卿治理边界)、[ADR-0012](0012-decision-terminology-not-zhupi.md)(裁决术语)

## 背景

天枢需要让插件与可进化能力在不中断既有运行的前提下迭代，同时保持运行归因、
回滚和治理权威可验证。进程内模块 reload 会改动活体对象，无法可靠证明旧任务继续
使用原实现，也无法为一次完整运行给出稳定身份，因此不作为热更新机制。

项目中“快照”还分别指位面快照、影子快照和恢复点。目标架构必须为完整系统内容、
实际运行实例和进化治理规则采用互不混淆的 canonical 术语与身份。

## 决策

### 1. 冻结三个 canonical 术语

- **典制 (`SystemSnapshot`)**：完整、不可变、内容寻址的有效系统配置身份，以组成项的
  canonical digest 标识；同一内容只有同一典制身份。
- **朝 (`RuntimeGeneration`)**：某一典制在具体运行宿主上物化出的运行实例，以独立的
  `rg-` 身份标识，不复用典制 digest；同一典制可以产生多个朝。
- **进化策略 (`EvolutionPolicy`)**：针对单个插件或进化对象约束可变化范围、模式、预算、
  裁决和回滚要求的治理规则；它与插件启用状态和版本锁定相互独立。

运行关联事实分成两个正交记录：V31 `run_system_bindings` 是 snapshot 启用时的典制 shadow；
V32 `run_generation_bindings` 是每个 `(memorial_id, attempt_id)` 的 insert-once 朝选择权威。
前者只作为 V31 历史 generation fallback；两者同在时 generation ids 必须一致。典制、朝和
binding 不复用主键，也不向冻结的 `RunAssignmentV1` 增加字段。

### 2. 热更新只采用代际切换

可执行能力的更新采用 `stage → warm → activate → drain → dispose`：新朝预热并验证成功后
才切换 active 指针，新 continuity 使用新朝，已开始的运行继续固定旧朝；旧朝引用归零后
才释放。预热失败时 active 与 last-good 指针不动，回滚只切回 last-good。

不使用 `importlib.reload()` 或其他进程内模块 reload，不原地改写活体注册表或正在执行的
对象。变化发生在版本之间，不发生在活体对象内部。

### 2.1 发布物与运行实例分离

P3 将可物化的运行发布建模为不可变、内容寻址的 `RuntimeRelease`，将某 scope 对该发布的
一次部署建模为 `RuntimeGeneration`。release digest 必须覆盖完整 manifest 与其摘要、CLI
版本来源、解析后的绝对二进制路径、二进制或 package 内容摘要、单发/会话 argv shape、
Pi wire version 和 materializer 版本。重启只能按已持久化 material 验证并重建，不得重新
按 `PATH` 解析后静默替换。一个 release 可以产生多个朝，因此 release 与 generation 不
共用主键，也不把完整 material 重复嵌进每一条 generation 记录。

### 2.2 切代与回滚是数据库原子操作

新 bundle 必须先在事务外完成 materialize 与 warm。activate 随后在同一
`BEGIN IMMEDIATE` 事务中按顺序执行：旧 active 置 draining、新 ready 置 active、最后 CAS
更新 active/last-good pointer。SQLite 事务外读者只会看到完整旧态或完整新态；不得先切
pointer，也不得先产生第二个 active。

常规状态图只允许 `draining → disposed`。`draining → active` 是
`rollback_to_last_good()` 的专用权威边，目标必须等于 pointer 的 last-good；失败的新代
不得成为 last-good。active 与 last-good 都是回收根，普通 reconciler 永不 dispose 它们。

### 2.3 引用按 attempt，连续性按现有谱系推导

运行期 lease 以精确 `attempt_id` 为身份，避免同一 Memorial 的基础设施重试产生 ABA；
Dispatcher attempt 外层退出是唯一权威 release 点，DAG 子节点复用 root attempt lease。
不持久化整数 refcount，durable 引用由 `execution_attempts` 与 insert-once
`run_generation_bindings` 推导；V31 历史记录尚无 marker 时才回退读取 `run_system_bindings`。

root 运行完成后，OPEN conversation Edict 仍可能在未来接收 follow-up，因此该 Edict 最新
root marker 的朝继续形成 retention，直至 Edict 关闭或取消。cron/interval 每次 fire 是
新 continuity，只在触发时选择 active；保留旧朝不等于把未来定时触发固定到旧朝。
`run_generation_bindings` 是 attempt 选择事实，不是 session、谱系或整数 refcount 账本；首期
不另建这三类 continuity 真相。跨 Edict session 成为真实需求时再另立 ADR。

### 3. Ring 0 不进入普通进化

Edict、Memorial、Attempt、Decision、Evidence、Effect journal 和 Promotion Authority 构成
治理微内核（Ring 0）。普通 Evolution 不能修改、替换或放宽这些权威，也不能改变自己的
Evaluator、权限、进化策略或晋升权威。

本轮不实现 `auto` 进化模式，也不允许自动晋升。可表达的进化模式仅为 frozen、manual、
canary；既有 `automatic_promotion_allowed: Literal[False]` 保持不变。

### 4. 依赖边界分阶段收紧

目标依赖方向是入口与执行面依赖应用/进化/证据/插件面，后者再依赖存储与治理微内核，
底层不得反向依赖上层。P0 不为满足理想分层而重排既有 import；当前已知例外包括：

- application 依赖 executor 和 universe；
- evidence、evolution 和 plugins 依赖 executor（plugins 经 tools registry 间接依赖）；
- models、storage 和 skills 依赖 evidence，skills 还依赖 evolution。

这些边只登记为存量例外，不授权新增同类依赖。P0 先用可通过的 import-linter forbidden
契约守住无冲突边界；后续阶段逐项解耦，最迟在 P7 前清零本 ADR 的例外清单并收紧为完整
层级约束。

## 影响

- release 是不可变内容单位，`(scope, RuntimeGeneration)` 是 rollout/rollback 单位，
  `SystemSnapshot + exact attempt generation marker` 是一次运行的完整归因单位；snapshot 关闭时
  marker 仍可独立证明该 attempt 选择了具体 generation set（包括显式空集合）。
- 插件可以保持启用并锁定版本，同时由进化策略单独冻结其进化。
- 动态加载第三方代码、进程内 reload 和普通 Evolution 修改治理微内核均不由本 ADR 开放。
