# 代码变体位面（Phase 2：代码层）

> 每个代码变体位面是一份独立 git worktree。当前平台能自动产出变异、执行 Gate 和
> 受治理评估，并给出 `evaluated` / `recommended` 结论；**不会把变体部署为 live
> 代码**。旧设计中的 Deployer、DeployPointer、launcher、自重启与自动健康回滚已移除。
>
> 本篇以当前边界为准；历史设计源头不代表现有可用能力。代码落点见
> [../../impl/universe/](../../impl/universe/)。

## 1. 为什么代码层「质变地更难」

| 差异 | 后果 |
|---|---|
| Python 不能在运行中的进程里热替换代码 | worktree 只用于隔离提案与评估；当前不提供 code switch / deploy |
| 自动生成的代码是无界风险面（崩溃/死循环/泄漏/破坏） | 必须有带门禁的评估底座，任何代码碰真实任务前都要过关 |
| 「择优」需 fitness，但不能拿可能损坏的代码跑真实流量 | 评估使用独立 DB 路径且不注入生产 DB；`trusted-local` 仍不能阻止恶意变体主动访问宿主资源 |

本质 = **一条带门禁、可治理且可验证的自我改进代码闭环**：

```
在隔离 worktree 生成补丁 → 硬门禁(编译+import+测试全绿)
  → 受管子进程跑评估集回放打 fitness → 人工审核 diff + 评估
  → evaluated / recommended（当前终点，不改变 live 代码）
```

## 2. 核心铁律

**一个代码变体位面 = 一份携带变体代码的 git worktree；评估只使用独立 DB。**

三条不变量（前两条继承 Phase 1）：

1. Legacy Universe 元数据和历史仍在主 SQLite；变体评估数据写入隔离 DB，再把评估结果汇总回主库。
2. 非 champion 冻结，champion 是工作副本。
3. **🔑 评估期只向变体注入独立 DB 路径，不注入生产 DB 路径**——这降低误写风险，
   但 `trusted-local` 无法阻止恶意变体主动查找宿主文件。当前即使门禁全绿也不会让
   变体进程接管生产服务。

与 Phase 1 正交：v1 里一个代码变体 = champion 的数据层 + 变体的代码，纯代码 delta → fitness 归因干净。

## 3. 代码变体的表示与存储（CodeVariantStore）

与 UniverseStore（行为配置的文件拷贝快照）**正交**——CodeVariantStore 只管「代码层」：

| 项 | 约定 |
|---|---|
| 一个代码变体 | 主仓一条分支 `universe/<id>` + 一份 worktree |
| 真相源 | git 是代码的唯一真相源 |
| fork 起点 | `start_ref`（解析为 SHA）记在 sidecar `_meta/<id>.json`，供 diff / restore 复用 |
| `code_ref` | 持久化到 `universes.code_ref`，行为层位面该列为 None |

## 4. 生命周期：行为层 vs 代码层对照

| 动作 | Phase 1（数据） | Phase 2（代码） |
|---|---|---|
| **branch** | 复制文件树 | `git worktree add -b universe/<id>`（不复制行为配置层数据） |
| **switch / promote** | 当前固定拒绝，不复制回 live | 当前固定拒绝，不重定向或重启 |
| **diff** | 文件 diff | `git diff start_ref`（含已提交与未提交改动，相对 fork 起点） |
| **archive** | 标记 + 保留快照 | 标记 + `gc_worktree`：删 worktree 工作文件，**保留分支/commit/meta**（可 restore） |
| **restore** | archived → challenger | 同时从保留分支 `worktree add` 重建工作文件 |
| **delete** | 删目录 | `remove`：删 worktree + 分支 + sidecar（不可恢复，区别于 gc 保留分支） |

`UniverseManager` 仍管理 branch/diff/archive/restore/delete，但
`switch()`、`rollback()` 与 `promote_code_variant()` 都固定 fail-closed；旧
`/api/universes/{id}/switch` 和 `/promote-code` 固定返回 409。archive/restore 只管理
快照或 worktree 生命周期，不等于激活。

## 5. 沙箱评估与门禁

### 5.1 SandboxRunner（受治理子进程）

从变体 worktree 拉起受管子进程：临时端口 + 独立 DB + wall timeout +
`TIANSHU_EVAL_MODE=1`，等 `/health` 健康，退出时收敛进程组并清理 DB 及
WAL/SHM。关键：editable 安装下用 `PYTHONPATH=<worktree>/src` 前置遮蔽，确保
跑的是变体代码而非主仓。当前 `trusted-local` 只是显式宿主回退，不声明内存、
CPU、文件系统或网络强隔离；`secure-remote` 缺少受验证后端时 fail-closed。

### 5.2 Gate（三关门禁，fail-fast）

