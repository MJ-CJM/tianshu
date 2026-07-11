# 代码变体位面（Phase 2：代码层）

> 把分叉从「行为配置层」延伸到「代码层」——每个代码变体位面 = 一份可独立运行的 git worktree 变体。平台能自动产出代码级变异、在受治理的子进程中评估、择优晋升。本篇讲契约与边界，代码落点见 [../../impl/universe/](../../impl/universe/)。
> 设计源头：`docs/superpowers/specs/2026-06-08-code-variant-universe-design.md`。

## 1. 为什么代码层「质变地更难」

| 差异 | 后果 |
|---|---|
| Python 不能在运行中的进程里热替换代码 | 代码变体靠 worktree + 受管子进程生命周期；`switch` = **重定向 + 重启**，而非复制文件原地切换 |
| 自动生成的代码是无界风险面（崩溃/死循环/泄漏/破坏） | 必须有带门禁的评估底座，任何代码碰真实任务前都要过关 |
| 「择优」需 fitness，但不能拿可能损坏的代码跑真实流量 | 评估使用独立 DB 路径且不注入生产 DB；`trusted-local` 仍不能阻止恶意变体主动访问宿主资源 |

本质 = **一条带门禁、可治理且可验证的自我改进代码闭环**：

```
在隔离 worktree 生成补丁 → 硬门禁(编译+import+测试全绿)
  → 受管子进程跑评估集回放打 fitness → 人工审核(看 diff + 评估) 晋升
  → git 重定向 + 重启切换 → 随时 git 回滚
```

## 2. 核心铁律

**一个代码变体位面 = 一份 git worktree（携带变体代码）+ 共享运行态 DB（评估时只注入独立 DB 路径）。**

三条不变量（前两条继承 Phase 1）：

1. 分叉「行为」（现含代码），共享「知识 + 历史」——记忆/edicts/memorials/cost/audit 仍是同一份 SQLite。
2. 非 champion 冻结，champion 是工作副本。
3. **🔑 评估期只向变体注入独立 DB 路径，不注入生产 DB 路径**——这降低误写风险，
   但 `trusted-local` 无法阻止恶意变体主动查找宿主文件。只有过门禁 + 人工晋升后，
   被信任的代码才按正常配置接共享 DB。

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
| **switch / promote** | 复制文件回 live | 重定向 `current_ref` + 重启进程 |
| **diff** | 文件 diff | `git diff start_ref`（含已提交与未提交改动，相对 fork 起点） |
| **archive** | 标记 + 保留快照 | 标记 + `gc_worktree`：删 worktree 工作文件，**保留分支/commit/meta**（可 restore） |
| **restore** | archived → challenger | 同时从保留分支 `worktree add` 重建工作文件 |
| **delete** | 删目录 | `remove`：删 worktree + 分支 + sidecar（不可恢复，区别于 gc 保留分支） |

`UniverseManager` 以「位面 = 一组可重定向的运行态」为抽象（Phase 1 §12 预留点），代码变体作为新的重定向维度接入，不破坏 1a/1b 契约。注意：行为层 `switch` 对带 `code_ref` 的位面直接拒绝——代码变体晋升必须走 Deployer 受控路径。

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
| ① 静态（秒级） | `compileall` + `import tianshu` 冒烟 | 连 import 都失败的直接毙 |
| ② 测试（分钟级） | worktree 内全量 `pytest` 必须全绿（champion 基线即 0 失败） | 仓库现成的最强安全网 |
| ③ fitness | 只有过 ①② 才配打分 | — |

### 5.3 EvalHarness（回放评估集打分）

从历史已完成 edict 取代表性 goal 列表（去重），在沙箱隔离 DB 上回放，聚合五维信号喂给 `compute_fitness`（与行为层共用同一适应度语义，保证可比）。变体 fitness vs champion fitness 在同一评估集上比，超 `universe_promote_margin` 才算赢。每次评估记入 `variant_eval_runs` 表。

设计文档提及两种模式（cassette 回归 / live 评估）；当前实现以 live 评估为主路径。

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

## 7. 晋升与回滚（Deployer）

| 环节 | 契约 |
|---|---|
| 晋升 | `current_ref` 指针翻到变体（旧 current 入 previous）→ `os.execv` 重新 exec launcher（PID 不变，容器存活，无需外部 supervisor） |
| 引导 | launcher 读指针：current 指向变体则 cwd=worktree + `PYTHONPATH` 前置其 src；为空则用主仓启动 |
| 暂存 | `stage` 只写指针不重启，供「先暂存、稍后受控重启」（manager 的 `promote_code_variant` 用此：翻状态为冠军 + 暂存 deploy 指针，重启是单独受控步骤） |
| 自动回滚 | 重启后查 `/health`，不健康 → 指针翻回 previous + 再 exec（坏晋升绝不让平台停摆） |
| 回滚 | 重定向回旧 commit + 重启，瞬时（git） |

## 8. 来源分类：位面改变 vs 代码改变

