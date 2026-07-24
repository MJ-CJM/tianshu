# grok-build / pi 借鉴分析与客卿抽象层设计

> 2026-07-23。基于多智能体深读（grok-build：7 域探索 + 天枢测绘 + 综合 + 三视角批判审查，12 agents；pi：8 份深读报告），结论已经过与天枢真实代码现状（KeqingExecutor、security/redact.py、CandidateService、WorkspaceApplyEngine、shadow_snapshot 等）的逐条核对。
>
> 三个交付物：① grok-build 借鉴清单；② pi 借鉴清单；③ 客卿抽象层设计——自进化能力外包给 coding agent 且**不绑定任何具体 agent** 的实现方案。

---

## 一、两个项目定性

| | grok-build | pi (pi-mono) |
|---|---|---|
| 出品 | SpaceXAI（Grok CLI） | earendil-works / badlogic |
| 形态 | 生产级终端 AI 编码 agent | 极简自扩展 agent harness |
| 规模 | Rust，~130 万行，70+ crate | TypeScript，~12 万行，6 包 |
| 哲学 | 全家桶内置（sandbox/hooks/插件市场/遥测/更新器） | library-first；权限系统刻意不做（外包容器化） |
| 对天枢 | **「被治理对象」的工业级上限——学「管什么」** | **「控制平面」的意外蓝图——学「怎么管」** |

一个战略级发现：grok-build 的 `[compat.claude]` 兼容矩阵把 `~/.claude` 的 skills/rules/hooks/MCP/权限/会话六格全部机读吸收并注入——**直接印证「Claude Code 上级机关」定位的技术可行性**：Claude Code 的全配置面可以被外部程序下发与回收。

天枢测绘确认的第一 gap：下游客卿（Claude Code/Codex）执行是黑盒——无会中工具拦截、无 hard cost cap（靠事后解析 stream-json 杀进程）、无 pre-run restore point；批红/policy/hook 只对自研 Native Agent 生效，对外部执行器只有「隔离工作区 + clean-env + 预算杀进程 + 产出归一」四件套。**本文的借鉴主线就是补这个 gap。**

---

## 二、grok-build 借鉴清单

### 2.1 下游管控语义（P0 主线出处）

| 机制 | 设计要点 | 落地（经批判裁决） |
|---|---|---|
| SubagentCapabilityMode | spawn 子 agent 时按 ToolKind 白名单**在下发前塑形工具清单**（manifest shaping, not call interception）+ worktree 隔离 | **A1 策略编译**（P0/S，最先做）：天枢 policy → Claude Code `settings permissions`（deny>ask>allow）+ `--disallowedTools`；Codex 走 sandbox/approval 面（明示降级） |
| HTTP hook + PreToolUse 门 | 事件信封 POST 远端（仅 HTTPS + SSRF 校验 + raw/resolved 双 URL 防泄密）；按序串行第一个 deny 短路；fail-open 取舍成文 | **A2 会中拦截**（P0/M→L）：Claude Code hooks 是 command 型，注入 shim 脚本 POST 回天枢网关；首期只 deny/allow，「ask 挂起等批红」二期；Codex 无 hooks 面明示不覆盖 |
| Stop 门禁 keep-working loop | hook 输出 block 则 reason 回灌 agent 同 turn 续跑；每 turn 硬上限 8 次；信封含 `stopHookActive`（防死循环）+ `backgroundTasks`（区分真完成 vs 暂歇） | **A3 验收门**（P0/M，拆两步）：第一步零 hook——退出后核验，不合格 `claude -r` 续会话回灌整改；第二步才做 Stop hook 版。验收逻辑复用 Auditor 规则 + memorial 验收契约 |
| Bash 段级不对称匹配 | 链式命令拆段：deny/ask 逐段+全串双检，**allow 只匹配全串**（防 `git status && rm -rf /` 借 `Bash(git *)` 放行）；危险命令免记忆底线；「不可拆整体升 ask」兜底 | **E2**（P1/M）：与 A1 策略编译共用同一引擎保证口径一致；解析器从 shlex 简版起步（bashlex 已失维护） |
| 五层配置 + tighten-only | 项目级配置只允许贡献少数节；permission 跨来源合并后 deny>ask>allow 裁决（全局 deny 不可被项目 allow 顶掉）；root 级 requirements.toml 不可绕过 | 理念并入天枢 policy 分层（org/项目/运行三层，只可加严） |

