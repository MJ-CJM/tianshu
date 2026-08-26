# 当前插件扩展实现与支持边界

> **Status: Current source fact。**
> 本页描述当前 Tianshu 插件能力；目标态设计见
> [target-architecture.md](target-architecture.md)。

当前 `plugins` 能力是 metadata-only catalog：它只发现、校验和登记本地
`manifest.json`，不安装依赖、不 import `entry_point`，也不执行第三方插件代码。因此它是
实验性的插件清单，不是动态 PluginHost。

P2 新增的是另一条独立能力：**已经由受信任源码实例化的进程内对象**可以按 owner 登记、
通过 handle 安全释放。它没有改变 manifest catalog 的边界，也没有把第三方插件变成可安装、
可激活或可执行。

P3 又新增一条窄的运行面切片：`keqing:pi` 可以由内部 controller 按不可变 release 执行
stage → warm → activate → drain，并让运行中的 attempt 固定到同一代。它验证了代际机械，
但没有开放 stage/activate API 或 CLI，也没有把这一能力泛化成第三方 PluginHost。

P4a 已把每个 subject 的 EvolutionPolicy 合入集成分支。P4b 已由 PR #109 合入
`feat/plugin-v1`（merge `a8a03071`），完成 per-subject Skill canary：完整 assignment set
持久封存，运行时深冻结且 continuity sticky；
SystemSnapshot 的 `evolution_overlay_set` 只保存 canonical overlay 列表的 digest。fresh root
零 canary 不挂 governed assignment artifact，singleton 保留旧 artifact，N>1 只挂多值
assignment-set artifact。它仍不是第三方动态 PluginHost；Web 也只提供 evolution mode 与
max canary basis points 的严格 CAS 编辑，availability/source/curator protection 只读，
`pinned` 不表示版本锁定。

P5 已由 PR #111 合入 `feat/plugin-v1`（merge `567b028e`）：后台漂移巡检可产生
`CandidateKind.EXECUTOR`，候选经 Gate、per-subject canary、高危 Decision 和精确 generation
authority 后激活 READY 代，并可回滚到 last-good。它只覆盖 `keqing:pi`，不改变 manifest-only
catalog 与第三方代码不加载的边界。

P6 已由 PR #114 合入 `feat/plugin-v1`（merge `8f32cc4c`），把**天枢进程本身**
纳入独立 `scope=process` 的 SystemSnapshot generation：
启动时解析当前典制，复用 V32 active/last-good 指针。严格模式发现目标漂移时，不写 P6
admission 管理的 process snapshot/release/generation/pointer/audit，并在 routing audit、plugin
sync、Pi recovery 与 scheduler/run 前拒绝；admission 前既有的迁移、Persona/Provider/目录装配
写入不在该承诺内。非严格模式记录审计后推进；A→B 仍保留 A，B→C 后才回收 A。Pi
controller/reconciler、executor registry、authority 与 attempt binding 均显式排除 process 行。
它不是进程内 Python 热卸载，也没有虚构 clean-shutdown receipt；last-good 只表示“上一个成功
激活且仍保留的快照”。

P7 当前已完成开发、PR 待创建。它只冻结 Skills：legacy、单 subject 或多
subject 的受管 run 在每个绑定阶段最多构建一次不可变视图，并以
`off` / `shadow` / `enforce`
三档分离兼容观测与失败关闭。它不增数据迁移，也不冻结 Persona、Prompt 或 Provider。

本页合并原 `docs/design/plugins/` 与 `docs/impl/plugins/` 的内容，作为本报告目录中的唯一
插件现状说明。用户开发示例仍在 [扩展开发指南](../../usage/extension-guide.md)，但当前/目标
能力边界以本目录为准。

## 1. 当前支持矩阵