同一套 `universes` 表 + `UniverseManager` 承载两类位面，靠 `code_ref` 是否为空区分调度路径：

| 类别 | origin | code_ref | 分叉的是 | switch 语义 |
|---|---|---|---|---|
| 行为层位面 | genesis / manual_branch / mutation | None | personas/skills/config 文件快照 | 复制文件回 live（原地热切换） |
| 代码变体位面 | code_variant | 分支名/SHA | git worktree 代码 | 重定向 `current_ref` + 重启（Deployer） |

manager 在 branch/switch/diff/archive/restore/delete 各动作里据 `code_ref` 分派到 `UniverseStore`（文件拷贝）或 `CodeVariantStore`（git worktree）。

## 9. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `code_variant_enabled` | False | 代码变体总开关（opt-in） |
| `code_variant_evolvable_paths` | selector/planner/tools 等 | 演化域 allowlist（机制支持全仓，策略默认受限） |
| `code_variant_auto_promote` | False | 代码层自动晋升（默认关，明确不推荐开） |
| `code_variant_auto_propose` | False | 太医诊断器自主提案总开关（默认关，见 §6b） |
| `code_variant_daily_propose_quota` | 2 | 自主提案每轮配额（诊断假设数上限） |
| `code_variant_sandbox_timeout_s` | 900 | 沙箱门禁+评估全程超时 |
| `code_variant_eval_set_size` | 20 | 回放评估集规模（60% 成功 + 40% 失败混采） |
| `code_variant_eval_budget_cny` | 20.0 | 单次沙箱评估成本闸（元），触顶截断，详见 [./eval.md](./eval.md) §9 |

晋升 margin / 样本量复用 Phase 1 的 `universe_promote_margin` / `universe_min_samples`。

## 10. 安全主防线

自我修改代码是高风险面，控制分层：测试门禁是最强安全网；独立 DB、
`EVAL_MODE` 副作用围栏、wall timeout、进程组收敛与终态收据降低评估期风险，
但不会把 `trusted-local` 变成强沙箱。`TIANSHU_EVAL_LLM_*`（详见
[./eval.md](./eval.md) §8）可给评估配独立低额度 LLM 凭证，不设则沿用宿主凭证。
**🔴 残余风险**：untrusted 变体拿到 key 后理论上可外泄；专用低额度凭证只缩小
损失面，不能消除机制风险。结论：**晋升前人工审完整 diff 是主控制**，代码层
auto-promote 默认关且不推荐开。每次变异/评估/晋升/回滚发 EventBus 事件进审计面板。

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

### 11.4 DeployPointer 自动回滚与健康检查

晋升不是「替换文件」而是「**重定向 + 重启**」——Python 不能热替换运行中进程的代码（§1）。`Deployer`（`deployer.py`）靠一个持久化指针文件做这件事：

- `DeployPointer` 存 `{current, previous}` 两条 `DeployRecord`。`promote` = `stage`（current 翻到变体、旧 current 入 previous）+ `os.execv` 自重启（PID 不变、容器存活、无需外部 supervisor）；launcher 引导时读指针决定 cwd 与 `PYTHONPATH`。
- **为什么必须自动回滚**：自重启后新代码可能根本起不来（坏晋升）。`verify_or_rollback` 在重启后轮询 `/health`（默认 30s），不健康就把指针翻回 `previous` 再 exec 一次。**保留 previous 这一格，是为了让「坏晋升」永远有一条无人工干预的退路**——平台绝不能因为一次自我改进失败就停摆。回滚本身是瞬时的（git 重定向 + 重启），因为代码早已 commit 在分支上，不需要重新构建。

指针 + previous 槽 + 健康探针三者合起来，把「不可逆的部署」变成「随时可退的重定向」。

### 11.5 生产前置的风险评估

机制能把风险关进沙箱，但**关不住「评估必须 live」带来的残余风险**：live 评估要真实 LLM key，untrusted 变体进程在评估期就能拿到这个 key，理论上可外泄（§10 的 🔴）。这是当前架构的已知边界，无法靠更多自动门禁消除。因此设计上把最后一道闸明确交给人：

- 代码层 `code_variant_auto_promote` **默认关，且文档明确标注「不推荐开」**——区别于行为层可以更激进地自动晋升。
- 晋升前的**主控制是人工审完整 `diff`**（`CodeVariantStore.diff` 相对 fork 起点输出全量改动），配合评估记录（`variant_eval_runs`）一起看；门禁全绿只是「允许进入人工审核」的入场券，不是放行令。
- 演化域 `code_variant_evolvable_paths` 默认只含 selector / planner / tools 等低风险路径——机制支持全仓改写，但**策略默认收紧风险面**，把自动变异限制在「改坏了也容易看懂、容易回滚」的区域。

一句话：自动化负责「快速产出 + 廉价淘汰」，人工负责「最后一道信任决策」。这条分工是整个代码变体位面安全性的压舱石。

**相关实现**：[../../impl/universe/](../../impl/universe/)
**相关设计**：[./eval.md](./eval.md)、[./evolution.md](./evolution.md)
