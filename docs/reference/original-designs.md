# 天枢原创设计专篇

> 本文是 [reference-projects.md](./reference-projects.md)（借鉴篇）的**镜像与对立面**。
> 借鉴篇记录「天枢从哪些项目搬来了什么」；本篇记录「天枢自己造了什么、相对它借鉴的项目新在哪」。
> 两篇互为参照：凡本篇标注「借来的子件」，其源头都能在借鉴篇找到对应行。
>
> 本文讨论设计来源，不代表所有机制都属于当前稳定支持面。尤其 Universe、代码变体和
> external executor 仍有实验/延期边界；当前成熟度以
> [CURRENT-STATE](../CURRENT-STATE.md)和
> [能力事实矩阵](../launch/capability-matrix.md)为准。

---

## 开篇：原创不在原语，而在组织

诚实定调：天枢的原创**不在于发明新原语**。ReAct 循环、3 层 compaction、fuzzy match、fitness function、SSRF 防护、滑动窗口限流——这些底层机制大多站在巨人肩上，逐条出处见 [reference-projects.md](./reference-projects.md)。

天枢真正原创的，是**把治理、长程自治、自进化这三件事组织成一个连贯系统**：

- **治理**——把单 Agent 人格扩展为受统一权限矩阵约束的「朝廷组织」，把任务建模为「诏令→题本→批红」的奏章闭环。
- **长程自治**——把「长任务该怎么评」做成一套验收契约驱动的外层自检升级循环。
- **自进化**——原始方案把 agent 的自我改进扩展为行为层 + 代码层的分支与适应度选优；当前真正影响运行的晋升另走受治理 Candidate，生产可执行路径限于 Skill。

下面按诚实分级展开：**真原创机制**（无直接借鉴源的端到端设计）、**原创封装与组合**（骨架或子件借来、组合方式原创）、**与借鉴的边界**（主体借来、仅做扩展，不算我们的原创）。

---

## 一、真原创机制

这些机制在参考项目中**找不到对应物**，是天枢端到端设计的产物。每条标注它解决什么、机制（含真实类名）、相对参考项目新在哪、代码落点、设计文档。

### 1.1 六部官制组织 —— 受统一权限矩阵约束的「朝廷」

**它解决什么**：参考项目要么是单 Agent 人格，要么是松散的「多 agent 团队」。当 agent 数量增长，「谁能干什么、谁能委派给谁、谁的工具上限多高」没有统一治理面。

**机制**：`AgentPersona` 模型（`id` / `department` / `tools_allowed` / `tools_denied` / `tool_tier_max` / `can_delegate` / `delegates_to` / `llm_config_name`）把每个官员的权限上限固化为数据。`OfficialSelector` 提供两条确定性路由：`TASK_DEPARTMENT_PREFERENCE`（按 `task_type` 映射部门）+ `DEPARTMENT_KEYWORDS`（关键字打分兜底）。六部各司其职：neige 规划 / bingbu 执行 / ducha 审计 / tongzheng 通知 / wenyuan 记忆 / hubu 成本。`PromptBuilder` 8 层注入把这套组织约束铺进系统提示。

**相对参考项目新在哪**：参考项目只有「人格卡」或「子代理」概念；天枢把多 Agent 升级为**受统一治理矩阵约束的组织**——每个官员有固定权限上限、部门职责、委派边界，并支持智能路由。

**借来的子件**：`SOUL.md` / `ROLE.md` 身份卡文本结构与 `can_delegate` 字段借鉴 OpenClaw（见借鉴篇附录）。但运行时模型（`AgentPersona` 的权限分层）、`task_type→department` 映射、关键字打分路由是天枢独创。

**代码落点**：`src/tianshu/persona/selector.py`、`src/tianshu/persona/model.py`、`src/tianshu/persona/prompt_builder.py`、`src/tianshu/resources/overlay.py`、`src/tianshu/resources/personas/neige/SOUL.md`。部门由 `AgentPersona.department` 字段与 selector 规则表达，不设独立模块。