| 能力 | 状态 | 当前行为 |
|---|---|---|
| manifest 发现 | 可用 | `PluginLoader` 按目录顺序读取 `plugins/<name>/manifest.json` |
| manifest 校验 | 有限 | `PluginManifest` 校验 JSON 形状和 `type` 枚举 |
| 元数据登记 | 可用 | 名称、版本、原始清单和声明的 SHA-256 写入 SQLite |
| Web/API 查询 | 可用 | 只显示 `manifest_only`，明确 `loaded=false` |
| 动态安装 | 不支持 | `POST /api/plugins/install` 返回 `501 plugin_install_not_supported` |
| 激活/停用 | 不支持 | `PUT /api/plugins/{name}/status` 返回 `501 plugin_activation_not_supported` |
| entry point 加载 | 不支持 | 启动过程不会 import 或调用 `entry_point` |
| 依赖与指纹验证 | 不支持 | `dependencies`、`sha256`、`permissions`、`auto_install` 只是声明字段 |
| 六类受信任源码贡献 | 可用 | Tool / Hook / Channel / Provider / Skill / Command 注册返回 owned handle |
| owner 整体释放 | 可用 | `dispose_owner(owner)` 按注册逆序释放，返回 `(disposed, skipped_stale)` |
| stale 身份保护 | 可用 | 旧 handle 不会摘除后来注册的同名对象，并尽力写 `contribution_dispose_stale` |
| MCP session 工具清理 | 可用 | 重新发现、断连、重连与 shutdown 都撤回当前 session 的旧工具集合 |
| Pi 不可变 release / runtime generation | 内部可用 | 单发与 session adapter 同代物化；stage/warm/activate/rollback/recover 仅由内部组合根调用 |
| attempt 代际固定与 continuity | 内部可用 | exact attempt 租约、follow-up/基础设施重试/DAG root 继承；周期任务每次 fresh root 取当时 active |
| 代际生产写入口 | 受治理窄入口可用 | 无直接 GenerationController HTTP/CLI；EXECUTOR candidate 的 canary/promote 经 PromotionService 间接驱动 exact stage/warm/activate |
| per-subject Skill canary | 已合入 | V34 封存 1..64 条 assignment；PR #109 已合入 `feat/plugin-v1` |
| per-subject Pi EXECUTOR canary | 已合入 | PR #111；Challenger 绑定获授权 READY 代，champion 保持 active；缺失、错配、歧义或撤销 authority 失败关闭 |
| EXECUTOR 高危晋升与回滚 | 已合入 | PR #111；promote 永远需要精确已决议 Decision；只激活映射代；rollback 恢复 last-good 并撤权，可从 durable pending 状态继续 |
| EXECUTOR 前向演化开关 | 默认关闭 | 关闭只拒绝新的 start-canary/promote；adapter、existing generation recovery、rollback 和 reconcile 保留 |
| EXECUTOR 漂移巡检 | 默认关闭 | control-plane scanner 显式启用后提案；Keqing GET 只读，不扫描或创建候选 |
| process SystemSnapshot generation | 已合入 | PR #114；V32 `scope=process` 直接保存 canonical `SystemSnapshotV1`；启动幂等、漂移、回滚和 active/last-good 保留由专用 bootstrap 处理 |
| strict SystemSnapshot run binding | 默认关闭 | strict 时 resolver/持久化失败在 runner 前以 `system_snapshot_unavailable` 失败；非 strict 保留有审计的 shadow 行为 |
| process generation 只读投影 | 已合入 | Evolution API/Web 在有候选与空候选状态都显示 active/last-good；关闭 snapshot 时两者明确为 null |
| Skills 每 run 不可变视图 | 开发完成，PR 待创建 | P7 仅覆盖 Skills；`off` 不构建视图，`shadow` 构建/比对但 runner 仍读 live，`enforce` 将视图绑定到 run |
| Skills 视图身份与 prebind 漂移 | 开发完成，PR 待创建 | 当前 Skills 源摘要必须与该 run 的 SystemSnapshot `skills` 组件一致；shadow 审计后读 live，enforce 在 runner 前以 `skills_view_unavailable` 失败关闭 |
| 插件 enabled / version pin | 不支持 | P4b UI 不提供这两个开关；curator protection 不是版本 pin |