### 2.2 审计与可观测

| 机制 | 设计要点 | 落地 |
|---|---|---|
| CanonicalToolMeta（x.ai/tool envelope） | 33 类语义 ToolKind 跨方言归一：**kind 开放集（未知沉降 Other 不炸）、namespace 封闭 enum、input 投影不镜像**（大载荷永不投影） | **B2**（P1/M→S）：Claude Code/Codex 工具事件归一 envelope，与 A2 共用 stream-json 解析点；是拦截/审计/监理的共同数据底座 |
| HookEventName + EventTraits | 15 个事件各自声明三元组（阻断档位 Observe/Tool/Stop、matcher 策略、是否上报中枢），穷举 match 编译期强制 | **B4 Hooks UI 数据契约**（P1/M）：per-hook HookRunResult 落起居注/edict 时间线 + 事件特性注册表（穷举断言测试替代编译期强制）；热重载语义裁掉 |
| PromptOrigin 任务谱系 | 发起者编码进 prompt_id 前缀（用户/后台完成/子 agent/cron…），`is_synthetic()` 派生持久化/展示/抢占策略；用户永远优先于自动唤醒 | **B3**（P1/S）：扩展 edict 既有 `source` 枚举 + 迁移回填（不新增字段防口径分裂）；抢占策略不做；「合成任务不入账」不抄——起居注要求全量入账 |
| headless 诚实计费 | cost 不完整时**删除全部 cost 浮点字段**防假账（宁缺毋假）；整数 tick 精确求和；退出码 130/143 区分信号 | **B1 一部分**（P1/S）：partial 语义天枢内部推断（预算杀进程→标 partial 不入对账）；整数记账用「分」级即可 |
| Secrets sanitizer | RegexSet 预筛零分配；**tripwire 测试钉死模式数==替换数**；路径匿名化 env 优先+正则兜底互斥 | 天枢 security/redact.py 已有同等物（批判审查否决新建）；只做「三出口强制过闸」收编检查 + 补 tripwire 测试 |
| External OTEL 封闭 schema | 属性 key 封闭在编译期枚举 + 导出口逐条 fail-closed 复核 + 内容闸门只收紧 + 远程禁用方向在类型上不存在 | 否决立项（对外订阅功能不存在；ADR-0003 已确立同范式）；留作将来对外输出面的设计范本 |

### 2.3 工作区、隔离与证据保全（位面机制的直接参照）

| 机制 | 设计要点 | 落地 |
|---|---|---|
| apply_worktree 三方合并 | 逐文件 base/ours/theirs 裁决：主仓没动→直接写入；双方都动→FileConflict 三份全文上报**不落地**；Overwrite 模式兜底 | **D2 客卿产出发布**（P1/M）：扩展既有 WorkspaceApplyEngine——preimage 漂移从「拒绝」升级为三方裁决（`git merge-file` 薄封装），冲突进批红审批流；前端锁死最简版（三份全文，不做交互式 merge UI）。注意：勿称「两段式发布第二段」（那是宣发术语） |
| 快照到 git ref 三部曲 | 回收前把工作树全部状态（含未提交/未跟踪）压成 commit 存 ref：scratch index 播种→add -A→commit-tree→update-ref；transfer 验证失败则不删树 | **D3 证据保全**（P2/S~M）：复用 shadow_snapshot 独立 GIT_DIR 机制扩展到评估/客卿工作区；memorial 可引用快照 commit，败者现场可复查 |
| SQLite worktree 元数据 + 节流 GC | 六 kind 分类（**Ab 配对评估是一等公民**）+ 年龄 GC + 节流戳 + 在用检测 fail-close | 台账进主 SQLite 新表、GC 挂现有 Sweeper；在用检测查 attempt lease 不抄进程 CWD 扫描（天枢自己就是 spawner） |
| fast-worktree 秒建 + 池化 | linked 元数据秒建+并行 CoW / btrfs 子卷 O(1) / 原生兜底三档；池化 + 一次采集脏状态喂多个池成员 | 位面配对评估高频起对照工作区时的降本手段（按需引入，非当期） |
| FS rewind + Git 软回滚 | 全文 before/after 快照；外部并发修改标三类冲突**上报但仍恢复**；git 永不 reset --hard（stash 保底、abort 优于破坏、FS 成功后才 restage） | 回滚下游产物时区分 agent 改动与人工改动；冲突清单进起居注 |
| OS 级沙箱 | Landlock/Seatbelt 内核强制；**项目层只能新增 profile 不能重定义全局同名**（防恶意仓库掏空 deny 列表）；进程留网、子进程断网 | 位面沙箱升级容器/内核级隔离时的 profile 分层参照 |