**设计文档**：[../design/persona/officials.md](../design/persona/officials.md)、[../design/persona/prompt-builder.md](../design/persona/prompt-builder.md)

### 1.2 诏令→题本→批红 领域模型 —— 奏章式治理闭环

**它解决什么**：参考项目有任务模型和执行记录，但没有把「哪个官员执行 / 执行是否通过 / 人工如何干预」三个维度分层建模，使得诏令级别的路由和配置无处落脚。

**机制**：三元组数据结构 + EventBus 状态机。`Edict`（用户下达的任务，含 `goal` / `assigned_persona_id` / `acceptance`）、`Memorial`（一次执行记录，含 `status` / `result` / `attempt` / `universe_id` / `feedback_score`）、`Decree`（人工批红，含 `action`=approve/reject/retry/amend/cancel 与 `grant_scope`）。状态机：`edict.submitted → edict.scheduled → plan.completed → execution.started/completed → audit.completed`。`Memorial.runtime_override` / `acceptance_override` 支持 follow-up 单轮覆盖而不回写 `Edict`。

**相对参考项目新在哪**：把奏章治理模式建模为一组数据结构和状态机，使三个治理维度都能从诏令级别路由和配置；`runtime_override` / `acceptance_override` 的单轮粒度控制是新增。

**借来的子件**：EventBus 事件驱动模式借鉴 Claude Code；记忆结构（`MEMORY.md` + daily log）借鉴 NanoBot。但奏章三元组及其状态转移契约是原创。

**代码落点**：`models/edict.py`、`models/memorial.py`、`models/decree.py`、`executor/agent.py`

**设计文档**：[../design/domain-model.md](../design/domain-model.md)、[../design/runtime-flow.md](../design/runtime-flow.md)

### 1.3 长任务 Outer Loop —— 验收标准驱动的自检与升级

**它解决什么**：参考项目有 compaction 和多轮思考，但没有「标准化的验收契约驱动外层循环」——「长任务该怎么评、评不过怎么升级」没有可配可复用的结构。

**机制**：`AcceptanceCriteria` 契约（`checks` / `critic` / `escalation` / `min_outer_iterations` / `max_outer_iterations` / `deadline_seconds` / `on_exhaustion`）+ `OuterLoopState`（frozen dataclass）FSM（L0–L3 四层升级 + 同类问题 streak 计数）。每轮：(1) actor 执行 → (2) `ChecksRunner`（bash/lint/rubric 并发客观检查）→ (3) `CriticReview`（多监督官并发评审，strictness 三档，synthesize 观点）→ (4) `decide_escalation`（L1 继续 / L2 会诊 / L3 人工）。终态由 `CompletionAudit` 覆盖审做双门禁，并支持软着陆 `winding_down`。

**相对参考项目新在哪**：把「acceptance criteria 前置 → 客观检查 → 多监督官评审 → 分层升级 FSM → 完成覆盖审」串成完整外环。DeepAgents 有 planning step 但无升级 FSM；Claude Code 有 agent loop 但无验收契约驱动的多官员评审升级。

**借来的子件**：客观指标检查的设计借鉴参考项目的 test harness；critic「LLM 独立评审」是业界通用做法。整个串联编排无外部参照。

**代码落点**：`executor/orchestrator/loop.py`、`executor/orchestrator/checks.py`、`executor/orchestrator/critic.py`、`executor/orchestrator/escalation.py`、`executor/orchestrator/state.py`、`executor/orchestrator/audit.py`、`models/acceptance.py`

**设计文档**：[../design/agent/orchestrator.md](../design/agent/orchestrator.md)、[../design/runtime-flow.md](../design/runtime-flow.md)

### 1.4 平行位面演化 —— 行为配置层的历史「分支赛跑→适应度选优→晋升」方案

**它解决什么**：参考项目都有 agent/skill 进化，但只在**单线时间线**上——历史快照丢失、无法多版本并行赛跑、无法择优晋升。