| 关 | 检查 | 失败即毙 |
|---|---|---|
| ① static（秒级） | `compileall` | 语法或编译失败直接拒绝 |
| ② import（秒级） | `import tianshu` 冒烟 | 模块加载失败直接拒绝 |
| ③ test（分钟级） | worktree 内全量 `pytest` 必须全绿（champion 基线即 0 失败） | 任一测试失败直接拒绝 |

Fitness 不属于 `Gate` stage；只有三关全过才进入配对评估。

### 5.3 EvalHarness（回放评估集打分）

从历史已完成 edict 取代表性 goal 列表（去重），在沙箱隔离 DB 上回放，聚合五维信号喂给 `compute_fitness`（与行为层共用同一适应度语义，保证可比）。变体 fitness vs champion fitness 在同一评估集上比，超 `universe_promote_margin` 才算赢。每次评估记入 `variant_eval_runs` 表。

设计文档提及两种模式（cassette 回归 / 在线模型评估）；当前实现以在线模型评估为主路径。

## 6. 自改代码（CodeMutator）

是 Phase 1 人格 mutator 的代码版，在变体 worktree 内按假设让 LLM 改写一个代码文件并提交。三重安全：

| 约束 | 行为 |
|---|---|
| **演化域 allowlist 强制** | 目标须落在 `code_variant_evolvable_paths`（默认仅 selector/planner/tools 等低风险路径），越界直接拒 |
| **traversal-safe** | 只能写 worktree 内，路径穿越拒绝 |
| **失败安全** | 任何异常/空输出/无变化 → no-op，不留半截改动 |

成功时 `git add + commit`（作者 `evolver@tianshu`），返回 commit SHA。

`UniverseEvolver.propose_code_variant` 编排完整闭环：`分支 → 变异 → 门禁 → 评估 → 记录 → 推荐`，失败安全，状态机覆盖 `disabled/no_collaborators/no_champion/no_mutation/gate_failed/evaluated/recommended/error`。默认**不自动晋升**。

## 6b. 太医诊断器与自主提案（Diagnostician）

`CodeMutator` 需要一对 `(target_path, hypothesis)` 才能动手——此前这对参数只能人工给。**Diagnostician**（`diagnostician.py`，喻「太医」）补上这一环：从平台自身的失败症状里提炼演化假设，交给既有的 `propose_code_variant` 闭环，人不再是唯一的假设来源。只诊断、不动刀——真正的改写/门禁/评估仍是原有安全链。

| 环节 | 契约 |
|---|---|
| 诊断输入 | 近期 `status=failed` 的 memorial（`goal` 截 120 字 + `error` 截 200 字 + 审计 `reasons` 截 200 字，默认取最近 30 条）+ 已试过的假设（近期 `origin=code_variant` 且带 `description` 的位面，倒序取 20 条，避免开重复药方） |
| allowlist 过滤 | LLM 提议的每条 `target_path` 都要过 `code_mutator._within_evolvable` 校验，越界（不在 `code_variant_evolvable_paths` 内）直接丢弃，不进候选清单 |
| 失败安全 | 无失败症状 / LLM 输出非 JSON 数组 / 全部越界 → 返回空列表 `[]`；三试非法 JSON 后放弃，不抛异常 |
| 配额执行 | `auto_propose_codes`：诊断最多要 `code_variant_daily_propose_quota`（默认 2）条假设，逐条同步调用 `propose_code_variant`（分支→变异→门禁→评估，见 §5-6），不并发 |
| 触发方式 | cron `universe.daily_code_propose`，`30 5 * * *`（每日 05:30，晚于 05:00 的行为层演化 `daily_evolve` 半小时，错峰）；也可手动 `POST /api/universes/propose-auto` 立即触发一轮 |
| 默认态 | `code_variant_auto_propose=False`——诊断器接线到位，但默认不真正自主开方，需显式开启 |
| 并发保护 | 与人格演化共用 `try_acquire_synthesis_lock` 机制，但用独立 lock key（`__universe_code_propose__`），互不阻塞 |

一次 `auto_propose_codes` 返回 `{"proposed": n, "results": [...]}`（或 `{"skipped": 原因}`），并发 `universe.code_proposed` 事件供审计面板订阅。

## 7. 当前晋升与回滚边界

当前有两条必须分开的链：

| 链 | 当前真值 |
|---|---|
| Legacy Universe | 负责 worktree 提案、Gate、评估和 `recommended`；`UniverseManager.switch/rollback/promote_code_variant` 固定抛 `promotion_service_required`，旧 switch/promote-code HTTP 接口固定 409 |
| Governed Candidate | `PromotionService` 是 canary / promote / rollback 的唯一 lifecycle 与 routing writer；HTTP 入口是 `/api/evolution/candidates/{id}/{canary,promote,rollback}` |