### 2.4 可靠性与自进化

| 机制 | 设计要点 | 落地 |
|---|---|---|
| classify_error 六变体重试分类 | 纯函数无 I/O：Retry（退避+jitter）/ RetryWithBackoff（429 尊重 Retry-After，限流重试上限单独收紧为 2）/ RetryWithImageStrip / RetryWithClientRebuild（HTTP/1.1 逃离中毒 HTTP/2 池）/ EmitToSession（凭证类上交上级）/ Fatal | **E1**（P1/M→S）：先评估 litellm Router 内建能力，只自研缺口（分类纯函数四档 + Retry-After + 信号喂太医）；完整三态熔断器单机用不上不做 |
| Doom-loop 检测 | 置信判定三条件合取（只对 thinking 通道动手、threshold≤8）；**恢复预算独立于重试预算**；耗尽接受现状不死磕 | 降 P3：MVP 只做滑窗检测工具调用签名重复 + warn 进起居注 + 信号喂太医；自动动作等误报数据 |
| Dream 周期沉淀 | 空闲触发 + 锁门控 + ONE LLM 整理 + **输入守恒**（只标记真正被读入的素材） | **D1 MemorialCurator**（P1/M→S）：不新建组件，给既有 Curator 家族加成员，复用 SkillCurator idle+lock 骨架 + schedule_run；输入守恒纪律照抄 |
| Goal mode 对抗复核 | 完成声明必须过独立证据复核，无法复现则保持 active 或**带具体缺口暂停** | 与廷议 stance/太医同源，验收门（A3）的复核纪律参照 |
| Rhai workflow journal 重放 | 确定性脚本 + host-call 按序记 JSONL + req_hash 校验重放 = 崩溃后精确续跑不重复烧 token | 长编排任务恢复语义参照（与 pi durable 模型互补） |
| Full-replace compaction | 失败三分类驱动降级阶梯；**AGENTS.md 与最后用户查询原文回注、不经摘要器复述** | **F1 原文回注白名单**（升 P1/S）：敕令原文/验收契约/policy 约束永不被 Compaction 改写——治理指令失真是控制平面独有风险 |

### 2.5 运维交互

- **Agent Dashboard**：Needs input 置顶排序、挂起权限数字键直接批复、**dispatch 框永远只新建绝不作回复目标**（防误发错 agent）→ F3（P2）：数据面从 DecisionService 派生视图（不建新表），推送面作为 Multica #5 WS 房间第一个业务房间。
- **后台任务四原语**（background/monitor/scheduler/loop）+ 统一唤醒模型：与 schedule_run+Sweeper 同构，「Stop 门禁可见后台账」的组合可参照。
- **MCP meta-tool 两段式发现**（search_tool + use_tool，防工具定义撑爆上下文）+ 字节截断落盘：将来 MCP 解禁时的工具面治理手法。

---

## 三、pi 借鉴清单

### 3.1 驱动下游 agent 的协议范本（RPC 模式）