P4b 路由顺序是 existing replay → continuity inheritance → fresh-root kill switch。关闭 routing
只阻止 fresh root 新选 challenger，不切断已持久化 follow-up continuity。此时内部 Evolution
probe=false，但 `evolution.rollback` 是 optional readiness check；若没有其他 required failure，
整体 health 为 degraded，`/health/ready` 仍返回 HTTP 200，避免关闭可选自进化时摘除业务实例。

单个清单解析失败只记录 WARNING 并跳过，不影响主服务启动。这里的 fail-soft 仅适用于无副
作用的元数据发现；代码加载继续 fail closed。

## 2. 当前实现

代码位于 [`src/tianshu/plugins/`](../../../src/tianshu/plugins/)：

| 文件 | 当前职责 |
|---|---|
| [`manifest.py`](../../../src/tianshu/plugins/manifest.py) | `PluginManifest` 数据模型；entry point、依赖、权限和 SHA-256 均为声明字段 |
| [`loader.py`](../../../src/tianshu/plugins/loader.py) | `discover()` / `load_manifest()` 只读取并解析 JSON |
| [`api.py`](../../../src/tianshu/plugins/api.py) | 登记 manifest 元数据；为受信任源码装配提供显式 `register_*` 门面 |
| [`contribution.py`](../../../src/tianshu/plugins/contribution.py) | frozen `ContributionHandle`、三态 dispose 结果、默认源码 owner 与 stale audit |

仓库目前不存在 `PluginInstaller`，也没有第三方插件的通用 import、pip 安装、SHA-256 验证、
依赖解析、entry-point 生命周期、隔离执行或 generation 热替换链。受信任源码贡献与 MCP
session 工具则已经具备身份安全的进程内释放原语。

启动装配如下：

```text
各内建注册表就绪
  → 创建 PluginApi
  → PluginLoader(settings.plugins_dir).discover()
  → 对每个合法 manifest 调 register_plugin()
  → SQLite 记录 status=manifest_only
  → 结束；不解析或执行 entry_point
```

`plugins_dir` 来自 `TianshuSettings.plugins_dir`，支持 `~` 展开。发现顺序使用 `sorted`
保持确定。

## 3. API 与 Web

| 路由 | 行为 |
|---|---|
| `GET /api/plugins` | 返回清单目录，强制 `status=manifest_only`、`loaded=false` |
| `GET /api/plugins/{name}` | 返回单条清单目录记录 |
| `POST /api/plugins/install` | `501 plugin_install_not_supported` |
| `PUT /api/plugins/{name}/status` | `501 plugin_activation_not_supported` |

Web 只展示“仅清单”和发现时间，不把数据库中的历史 `active` 值解释成代码已经加载。对应
边界由 [`test_plugin_manifest_api.py`](../../../tests/gateway/test_plugin_manifest_api.py)
锁定。

## 4. 受信任源码扩展

`PluginApi.register_tool/hook/channel/provider/skill/command` 可以把已经由内建代码实例化的
对象交给相应注册表，并返回 frozen `ContributionHandle`。这是程序化扩展门面，不是
manifest 自动加载路径。

调用方应传入稳定、可追踪的 `owner`；缺省 `plugin:anonymous` 只为兼容既有源码调用。
`handle.dispose()` 可重复调用：首次成功返回 `disposed`，旧身份被替换时返回
`skipped_stale`，已经释放或无对应注册表时返回 `noop`。`dispose_owner(owner)` 按注册逆序
释放该 owner 的全部贡献并返回 `(disposed, skipped_stale)`。

身份校验按注册表能力落实：Tool / Channel / Skill / Command 同时核对当前对象身份；Hook
为每次 contribution 建立唯一 wrapper，再复用 handler identity 注销，因而两个 owner 复用
同一原 handler 也不会相互误删；Provider 持久化到 SQLite、没有对应内存槽位，因此只按
PluginApi 的 owner/current-handle 记账，释放结果照实透传底层——demo mode 不删除 Provider
行并返回 `noop`。旧 handle 遇到同名新对象时不会摘除新对象，并尽力
记录 SystemAudit `contribution_dispose_stale`；审计写失败不阻断释放流程。