**历史设计机制**：`Universe` 模型（`status`=champion/challenger/archived，`origin`=genesis/manual_branch/mutation/code_variant，`parent_universe_id`，`fitness`）+ `UniverseStore` 全量快照 + `UniverseEvolver` 变异/评估，原本计划由 `UniverseManager` 完成 branch/switch/rollback/diff/archive/restore，并按 memorial identity 确定性分流。

**当前边界**：Legacy Universe 仍支持 genesis/branch/diff/archive/restore/delete 与评估推荐，但 `UniverseManager.switch/rollback/promote_code_variant` 已固定 fail-closed。当前可审计灰度改由 `PromotionService`、`ChallengerRouter`、不可变 `RunAssignmentV1` 与 effective overlay 实现；非 demo profile 的 Candidate canary 使用 HMAC bucket 固化选择，不会由 Legacy manager 自动晋升或切换 live。

**相对参考项目新在哪（历史设计语境）**：把自进化从「单线」升级为「可分叉 + 可回滚 + 可选优」的平行版本控制——一套「行为版 git」。同时在役一个冠军、候选冻结快照和确定性探索分流，是原始 Universe 方案，而不是当前 live routing 契约。

**借来的子件**：fitness / 变异→评估→选优骨架与 GA/进化算法通用思路相通；SkillCurator 的「修撰」骨架（idle/lock/gate/LLM 变异/分支/熔断）启发了 evolver 主干。但位面级分叉、确定性哈希分桶、全量快照、五维权重配置是原创实现。

**当前代码落点**：`src/tianshu/universe/manager.py`、`src/tianshu/universe/router.py`、`src/tianshu/universe/store.py`、`src/tianshu/universe/evolver.py`、`src/tianshu/evolution/promotion.py`、`src/tianshu/models/run_assignment.py`

**设计文档**：[../design/universe/evolution.md](../design/universe/evolution.md)、[../design/universe/README.md](../design/universe/README.md)

### 1.5 代码变体位面 —— 代码层自进化的历史完整方案

**它解决什么**：AI 系统能安全地**改自己的代码**的平台不多见。Python agent 难以进程内热替换代码；行为层位面只能优化人格/策略，碰不到实现代码。

**历史完整方案**：`CodeVariantStore`（git worktree + branch `universe/{id}` 隔离）、`CodeMutator`、三关 `Gate`、`SandboxRunner` 与 `EvalHarness` 之后，原设计还设想用 `current/previous` 指针、自重启和健康探针完成 live 部署回滚。

**当前边界**：worktree、变异、Gate、沙箱配对评估和 `recommended` 结论仍存在；部署指针、自重启 launcher 和自动健康回滚已移除。旧 Universe switch/promote-code 固定 409，Code Candidate 的生产 promotion adapter 也不可用，因此当前没有 code live activation。未来若重新开放，还必须经 `PromotionService` 并绑定精确、已批准的高风险 Decision。

**相对参考项目新在哪（历史设计语境）**：把代码变体纳入平行位面体系，使系统既能评估人格/策略，也能评估实现代码；指针 + previous 槽的自恢复是当时设计的一部分，但未保留为当前能力。

**借来的子件**：git worktree 是 git 标准功能（非创新）；蓝绿/金丝雀部署 pattern 业界常见。当前保留的是 worktree + Gate + 受治理评估，历史自重启回滚方案不应作为现有原创能力宣称。

**当前代码落点**：`src/tianshu/universe/code_store.py`、`src/tianshu/universe/code_mutator.py`、`src/tianshu/universe/gate.py`、`src/tianshu/universe/sandbox.py`、`src/tianshu/universe/eval_harness.py`。历史部署器与 launcher 模块已删除。

**设计文档**：[../design/universe/code-variant.md](../design/universe/code-variant.md)

---

## 二、原创封装与组合

这些机制的**骨架或子件是借来的**，但组合方式是天枢的新意。诚实标注借来的子件。

### 2.1 朝廷 court 共享上下文 —— 跨官员的统一记忆层