| 机制 | 设计要点 | 落地 |
|---|---|---|
| 严格 JSONL 分帧 | **唯一分帧符 LF**（Node readline 会误切 U+2028/2029；Python readline 天然合规）；容错剥 CR | 自研 wire 协议时直接写进 spec |
| 双流契约 | 命令带 `id` → response 回带同 id + 回显命令名；事件不带 id 单向广播 | adapter 协议基础 |
| **三层结算事件** | `agent_start` / `agent_end`（带 `willRetry`）/ **`agent_settled`**（彻底结束） | 编排器「何时可推进/回收」的锚点；防把自动重试误判为失败 |
| 增量拉取 | `get_entries{since}` 持久游标 + `leafId` 一次判分支移动 | 审计留痕、断线重连 |
| 扩展 UI 反向通道 | 同一 stdio 上叠加请求-响应子协议：UUID 关联 + select/confirm/input + **timeout 自动默认值自解不卡死** | 「下游挂起等上级裁决」通道 = A2 二期「ask 挂起等批红」的协议参照 |
| 权限审批软肋（反面教材） | 审批寄生在可选守卫扩展 + 超时默认**放行** | **天枢审批强制点必须在控制平面、默认拒绝/挂起**——这恰是天枢相对下游 agent 的差异化 |
| 关闭语义 | 无 close 命令，关 stdin 即优雅 shutdown | 进程生命周期即会话生命周期 |

### 3.2 控制平面架构（packages/server = 小号天枢）

| 机制 | 设计要点 | 落地 |
|---|---|---|
| Supervisor 进程模型 | spawn 托管多个 `pi --mode rpc` 子进程；状态机 starting→online→stopping→stopped/error；意外退出兜底；get_state 回填 sessionId | KeqingExecutor 生命周期管理的对标补齐 |
| IPC 双模式 | 一次性 `rpc` 命令 vs **`rpc_stream` 把 socket 升级为双向流**（事件+审批反向通道全回流） | 天枢「下发 edict = rpc、订阅事件 = rpc_stream」的 API 面参照 |
| 重启对账 | instances.json 台账；重启后把所有 online 记录**降级为 stopped** | 控制平面重启后「孤儿 agent 对账」的现成答案 |
| 运行时投影分层 | TUI/RPC/print 共享同一 AgentSessionRuntime，**RPC 只是 runtime 的 JSON 投影** | 「核心一份、对外多传输投影」= 客卿 adapter 层的分层原则 |
| radius 云注册 | 机器/实例两级注册 + 心跳退避 + 能力协商 + 断线重注册 | 跨机器 agent 舰队的前瞻参考（单机当期不做） |

### 3.3 存储与恢复

| 机制 | 设计要点 | 落地 |
|---|---|---|
| SQLite 会话库 | sessions + entries 树（entry_seq 事务取号）+ **物化活动分支** + **物化统计视图**（写入同事务增量维护 token/成本，失败整体回滚）+ 迁移框架 + WAL/FULL | **C1**：可近 1:1 翻成天枢存储；起居注列表页性能解法 |
| entry 树「状态即事件流」 | 配置变更（模型/thinking/工具集）也作 entry 落同一日志，派生状态回放得出；leaf 指针是持久化 entry | **C2**：廷议分支/memorial 对照/位面 A/B 的一等结构 |
| `custom` vs `custom_message` 二分 | 扩展数据分「存档不进 LLM 上下文」与「进上下文」两类 | 治理元数据（stance/言官批注）用前者：审计留痕不污染上下文 |
| **Durable 恢复模型** | write-ahead：变更在 API resolve 前先落 durable entry；确定性 target id 判重；保守恢复——未完成 turn 标 interrupted 不自动重试、**工具仅声明幂等才重跑、缺终态才补做** | **C3**：「下发给客卿的任务是非幂等副作用，绝不盲目重发」——schedule_run/Sweeper 崩溃语义升级蓝图 |
| Compaction 自包含 checkpoint | retainedTail 物化保留；切点绝不落 tool_result；被放弃分支自动摘要留档 | 断点续跑 + 「为什么放弃」决策痕迹 |
| fork 血缘 | parent_session_id 会话谱系 | 任务溯源审计模型 |

### 3.4 扩展与治理挂点（设计思想）