若底层注销本身抛异常，handle 不会被提前标记完成，owner 账本也不会丢弃失败项或尚未处理
的较早贡献；调用方可在故障解除后再次 `dispose_owner`，继续按逆序释放。

显式使用这些门面的调用者仍需在源码和部署流程中承担：

- 导入、实例化和版本兼容；
- Policy、Decision 和凭据边界；
- 失败、关闭和测试责任。

P2 已补齐 Tool / Channel / Skill 的注销原语和六类贡献的 owner/disposer；P3 则为
`ExecutorAdapterRegistry` 增加了 generation-owned bundle、exact-attempt lease 与同锁 selection，
但只服务 `keqing:pi` 的内部代际切片。当前仍无第三方依赖闭包、隔离和 entry-point 生命周期，
因此不能把这些 `register_*` 方法或 Pi 代际描述成目标态 PluginHost。

### 4.1 MCP session 生命周期

MCP 工具以 `mcp:<server>` 归属。每次工具重新发现时，manager 先释放该 session 的上一组
handles，再登记新集合；连接离开 connected 状态、重连和 shutdown 都撤回当前集合。释放
使用 owner + handler identity 校验，所以旧 session 不会删除后来注册的同名工具。管理 API
重启 session 只调用 manager 的 shutdown/start，不再直接修改 `ToolRegistry._tools` 私有字典。

## 5. P1 典制影子归因

P1 在不打开动态插件加载的前提下，新增了 `SystemSnapshotV1`（典制）的内容身份和影子绑定。
它解决的是“这次运行实际看到了哪些系统内容”，不是“如何安装或热替换第三方插件”。

| 组件键 | P1 的实际内容投影 |
|---|---|
| `kernel` | 天枢版本与共享 dependency-lock 标记 |
| `executor:<adapter_id>` | 当前注册表中每个执行器 manifest 的内容摘要 |
| `skills` | 各搜索层的 `SKILL.md`、`scripts/references/assets/templates` 资源及受信任源码注入的 Skill 内容 |
| `personas` | 当前在编 Persona 的语义字段及其 runtime `SOUL.md` / `ROLE.md` 内容 |
| `policy_rules` | 内建规则 id 与 priority 的确定性投影 |
| `provider_profiles` | 内建 Profile 与持久 Provider 的无明文密钥、无时间戳语义投影 |
| `evolution_overlay` | 仅 governed assignment 存在；legacy assignment 明确省略 |
| `prompts` | schema 已预留，本阶段不填 |

V31 `0031_system_snapshots` 用不可更新、不可删除、不可 replace 的内容寻址表保存典制，
`run_system_bindings` 按 `(memorial_id, attempt_id)` insert-once 记录运行事实。成功绑定会在
Evidence 中增加 `application/vnd.tianshu.system-snapshot.v1+json` required artifact；assignment
只读 API 和 Edict 详情可以投影同一内容摘要。

P1 写侧最初是 **shadow / 影子模式**。P6 PR #114 已让 `system_snapshot_strict=true` 真正
在 resolver 或 authoritative binding 失败时于 runner 前拒绝，并保留默认关闭的兼容路径；非严格
模式仍尽力写 SystemAudit 与 durable outbox 后继续。它仍不等于完整运行内容已经全部可归因：dependency-lock 目前仍是零值占位，
policy 只覆盖 id/priority，prompt key 尚未填充。P3 已让非空 generation tuple 在绑定与执行面
fail closed，但 P1 的典制写入本身仍是影子模式；系统仍没有动态加载、第三方依赖闭包、
Canary 切换或第三方插件级热替换。P2 的受信任 contribution 也尚未接入
Candidate → Generation → Canary → Promotion。

## 6. P3 Pi 运行代际的准确边界

V32 `0032_runtime_generations` 共增加五张表：不可变 release、运行代、不可变 journal、
active/last-good pointer，以及独立 insert-once 的 `run_generation_bindings`。
`PiReleaseMaterializer` 从持久化的绝对 binary path、完整受管 package、
版本与来源、binary/package digest、manifest、argv shape、wire version 和 materializer 版本重建
同一份单发/session bundle；恢复时不会重新 `which()` 后静默换用另一份 Pi。