**组合的新意**：把 shared state 建模为一个**虚拟 persona wing**。court 非独立 persona，而是 `COURT.md`（朝廷协议）+ `memory/court/MEMORY.md`（朝堂共享长期记忆）。`MemoryManager.read_pre_execution` 把 court 自动纳入召回候选（`visible_ids = [persona_id, 'court']`）；`MarkdownMemoryBackend.ensure_dirs` 硬编码总是包含 `'court'`；`PromptBuilder` Layer 6 注入 court 记忆；`ConsultationSession` 与 `Reflector` 的跨官员洞见写回 court。使所有官员在 prompt 注入时都看到统一的朝廷协议和共同决策历史。

**借来的子件**：Markdown 记忆后端 + 双层结构（长期 + 日志）借鉴 NanoBot；PromptBuilder 多层注入借鉴 NanoBot 与 Claude Code。但 court 作为虚拟 persona wing 的统一共享约束、在记忆与 prompt 中的特殊地位是原创。

**代码落点**：`src/tianshu/memory/markdown_backend.py`、`src/tianshu/memory/manager.py`、`src/tianshu/persona/prompt_builder.py`、`src/tianshu/consultation/session.py`、`src/tianshu/resources/overlay.py`、`src/tianshu/resources/personas/court/`（只读打包默认；运行时覆盖在 `~/.tianshu/personas/court/`）　**文档**：[../design/persona/officials.md](../design/persona/officials.md)

### 2.2 咨询会诊 session —— 多官员并行意见汇聚

**组合的新意**：「多人对一事的并行决策」（相对「多事的并行执行」DAG）。`ConsultationSession.start` 用 `asyncio.gather` 并发调用 N 个 persona 的 LLM 生成 `PersonaOpinion`（`confidence` / `key_points`）→ `Synthesizer` 单次 LLM 汇聚成 `synthesis` + `decision`。集成：长任务外环 L2 升级时触发，会诊失败自动降级 L3，synthesis 注入下轮 actor override prompt，结果落 court 共享记忆供审计溯源。会诊在「L1 重试 → L2 会诊 → L3 人工」升级链中承担「智囊中枢」、延迟人工介入的作用。

**借来的子件**：`asyncio.gather` 是标准库；DeepAgents 的 multi-agent summarization 有多代理信息融合，但属模型内部分析层。「征多专家意见 → LLM 汇聚 → 注入后续迭代」的会诊链条与 persona 身份具体化是原创。

**代码落点**：`consultation/session.py`、`consultation/synthesizer.py`、`consultation/models.py`　**文档**：[../design/consultation/README.md](../design/consultation/README.md)

### 2.3 审计两层门禁 —— 规则快筛 + 可选 LLM 深审

**组合的新意**：事件订阅被动触发的执行后自动质检门。`RulesEngine` 同步快规则（token 预算/execution error/空结果）→ verdict=pass|flag；仅 flag 时 `LLMReviewer`（温度 0.1、短输出、单次重试）触发第二层，返回 pass|flag|block。`Auditor` 订阅 `execution.completed`，按 `review_policy`（never / on_failure / on_flag / always）四档决策是否真审，三态裁决驱动 memorial 状态转移（AUDITING → NEEDS_REVIEW / COMPLETED / FAILED），`Decree` 可覆盖人工决策。与外环的 completion audit 串行互补（外环驱动迭代，auditor 决定交付）。

**借来的子件**：规则扫描概念来自 Hermes Guard（13 类威胁 + 50+ regex，见借鉴篇）；rules + LLM 二层是业界通用思路。但「events 驱动 + 分档审批 + 与 completion audit 区分职能」的组合是原创。

**代码落点**：`auditor/auditor.py`、`auditor/rules.py`、`auditor/reviewer.py`、`models/common.py`　**文档**：[../design/auditor/README.md](../design/auditor/README.md)

### 2.4 双层泳道 Harness —— per-edict 隔离 + 全系统背压 + 级联取消