- **三层拦截深度**：`tool_call` 改参/block（浅）→ `operations` 换 I/O 执行后端（中，sandbox/gondolin 隔离全靠它）→ 同名 registerTool 整表替换（深）。天枢 hooks 补「换执行后端」档是把 agent 塞进受管执行环境的关键。
- **治理挂点四层**：准入（tool_call block+reason）/ 上下文（context 深拷贝改写、system prompt 链式）/ 传输（before_provider_headers/request——网关级）/ 取消（session_before_* cancel）。pi 交给本地扩展，**天枢上收为中心策略引擎**——这就是护城河。
- **防提权信任门**：项目级扩展在信任决策前根本不加载、不参与决策。
- **hooks 契约升级**：`observe()`（只读旁观）与 `on()`（参与语义）分离；每类事件专属 reducer（transform 链 / block 早退 / patch 累积）；`createScope` 溯源（哪个视角/客卿产生的意见）。
- **Observability 契约**：核心只发稳定 span 事件，适配器外译；默认脱敏 opt-in；「hooks 可影响执行 vs 订阅者必须被动」硬区分——起居注/Hooks UI 底座。
- **ctx 能力分层**：事件上下文只读、命令上下文才能改会话拓扑——hook 点权限分级范式。

### 3.5 Provider 与凭证

- 透明网关：`registerProvider("anthropic", {baseUrl, headers})` 只换端点保留全部模型、零 streaming 代码。
- **凭证隔离三件套**：运行时内存覆盖不落盘（setRuntimeApiKey）；可注入自定义 CredentialStore（托管 Vault/KMS）；**check/resolve 探活与取值分离**（不接触明文判通道可用）。
- **OpenShell 凭证网关模式**：沙箱内只能访问 `inference.local`，网关上游注入 key——**raw key 永不进沙箱** = 位面/客卿凭证隔离的理想形态。
- 能力目录五元组：reasoning/input/thinkingLevelMap/cost（四类计费）/contextWindow。
- 重试分类正则两张表：可重试抖动 vs **额度/账单耗尽快速失败不烧钱**。

### 3.6 治理工作流样板与安全纪律

- 渐进披露 skill（描述常驻、正文按需 read）+ `disable-model-invocation`（治理权限原语）。
- `.pi/prompts` 五工作流（is/pr/sa/cl/wr）：label 生命周期、CI-aware、AI 免责标记、「不信任来源独立验证」纪律——受治理工作流命令化的样板；**AGENTS.md 定约定 + slash-command 落约定**闭环；含并发 agent git 隔离铁律。
- 子 agent 委派 wire 协议：spawn `--mode json --no-session` + 事件流回收 usage + 50KB 截断 + **项目级 agent 默认不信任须二次确认**。
- **隔离让 agent 自知**：改 system prompt 告知「你在隔离环境、cwd 是 X」，防按宿主路径乱来。
- 凭证黑名单 baseline：denyRead `~/.ssh ~/.aws ~/.gnupg` + denyWrite `.env *.pem *.key` + 网络域名白名单。
- **供应链硬化清单（F2，开源前照搬到 uv.lock 体系）**：精确钉版 + 发布冷却期（min-release-age）+ 生命周期脚本 allowlist + pre-commit 拦 lockfile + 发布附锁 + 一律 ignore-scripts + 定时 audit + 隔离冒烟 + SHA256SUMS。
- 安全纪律：`stopReason=length` 截断的 tool call 一律当错误绝不执行；结构操作需 idle 且 await 前同步置相位防重入。

---

## 四、否决项（防重复提案）

| 否决建议 | 理由 |
|---|---|
| 进化产物行为版本目录 | 与 CandidateService/PromotionService 重叠过半；只在既有状态机加晋升后状态 + replacement 指针 |
| 起居注对外订阅封闭 schema | 功能不存在未立项；ADR-0003 已确立同范式 |
| 新建统一脱敏器 | security/redact.py 已有且已接线；只做三出口过闸收编 + tripwire 测试 |
| 供应链 staging 事务化安装 | MCP/插件面未解禁；只做分级信任并入 Guard TrustLevel + 技能通道 SHA pin |
| doom-loop 监理（P2→P3） | warn 无人看收益趋零；等 B2 envelope 落地 + 误报数据 |
| ACP 协议层 / relay 拓扑 | 单机同宿主无场景；只留「双向 stream-json 运行中插话」并入 B1，长线写进 Multica #3 叙事 |