内部 `GenerationController` 负责 stage、warm、activate、rollback 和 recover：外部 probe 与材料
校验不占用 SQLite 写事务；active 切换、旧代 draining 和 pointer CAS 在同一事务内完成。
warm、activate、rollback、重启恢复和每次 pinned Pi 执行都会复核材料。commit 后 registry 发布
若短暂失败，会按 durable truth 严格核对 identity 后收敛；身份漂移、仍有 lease 或持续发布失败
不会被伪装成 ready。这里证明的是数据库状态切换的原子性与检查到的材料一致性；材料复验发生
在 SQLite CAS 事务之外，不能宣称对可并发改写同一路径的同 UID hostile writer 存在
“verify 与 CAS 不可分割”的原子保证。宿主目录与同 UID 写权限仍属于部署信任边界。

Router 在第一个受管副作用前把每个新 attempt 的实际 generation 选择写入
`run_generation_bindings`；即使选择为空也显式写 `bound []`。这是 P3 的 exact-attempt 代际权威。
snapshot 启用时还可写 `run_system_bindings` shadow；它只作为典制归因与 V31 历史 fallback，
两者同在时 generation ids 必须一致。snapshot 关闭时 `run_system_bindings` 为零行，但新的
attempt marker 仍存在。同 Memorial 的新 attempt、follow-up 和 DAG root/child 保持 continuity；
cron/interval 每次触发由 scheduler 创建无 parent 的 fresh root，并在推进调度 cursor 的同一事务内
预绑定触发时 active，因此不会继承上一次 fire。每个 OPEN Edict 仍保留最新 root binding，目的只是
封住完成后到潜在 follow-up 创建之间的 continuity gap；下一次周期 fire 会以自己的新 binding 取代它。
进程内 registry 只按 exact attempt id 记 lease，唯一释放点是 dispatcher 的 `finally`。
已固定代若 failed/disposed、材料不可读或内容漂移，运行以 `generation_retired` 失败，并在当前
fence 完成成功后追加脱敏 SystemAudit 与 durable outbox。

`GenerationReconciler` 只回收既非 active/last-good、又没有 exact-attempt 或 OPEN Edict 最新 root
continuity 引用的 draining 代；启动只物化 durable retained roots，遗留 pre-active 记录按 P3
规则失败化。它以同锁原子 `(ready,error_codes)` 快照参与 `/health/ready`：活动代或仍被 continuity
引用的旧代材料缺失/不匹配、journal 断链与探针异常是 required failure（HTTP 503）；只有无引用
draining/terminal bundle 待清理是 optional degraded（HTTP 200）。全新数据库没有 pointer 时继续走
原 static adapter，live/demo 行为不变，状态页的 generation 为 `null`。

V32 升级对既有 attempt 做三分回填：已有 `run_system_bindings` 就复制其 generation ids；能由
governance contract/runtime override 证明为非 Pi 就写 `bound []`；无法可靠还原历史 Pi 选择则写
`unresolved`。后者不会猜测 active，也不会静默退回 static；continuity/retention 读取会失败关闭。

这套能力当前**不包含**：自动下载/升级 Pi、直接公开 GenerationController stage/activate 接口、
任意第三方 package、进程内 Python contribution 热替换，以及 Tool/Hook/Provider 等 built-in
对象的多代并存。P5 已为 Pi canary READY 代持久化精确 candidate/version/release/generation
授权并纳入 routing、recovery 与 retention root；无映射、映射歧义、摘要不符或已撤销的 READY
仍失败关闭。

## 7. P5 Pi executor 治理垂直切片

V35 `0035_executor_candidate_kind` 把 `executor` 加入候选 kind，并在 `foreign_keys=ON` 下按依赖序
连带重建候选表与六张子表。`executor_generation_authorities` 保存每个候选当前 epoch 的精确
授权，append-only journal 保存全部状态变化；二者都不能替代 Candidate、Decision 或 Promotion
journal 的权威。