**组合的新意**：`LaneManager` 提供 `SessionLane`（per-edict 串行隔离）+ `GlobalLane`（系统背压）双层 `asyncio.Semaphore`；`WorkerPool` async submit + 活跃任务追踪；`cancel.py` 级联取消（root → DAG 子树）；`retry.py` 有界重试 + backoff。四层协同（global 背压 → session 隔离 → worker 并发 → cancel/retry），用于长任务多 edict 并发时防止单任务拖垮整体、支持暂停/恢复的优雅中断，并与 checkpoint 协同保存泳道状态。

**借来的子件**：`WorkerPool` 核心（Semaphore）是标准库；queue 模式参考 PicoClaw（借鉴篇标注「启发但未直接落地」）。双层泳道隔离、级联取消、与 checkpoint 协同是扩展。

**代码落点**：`executor/lanes.py`、`executor/worker_pool.py`、`executor/cancel.py`、`executor/retry.py`　**文档**：[../design/agent/orchestrator.md](../design/agent/orchestrator.md)

### 2.5 五维适应度 + 历史 DeployPointer 设想

**组合的新意（历史设计语境）**：`compute_fitness(stats, weights=(0.4,0.15,0.2,0.1,0.15))` 把成功率、成本反向、审计通过率、平均重试反向、用户反馈归一后加权；历史方案再用 `{current, previous}` 指针、自重启和健康探针把部署变成可退的重定向。当前只保留五维 fitness 与评估推荐，指针部署方案已经移除。

**借来的子件**：fitness 概念源于优化/进化算法；指针文件、蓝绿/金丝雀是业界常见 pattern。当前可归属的实现贡献是五维信号与配对评估；已移除的自重启方案只作为历史设计记录。

**当前代码落点**：`src/tianshu/universe/fitness.py`、`src/tianshu/universe/eval_harness.py`　**文档**：[../design/universe/eval.md](../design/universe/eval.md)、[../design/universe/code-variant.md](../design/universe/code-variant.md)

---

## 三、与借鉴的边界（诚实声明）

以下机制的**主体是借来的**，天枢只做了扩展，因此**不算我们的核心原创**，列在此处划清边界。完整借鉴出处见 [reference-projects.md](./reference-projects.md)。

- **鸿胪寺 hongluisi 外网治理**——`EngineRegistry`（引擎热更新）+ `CredentialInjector`（凭证自动注入、禁止用户手写 auth headers）+ `FetchRouter`（override/fallback 联动）+ `SSRFGuard`（协议/端口/DNS+CIDR 多层防护）+ `RateLimiter`（per-Edict 滑动窗口）+ Edict 级 host 白名单。**SSRF 防护是通用安全最佳实践、滑动窗口是标准算法**——主体借来；天枢的扩展是把它们集成成「可治理的网络服务」（引擎可插拔、凭证中心托管、override 强制关 fallback、host 白名单不可绕）。代码落点：`tools/hongluisi/`、`secrets/`。文档：[../design/tools/network.md](../design/tools/network.md)。

> 边界原则：凡是「业界通用安全/算法/标准库 + 天枢做集成」的，归此节，主体见借鉴篇，不主张为原创原语。

---

## 四、一句话总结

天枢最具差异化的三个原创：

1. **奏章式治理领域模型**——`Edict / Memorial / Decree` 把「谁执行 / 是否通过 / 如何干预」三个治理维度分层建模为数据结构 + 状态机，让多 Agent 在统一权限矩阵（六部官制）下运转。
2. **验收驱动的长程自检升级**——`AcceptanceCriteria` 驱动的 actor→checks→critic→L1/L2/L3 升级外环，把「长任务该怎么评、评不过怎么升级」做成可配可复用的闭环。
3. **行为 + 代码双层演化评估**——把 agent 自我改进从单线升级为行为配置候选与 git worktree 代码候选的分支、Gate 和五维评估；当前受治理 canary/promotion 的已实现生产路径限于 Skill Candidate，代码 live activation 仍 fail-closed。

其余原语（ReAct / compaction / fuzzy match / fitness / SSRF）多站在巨人肩上，出处见 [reference-projects.md](./reference-projects.md)。**天枢的价值不在原语，而在把治理、长程自治、自进化组织成一个连贯系统。**