---

## 五、客卿抽象层设计：自进化外包给 coding agent，且不绑定实现

### 5.1 目标与定位

把天枢自进化（位面开发、技能生成、代码候选）的 **coding 能力外包给外部 coding agent（客卿）**：天枢提需求（edict）+ 治理（policy/审计/隔离）+ 验收（Auditor/配对评估/廷议），客卿写代码。天枢保持轻量——每一行核心代码花在治理上，不维护 coding 能力。

**不绑定原则**：Claude Code 是首选客卿（用户重度使用、headless+hooks 最成熟），但接口从第一天抽象，Codex/pi/未来 agent 可插拔。这不是过度设计——位面竞争力评估天然演化出「同一需求多个客卿各写一版、配对评估谁赢」，多客卿是该机制的燃料。

### 5.2 可行性的关键：治理分两层，底线不依赖下游

「不绑定某个 agent」之所以可行，在于把治理拆成两层：

- **硬保证层（构造期，不依赖下游任何配合）**：worktree 隔离工作区 + clean-env + 凭证网关（OpenShell 模式：客卿只见网关端点，raw key 永不进沙箱）+ 预算熔断 + 事后验收（可执行验收物）+ 产出三方合并（不直写主仓）。任何 agent——哪怕能力面为零——都受这层管辖。
- **软增强层（运行期，按下游能力面协商启用）**：策略编译（permissions 白名单）、会中工具拦截（hooks shim）、Stop 验收门、会话续用回灌、运行中插话。有则用、无则明示降级，**降级不破坏硬保证**。

grok-build 的 SubagentCapabilityMode（清单塑形优于逐调用拦截）与 pi 的「supervisor 不懂 agent 内部、只管进程与事件流」共同印证：控制平面的强制力应来自构造期与边界，运行期管控是增强不是底线。

### 5.3 分层架构

```
┌──────────────────────────────────────────────────────┐
│ 天枢治理核心（协议无关）                                 │
│  edict(需求规格+验收物) · policy · 批红/廷议 · Auditor   │
│  起居注 · memorial · 户部计费 · 配对沙箱评估              │
├──────────────────────────────────────────────────────┤
│ KeqingSupervisor（进程托管，参照 pi packages/server）    │
│  spawn/状态机/意外退出兜底/重启对账/预算熔断               │
├──────────────────────────────────────────────────────┤
│ CodingAgentAdapter（per-agent 方言层）                  │
│  ClaudeCodeAdapter │ CodexAdapter │ PiAdapter │ …      │
│  策略编译 · 事件归一 · 会话语义 · usage 提取              │
├──────────────────────────────────────────────────────┤
│ 硬保证层（对所有 adapter 无条件生效）                     │
│  worktree 隔离 · clean-env · 凭证网关 · 三方合并发布      │
└──────────────────────────────────────────────────────┘
```

参照 pi 的「核心 runtime 一份、对外多传输投影」反向应用：**治理语义一份，per-agent 方言多份投影**。

### 5.4 CodingAgentAdapter 接口（六方法 + 能力声明）

```python
class AgentCapabilities(BaseModel):
    """能力协商：adapter 静态声明，编排器据此启用软增强并明示降级。"""
    permission_shaping: Literal["full", "partial", "none"]
    #   full=Claude Code(settings permissions + --disallowedTools)
    #   partial=Codex(sandbox/approval)  none=未知 agent
    hooks: Literal["command_shim", "http", "none"]   # A2 会中拦截可用性
    stop_gate: bool          # A3 第二步 Stop 验收门可用性
    session_resume: bool     # 验收不合格续会话回灌（claude -r）
    interject: bool          # 运行中插话（双向 stream-json）
    usage_reporting: Literal["full", "partial", "none"]

class CodingAgentAdapter(Protocol):
    capabilities: AgentCapabilities

    async def spawn(self, task: TaskSpec, workspace: Workspace,
                    compiled_policy: CompiledPolicy, budget: Budget) -> AgentRun: ...
    def events(self, run: AgentRun) -> AsyncIterator[CanonicalAgentEvent]: ...
    async def interject(self, run: AgentRun, message: str) -> bool: ...   # 能力可选
    async def abort(self, run: AgentRun, reason: str) -> None: ...
    async def resume(self, session_ref: str, feedback: str,
                     workspace: Workspace) -> AgentRun: ...               # 验收回灌
    async def collect(self, run: AgentRun) -> RunResult: ...
    #   RunResult: diff/产出清单 + usage(partial 标记，天枢侧推断) + exit 分类
    #   （信号退出 vs 业务失败并入既有 failure_reason 分类学）
```