`ExecutorDriftScanner` 由 control-plane tick 低频驱动，比较当前 active release 与重新物化的
本机 Pi release。它有独立默认关闭开关、确定性提案身份与进程内限流；相同 base/challenger
不会重复产生候选，frozen policy 在 artifact 写入前拒绝。`GET /api/keqing/status` 只投影最近一条
durable executor candidate，页面只负责链接到演化中心。

EXECUTOR `start_canary` 是异步 saga：先持久 intent，再 stage、warm 和 authorize exact READY
generation，最后才让 challenger routing 可见。Canary 期间 challenger run 只能绑定该 READY 代，
champion run 继续取 active pointer；promote 要求高危 Decision，并只激活既有映射，不二次 stage。
Canary rollback 先撤流量和 authority，promoted rollback 恢复 exact last-good pointer。未收口的
start/promote/rollback 形成 subject fence，避免新代越过旧代的外部效果窗口。

`executor_generation_enabled` 是**前向演化 kill switch**。关闭时，新的 start-canary/promote 在
新 journal/effect 前返回 `executor_generation_unavailable`，但 `ExecutorPromotionAdapter` 始终装配；
既有 generation recovery、已发生效果的幂等收口、canary/promoted rollback 与 pending rollback
reconcile 继续可用。这个区别保证“停止继续进化”不会同时拆掉安全回退路径。

同命令 executor canary 的 single-flight 锁是进程内边界；durable journal 和 CAS 仍负责重启重放与
冲突拒绝。当前正式运行模型本来就是 single-host、single-node、single-process SQLite，不由此
扩展为多进程或分布式编排承诺。

## 8. P6 进程 SystemSnapshot generation 与严格绑定

P6 已由 PR #114 合入 `feat/plugin-v1`（merge `8f32cc4c`）。它不新增迁移，复用
V32 五张 generation 表。`runtime_generation_releases` 的 process 行直接保存
canonical `SystemSnapshotV1`，且 `release_digest == snapshot.digest`；`RuntimeReleaseV1` 继续只
表示 executor release，repository 以 `insert/get_process_release` 窄接口和 scope-aware decoder
阻止两类材料互相解释。

`ProcessSnapshotBootstrap` 在完整 resolver 与 executor 装配之后、任何 scheduler/run 工作之前
执行。相同内容重启不新建 generation；接受的新内容按 STAGED→WARMING→READY→ACTIVE 原子推进。
active 与 last-good 只保留两代；返回已保留的 last-good 使用 repository rollback。严格漂移在
写入 snapshot/release/generation/audit 前拒绝，未知显式目标返回 `target_snapshot_unavailable`；
非严格漂移记录 `system_snapshot_drift` 后继续。这里没有 clean-exit 标记，所以文档不得把
last-good 写成“最近一次干净退出”。

process generation 不进入 Pi materializer、executor registry、authority、attempt binding 或 Pi
readiness。`GenerationController` 与 executor `GenerationReconciler` 的生产组合点只管理
`executor:keqing:pi`，保留根扫描也按 scope 隔离。运行绑定的 strict 失败由 dispatcher 归类为
稳定、脱敏的 `system_snapshot_unavailable`；默认 strict-off 保留历史 shadow 兼容。

Evolution Center 仅投影 process active/last-good generation id，不提供切换按钮。关闭
SystemSnapshot 时即使数据库留有旧 process 行也返回 null。P6 不做任意 Python
模块 reload/unload。

## 9. P7 Skills 每 run 冻结视图

P7 当前已完成开发、PR 待创建。`FrozenSkillV1` / `FrozenSkillsViewV1` /
`FrozenContentViewsV1` 将实际 Skills 读取面深冻结；loader 的详情、列表、index、always、
all、tool 与 workspace overlay 语义共用同一 task-local 视图。legacy、单 subject 和多
subject governed run 都覆盖，嵌套、异常和取消后恢复外层 context。

两个默认为 false 的开关构成三档：

