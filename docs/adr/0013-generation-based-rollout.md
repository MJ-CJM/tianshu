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

`run_system_bindings` 表达每个 `(memorial_id, attempt_id)` 与典制、朝的 insert-once
关联事实。典制、朝和 binding 不复用主键，也不向冻结的 `RunAssignmentV1` 增加字段。

### 2. 热更新只采用代际切换

可执行能力的更新采用 `stage → warm → activate → drain → dispose`：新朝预热并验证成功后
才切换 active 指针，新 continuity 使用新朝，已开始的运行继续固定旧朝；旧朝引用归零后
才释放。预热失败时 active 与 last-good 指针不动，回滚只切回 last-good。

不使用 `importlib.reload()` 或其他进程内模块 reload，不原地改写活体注册表或正在执行的
对象。变化发生在版本之间，不发生在活体对象内部。

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

- 插件或执行器更新的原子发布、运行归因与回滚单位是完整典制，实际承载单位是朝。
- 插件可以保持启用并锁定版本，同时由进化策略单独冻结其进化。
- 动态加载第三方代码、进程内 reload 和普通 Evolution 修改治理微内核均不由本 ADR 开放。