设计出处：五操作面来自 pi RPC 命令面收敛；capabilities 协商来自 grok ToolCapabilities 声明 + MCP 能力协商；resume 语义来自 Claude Code `-r`（pi `switch_session` 同构）。

### 5.5 统一事件 envelope（CanonicalAgentEvent）

所有 adapter 的事件流归一为同一 envelope，是拦截、审计、监理、计费四个消费者的共同底座（B2）：

- **三层结算**（借 pi）：`run_start` / `run_end{will_retry}` / `run_settled`——编排器只认 settled 推进。
- **工具事件**（借 grok CanonicalToolMeta）：`kind` 开放枚举（Read/Edit/Execute/Task/AskUser/…，未知沉降 Unknown 不炸）+ 封闭 `dialect`（claude_code/codex/pi）+ **投影式 input**（只投跨方言稳定字段，大载荷从 raw 读）+ `is_error`。
- **治理事件**：`permission_request`（hook shim 上报）、`budget_tick`、`interjection_ack`。
- 版本契约：加字段/加值不 bump，删除或改语义才 bump；消费者遇 Unknown 静默忽略。

### 5.6 策略编译器（PolicyCompiler）

天枢 policy（单一真相，与 Native 审批共用同一求值引擎，含 E2 bash 段级语义）按方言编译：

| 目标 | 编译产物 | 覆盖度 |
|---|---|---|
| Claude Code | `settings.json` permissions（deny>ask>allow）+ `--disallowedTools` + `--max-turns` + hooks 注入（PreToolUse shim / Stop gate） | full |
| Codex | sandbox / approval 配置面 | partial（spec 明示降级语义） |
| 未知 agent | 无运行时产物 | none——仅硬保证层 + 验收收紧（如强制人工批红产出） |

原则（借 grok tighten-only）：编译只可能比天枢 policy 更严不可能更宽；编译结果与 policy 版本一并进起居注（可审计「当时下发了什么约束」）。

### 5.7 自进化闭环（需求→开发→验收→发布）

```
memorial/京察/太医信号 ──► edict 生成（需求规格 + 可执行验收物：测试/脚本/检查清单）
        │
        ▼
KeqingSupervisor.spawn(adapter 按 edict 指定或多客卿竞争)
        │  硬保证：worktree + clean-env + 凭证网关 + 预算
        │  软增强：compiled_policy + hooks shim（能力允许时）
        ▼
事件流 ──► CanonicalAgentEvent ──► 起居注（全量入账）+ 会中拦截 + 户部计费
        ▼
run_settled ──► 验收门（A3）：跑验收物 + Auditor 规则 + memorial 验收契约
        │   不合格 ──► capabilities.session_resume ? resume 回灌整改（上限 N 次）
        │                                        : 重跑或挂 memorial
        ▼
合格 ──► WorkspaceApplyEngine 三方合并（D2）：冲突进批红
        ▼
配对沙箱评估（位面竞争力，Ab 工作区建档 + 败者快照留证 D3）──► 廷议终审 ──► 晋升
        ▼
失败任何环节 ──► 挂 memorial（不挂 edict，沿用既有语义）──► MemorialCurator 周期沉淀（D1）
```

多客卿竞争模式：同一 edict 发给 N 个 adapter，各自在独立 worktree 产出，配对评估裁决——「自进化第二幕」的具体形态：天枢从「自己改自己」升级为「指挥客卿改位面、用评估机制选优」。

### 5.8 落地依赖链（每步独立交付价值）