| 模式 | 配置 | 运行语义 |
|---|---|---|
| `off` | `frozen_content_views=false` | 不构建 frozen view，loader/run 继续读取 live；watcher 保留 legacy invalidate + reload 语义，但 observer backend 与其他模式统一为 polling |
| `shadow` | `frozen_content_views=true` 且 `frozen_content_views_enforced=false` | 每次 bind 构建，并在 SystemSnapshot 身份可用时对比 Skills 源摘要；真实漂移原子写 `skills_view_drift` 审计/outbox，runner 继续读 live |
| `enforce` | 两开关均为 `true` | 为整个 run 绑定该视图；已解码的 Skills 身份缺失、视图构建失败或摘要漂移在 runner 前以 `skills_view_unavailable` 失败关闭 |

真实 digest mismatch 记 outcome=succeeded 的 `skills_view_drift`；视图工厂、整体捕获、
模型校验等致命失败，以及 enforce 下已解码 Skills 身份缺失，记 outcome=failed 的
`skills_view_binding_failed`。shadow 在身份缺失时只跳过对账，不写 failed 事件。单个
SKILL.md 解析异常仍沿用 loader 的 warning + skip，不会仅因此产生 binding-failed 审计。
两类审计均以 audit + outbox 在当前事务原子写入，不记 Skill 内容或原始异常。
持久 SystemSnapshot/run binding 若结构损坏，则在进入上述 P7 对账前沿用 P6 稳定分类：
strict 为 `system_snapshot_unavailable`，否则为 `generation_binding_unavailable`；该类错误
不伪装成 `skills_view_unavailable`，也不写成 Skills view drift。

`freeze_view()` 不复用 L1/L2/digest cache。每轮全量 capture 通过已打开目录 fd 读取
SKILL.md 和资源；搜索路径祖先只记录 `dev/ino/mode` identity witness，搜索根与捕获树内
文件/目录记录 `dev/ino/mode/size/mtime_ns/ctime_ns` stability witness，从而不把树外 sibling
churn 误判为 Skill 变化。同时纳入 injected generation；注入 Skill 按名称稳定排序。只有连续两次全量
capture（包括 witness、内容、层次与 injected generation）完全一致才接受。最多尝试三轮，
持续 churn 后以 `skills_view_unavailable` fail closed；晋升 exchange 后即使旧目录随即
cleanup，也不会拼出 mixed view。

捕获从搜索目录的每一级路径组件开始固定身份；搜索路径、Skill 目录或成员，以及
`scripts/`、`references/`、`assets/` 内任意层级的资源 symlink 都会失败关闭，不会跟随到
捕获树外。live 与 frozen 还保持同一套 `requires.bins` / `requires.anyBins` /
`requires.env` / OS、`always`、原始字节大小上限、`load_all`、metadata、injected、workspace
fallback、覆盖优先级和字符预算语义。高优先级 Skill 即使因 requirements 或大小不进入
`load_all`，仍按 live 语义成为详情/metadata 的有效层，较低层满足条件的 `load_all` 内容则
继续显露。requirements 的**环境判定结果**会以 `load-all-eligible` 进入 `source_digest`；因此
所需环境变量的存在性或二进制/OS 可用性变化并使 eligibility 判定翻转时，即使 SKILL.md
字节未变，也会形成新的来源身份，而不是把影响执行面的环境漂移藏在同一个 snapshot
digest 下。

`state=absent` 当前是 assignment-aware 的运行时覆盖语义，不是已经落库的全局删除标记：当
选中的是 base/champion 的 absent 时，只撤去该受治理目标层并显露更低优先级来源；当选中
challenger，或旧兼容记录无法判定 assignment 时，继续保留历史 tombstone 语义并隐藏低层。
新的 absent candidate 在 start-canary、promote 和 adapter activate 前统一以稳定错误
`skill_absence_requires_durable_tombstone` 拒绝，且不产生路由或晋升副作用。可跨 assignment、
重启和来源层稳定隐藏的 durable global tombstone 尚未实现，明确延期到 P7b；不得把当前
历史兼容覆盖解释为已经支持新的 Skill 删除晋升。

enforce 的 prebind 失败不会在 audit/outbox 尚未提交时直接逃出 caller-owned UoW。router
只有在 `skills_view_binding_failed` / `skills_view_drift` 的 audit + outbox 成功写入后，才把
失败登记为 UoW post-commit failure；调用方业务写入与证据在同一事务成功提交后，`commit()`
才抛稳定错误。若审计/outbox 写入失败，则不登记 post-commit failure，原异常立即退出并使整笔
caller 事务回滚。未登记该 failure 的默认 UoW 提交/回滚语义不变。