Candidate canary 在创建 Memorial 的同一事务中固化 `RunAssignmentV1` 与
`EffectiveEvolutionOverlayV1`；dispatcher claim 后按该 assignment 加载已校验 payload，
重试不重新分桶。当前生产装配只有 Skill Candidate 具备真实 activation/rollback
adapter，Skill loader 也会消费 per-run overlay。

Code Candidate 的 proposal/schema/Gate 基础存在，但生产 promotion adapter 是
`UnavailablePromotionAdapter`，因此 live activation/rollback fail-closed。代码晋升即使
未来接入 adapter，也必须绑定精确、已 resolved 且 action=approve 的高风险
`GOVERNED_APPLY` Decision。仓库当前不存在 DeployPointer、launcher、自重启或健康检查
自动回滚实现。

## 8. 来源分类：位面改变 vs 代码改变

同一套 `universes` 表 + `UniverseManager` 承载两类位面，靠 `code_ref` 是否为空区分调度路径：

| 类别 | origin | code_ref | 分叉的是 | 当前激活语义 |
|---|---|---|---|---|
| 行为层位面 | genesis / manual_branch / mutation | None | personas/skills/config 文件快照 | 无；legacy switch 固定拒绝 |
| 代码变体位面 | code_variant | 分支名/SHA | git worktree 代码 | 无；只到评估/推荐 |

manager 在 branch/diff/archive/restore/delete 动作里据 `code_ref` 分派到
`UniverseStore`（文件拷贝）或 `CodeVariantStore`（git worktree）；不再承担 live mutation。

## 9. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `code_variant_enabled` | False | 代码变体总开关（opt-in） |
| `code_variant_evolvable_paths` | selector/planner/tools 等 | 演化域 allowlist（机制支持全仓，策略默认受限） |
| `code_variant_auto_promote` | False | 兼容保留字段；当前 evolver 不读取，不会开启自动晋升 |
| `code_variant_auto_propose` | False | 太医诊断器自主提案总开关（默认关，见 §6b） |
| `code_variant_daily_propose_quota` | 2 | 自主提案每轮配额（诊断假设数上限） |
| `code_variant_sandbox_timeout_s` | 900 | 沙箱门禁+评估全程超时 |
| `code_variant_eval_set_size` | 20 | 回放评估集规模（60% 成功 + 40% 失败混采） |
| `code_variant_eval_budget_cny` | 20.0 | 单次沙箱评估成本闸（元），触顶截断，详见 [./eval.md](./eval.md) §9 |

`universe_promote_margin` 只决定是否返回 `recommended`；不触发部署。

## 10. 安全主防线

自我修改代码是高风险面，控制分层：测试门禁是最强安全网；独立 DB、
`EVAL_MODE` 副作用围栏、wall timeout、进程组收敛与终态收据降低评估期风险，
但不会把 `trusted-local` 变成强沙箱。`TIANSHU_EVAL_LLM_*`（详见
[./eval.md](./eval.md) §8）可给评估配独立低额度 LLM 凭证，不设则沿用宿主凭证。
**🔴 残余风险**：untrusted 变体拿到 key 后理论上可外泄；专用低额度凭证只缩小
损失面，不能消除机制风险。结论：**人工审完整 diff 是进入任何未来 live activation
设计的前置控制**，但当前审完也不会部署代码。Legacy Universe 记录变异/评估事件；
Candidate canary/promote/rollback 由 PromotionService 写持久 journal、outbox 与系统审计。

## 11. 设计 rationale：每个选择背后的「为什么」

前面几节讲「契约是什么」，本节集中回答「为什么这样设计」。评估底座的更细 rationale（选集 / 打分 / 回归比对）见 [./eval.md](./eval.md)。

### 11.1 为何允许 LLM 改自身代码

Phase 1 的 mutator 只能改「行为配置层」（personas / skills / config 的文件快照）——但很多瓶颈在配置层根本够不着：selector 的挑选逻辑、planner 的拆解策略、tools 的实现细节都写死在 Python 里。**只靠 prompt 调参，迭代空间被代码本身封顶**。把分叉延伸到代码层，等于把「改提示词」升级成「改算法」，单步改进的上界更高、迭代更快。代价是无界风险面——所以代码版 `CodeMutator` 是 Phase 1 人格 mutator 的**直接对应物**，沿用同一套「失败安全 + allowlist + traversal-safe」三重约束（见 `code_mutator.py`），只是把「写配置文件」换成「写 worktree 内的 `.py` 并 `git commit`」。能力放开，纪律不放开。

### 11.2 三段安全门为何是 compile → import → test 这个顺序

`Gate.run`（`gate.py`）逐级 **fail-fast**，顺序按「成本递增 / 命中率递减」排：