```
① A1 策略编译（P0/S）        ← ClaudeCodeAdapter 首个方言，PolicyCompiler 骨架
② B2 事件归一 envelope（S）+ B1 调用契约（S）
                              ← adapter events()/collect() 落地；起居注/计费受益
③ A3-1 退出后验收 + resume 回灌（S，零 hook 成本）
                              ← 自进化闭环最小可用（需求→开发→验收→整改）
④ A2 hooks shim 会中拦截（M→L，仅 hook-capable agent）
⑤ A3-2 Stop 验收门 + D2 三方合并发布（M）
⑥ 多客卿：CodexAdapter（partial 降级验证抽象层成立）→ 多客卿竞争评估
```

配套横切项按需并行：F1 原文回注白名单（S）、E1 重试分类（S）、E2 bash 段级语义（M，与①共引擎）、B3 origin 谱系（S）、B4 Hooks UI 契约（M）、C1/C2/C3 存储与恢复（伴随起居注增强）、D1 MemorialCurator（S~M）、D3 证据保全（S~M）、F2 供应链 checklist（开源前）、F3 Needs-input 置顶（随 Multica #5）。

### 5.9 候选客卿评估矩阵（2026-07-23，均有本地参考仓 ~/ai-example/）

准入判据三问：headless 可驱动？结构化事件流？能力面几档？硬保证层对所有候选无条件成立，新客卿接入 = 写 adapter + 声明 capabilities，治理核心零改动。

| 候选 | 驱动接口 | 能力面 | 判定 |
|---|---|---|---|
| pi | `--mode rpc`（协议最干净：agent_settled/fork/审批反向通道） | 无内置权限，但**扩展拦截全场最深**——天枢发 `tianshu-guard` pi 扩展作治理 shim（tool_call block / before_provider_headers / operations 换后端），实际可达 full | **默认客卿（2026-07-23 拍板：开源 + 多 provider 不锁定）**，详见 [docs/design/keqing/pi-default-adapter.md](../design/keqing/pi-default-adapter.md) |
| Claude Code | headless stream-json + hooks + settings permissions | full | 第一备选 + 对照基线（用户订阅成本优势场景、多客卿评估对照组、pi 故障降级） |
| opencode | `run` headless + serve HTTP + 官方 SDK | permission 配置 ✓、plugin `tool.execute.before` 可阻断 ✓、session 续用 ✓、usage ✓ | 第三方言验证；grok-build 移植过其工具实现，语义成熟 |
| Codex | exec 模式 | partial（无 hooks 面） | 已支持，保留作降级验证 |
| hermes-agent（Nous） | `cli.py`/`batch_runner.py` + **acp_adapter/**（ACP 协议） | toolsets 白名单 ✓；**六种执行后端**（local/Docker/SSH/Singularity/Modal/Daytona）——硬保证层可直接复用其隔离后端 | Python 同栈候选；**客卿模式必须关其自主学习/记忆**（它自带学习闭环，两套自进化互相干扰，治理与沉淀归天枢）；先摸 ACP adapter 成熟度 |
| openclaw | gateway API/CLI | agent 内核即 pi runtime（pi-agent-core/pi-ai） | **不作客卿**：自身是控制平面形态，与天枢角色重叠大于互补；PiAdapter 已覆盖其 agent 语义。价值在渠道/网关设计参考 |

落地顺序：Claude Code → pi → opencode → Codex（已有）→ hermes → openclaw（不做）。多客卿竞争评估的价值随 adapter 数量上升；六个候选正好覆盖能力谱系各档（full / shim 增强 / partial / none），是对抽象层的天然压力测试。

---

## 附：分析产物索引

- grok-build workflow：12 agents（7 域探索 + 天枢测绘 + 综合 20 条 + 3 视角裁决），约 153 万 subagent tokens
- pi：8 份深读报告（RPC 协议 / 会话存储与供应链 / SDK 与 provider / skills 与自扩展 / extensions 事件全目录 / 扩展示例隔离天花板 / 运行时 HIGH 档 / MEDIUM-LOW 档与 server）
- 项目记忆：`project_grok_pi_borrow_2026_07_23.md`（结论 + P0 依赖链 + 否决项）