定时触发还区分“证据已提交后的绑定失败”和“提交前证据写失败”。前者由
`ScheduledFireBindingUnavailable` 携带已提交的 `PreparedFire` 返回给 scheduler：interval/cron
循环保持存活并采用已经持久化的下一 cursor，once/immediate 只在提交后消费 initial root，
`run_now` 不移动原定时 cursor；只要有 claimable attempt，scheduler 都显式唤醒 durable run
reconciler，而不会把 job 错标为 failed 或再补一条虚假的 failed `schedule_run`。后者仍是普通
`FrozenContentViewUnavailable`：整个 fire 事务回滚，cursor 与 initial root 原样保留，不唤醒
reconciler，循环退避后可在同一 fire identity 上恢复。

同 idempotency key 重试只有在 attempt 仍为 `claimable` 且已有 P7 failure/drift outbox marker
时才重新 prebind/freeze；终态 attempt 的精确幂等重放直接返回既有结果，不重新冻结或拿当前
Skills 去改写历史语义。claimable attempt 重新对账成功时会在同一事务写
`skills_view_binding_recovered` audit/outbox，关闭该 attempt 的重试 marker；恢复审计本身写入
失败也必须回滚，不能把“已经恢复”只留在进程内。

`SkillsWatcher` 在所有模式都使用 `PollingObserver`，避免 macOS FSEvents 在 Skill 原子目录
交换及 observer start/stop 竞态中直接终止进程；frozen-mode wiring 的回调路径只失效缓存并
通知，不预加载 live active 视图，`on_change=None` 的 legacy 路径仍保留 debounce 后 reload。
stop 之后排队的旧 generation callback 也会被丢弃。Skill 成功 activate/rollback 仍显式失效
缓存。promotion 的 cache invalidator 无论 frozen flag 是否开启都
装配；`verify_rollback()` 命中已恢复 base 时也失效缓存。若 exchange 已成功但 cleanup 等后续
步骤让结果返回不确定，重试在 reconcile 后发现 live 已是 desired/no-op，仍主动 invalidate，
保证缓存最终收敛。
P7 保证同进程 mid-run 稳定，但不保存旧 Skills
字节。无 prebind 的 run 只冻结一次；生产 prebind + dispatch 是两个独立阶段：prebind
最多冻结一次完成身份捕获，dispatch 最多冻结一次完成执行重建，不复用同一 view。两阶段可
跨进程，在 P7b 持久旧内容前，重建并比对是
fail-closed 约束。prebind 或重启后若只剩旧 SystemSnapshot，shadow 会审计漂移并保持 live，
enforce 会 fail closed；两者都不会静默把“旧 snapshot + 新 view”配成一次受管运行。
跨进程耐久回放旧内容所需的 artifact-backed `skills_view` 属于 P7b，因会扩大
持久化、retention、配额、secret scanning 和 rollback 契约而明确延期。
上述稳定捕获依赖本地 POSIX 文件系统能为普通写者提供可信的 stat/ctime 变化；它不抵抗
host administrator、同 UID 特权/恶意写者伪造 witness，也不对 ctime 不可靠的文件系统作承诺。

## 10. 为什么当前不开自动加载

执行第三方入口前至少需要完成：

- 可安装来源、依赖锁定、内容寻址、签名和 provenance；
- API/ABI、Host 版本和状态 schema 协商；
- owner/disposer 基础原语已经完成；仍需 entry point 生命周期、健康检查、依赖闭包和隔离；
- Tool、Hook、Channel、Provider、Skill、Command 的 Capability 与冲突规则；
- 文件、网络、Secret 和资源配额；
- generation 并存、warming、Canary、last-good 和回滚。

这些边界完成前，“发现了清单”不能展示为“插件已安装或激活”。从当前能力到目标态的迁移
顺序见 [migration-roadmap.md](migration-roadmap.md)。