| 关 | 成本 | 拦掉的典型坏变体 | 为何排这里 |
|---|---|---|---|
| ① `compileall` | 秒级 | 语法错、缩进炸、括号不配对 | 最廉价的过滤器先跑——LLM 改写最常见的低级错在这一关秒毙，不浪费后面分钟级的 pytest |
| ② `import tianshu` | 秒级 | 语法过了但模块级炸（坏 import、顶层异常、循环依赖） | 比编译强、比测试弱的中间网；连 import 都失败的代码连测试都跑不起来，提前短路 |
| ③ `pytest -q` | 分钟级 | 编译/导入都过、但行为被改坏（回归） | 仓库现成的最强安全网，最贵所以最后跑；champion 基线即 0 失败，变体必须同样全绿 |

颠倒顺序就是浪费：先跑分钟级 pytest 去撞一个秒级 compile 就能发现的语法错，等于让最贵的关卡做最廉价关卡的活。fail-fast 让 99% 的坏变体在前两关的秒级开销内出局。三关全过才进 fitness（§5.2），**「能跑」是「跑得好」的前置**。

### 11.3 worktree 隔离 + 受治理执行边界为何缺一不可

两层隔离针对两类不同风险，是正交的：

- **worktree（空间隔离 / `code_store.py`）**：每个变体 = 一条 `universe/<id>` 分支 + 一份独立工作树，git 是唯一真相源。变体的改动物理上锁在自己的 worktree 里，碰不到主仓工作副本；评估完只删工作文件、**保留分支可 restore**（gc）。难点：本仓是 editable 安装，裸 `import tianshu` 会解析回主仓 src——所以 Gate / Sandbox 全程注入 `PYTHONPATH=<worktree>/src` 前置遮蔽 + `cwd=<worktree>`，确保检的、跑的都是变体代码而非主仓。
- **沙箱（运行时边界 / `sandbox.py`）**：worktree 只隔离「代码在哪」，拦不住「代码运行时干什么」。`SandboxRunner` 统一经 `ExecutionGateway` 拉起变体，固定临时随机端口、隔离 DB 与 `TIANSHU_EVAL_MODE=1`，并在退出时收敛整个进程组、删除隔离 DB。当前 `trusted-local` 宿主执行仅为显式允许的 best-effort，收据如实记录未强隔离；`secure-remote` 没有可证明的强后端时 fail-closed，不再虚标 `RLIMIT_AS` 或容器能力。

少了 worktree，变体代码会污染主仓；少了受治理执行边界，损坏代码即便编译
通过，也无法获得统一的 wall timeout、进程树收敛和终态收据。两层合起来降低
评估风险，但 `trusted-local` 仍不能阻止变体访问宿主资源；只有未来接入并证明
强制隔离能力的后端，才可宣称内存、CPU、文件系统或网络隔离。

### 11.4 历史方案：DeployPointer 自动回滚

早期 Phase 2 设计曾提出 `current/previous` 指针、进程自重启和健康探针回退。该方案
要求当前进程同时承担部署器与故障恢复器，无法形成足够清晰的独立信任边界，因此相关
Deployer/launcher 实现已经移除。当前源码中没有 deploy pointer 文件，也不会执行
进程内 re-exec 或自动翻回 previous。

如果未来重新开放 Code Candidate live activation，至少需要：

- 在 `PromotionService` 下提供经过验证的 Code promotion/rollback adapter；
- 绑定精确、已批准的高风险 Decision，且保留幂等 journal 与 effect receipt；
- 由独立部署/恢复边界证明启动、健康、回滚和崩溃恢复，而不是恢复旧 UniverseManager
  旁路或仅靠一个指针文件。

### 11.5 生产前置的风险评估

机制能把风险关进沙箱，但**关不住「在线模型评估」带来的残余风险**：这类评估要真实 LLM key，untrusted 变体进程在评估期就能拿到这个 key，理论上可外泄（§10 的 🔴）。这是当前架构的已知边界，无法靠更多自动门禁消除。因此设计上把最后一道闸明确交给人：

- `code_variant_auto_promote` 只是兼容配置字段，当前 evolver 不读取；不存在打开该开关即可部署的路径。
- **人工审完整 `diff`**（`CodeVariantStore.diff` 相对 fork 起点输出全量改动），配合评估记录（`variant_eval_runs`）一起看；门禁全绿只是形成推荐的入场券，不是放行令。
- 演化域 `code_variant_evolvable_paths` 默认只含 selector / planner / tools 等低风险路径——机制支持全仓改写，但**策略默认收紧风险面**，把自动变异限制在「改坏了也容易看懂、容易回滚」的区域。

一句话：自动化负责「快速产出 + 廉价淘汰」，人工负责判断是否值得进入未来受治理
落地；当前平台对 Code Candidate live activation 保持 fail-closed。

**相关实现**：[../../impl/universe/](../../impl/universe/)
**相关设计**：[./eval.md](./eval.md)、[./evolution.md](./evolution.md)
