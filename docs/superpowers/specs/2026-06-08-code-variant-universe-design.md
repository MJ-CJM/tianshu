# 高阶平行位面：代码变体的自进化分叉系统 — 设计文档（Phase 2）

> 承接 Phase 1《平行位面：行为配置的自进化分叉系统》（`2026-06-07-parallel-universe-design.md`）。
> Phase 1 把"自进化"从单线扩展到"行为配置层"的多位面并行择优；Phase 2 把分叉从**行为配置层**进一步扩展到**代码层**——每个位面可以是一份**可独立运行的代码变体**，平台能自动产出代码级变异、并行评估、择优。

---

## 1. 背景与问题

### 1.1 现状：Phase 1 只分叉"数据"

Phase 1 的位面分叉的是**数据**（personas/skills/config 文件），跑在同一份固定代码上。因此它能热切换、靠复制文件回滚、风险可控。但它**碰不到平台自身的代码行为**——selector 的选官启发式、planner 的拆解策略、tool 的实现、executor 的重试逻辑等，都无法被位面机制演化。

### 1.2 用户诉求

让平台的自进化能力延伸到代码本身：随着使用，平台能自动产出代码级变异，在隔离环境里并行评估，择优晋升，从而**持续自我改进**，而不止于换配置。

### 1.3 为什么这是"质变地更难"（清醒定性）

代码层分叉与数据层分叉有三个本质差异：

1. **Python 不能在运行中的进程里热替换代码**（模块 import 后被缓存，`importlib.reload` 对持有引用的 web app 不可靠）。→ 代码变体必须靠**进程/worktree 级隔离**，`switch` 对代码变体 = **重定向 + 重启**，而非复制文件原地切换。
2. **自动生成的代码是无界风险面**——会崩溃、死循环、泄漏、甚至搞破坏。"晋升"意味着平台开始用机器自己写的代码跑真实任务，即自主自我修改。
3. **"择优"需要 fitness 信号，但不能拿可能损坏的代码去跑真实用户流量**。→ 必须有一道**带门禁的评估底座**（先过测试、再用回放评估集打分），任何代码碰真实任务之前都要过这道关。

因此 Phase 2 的本质 = **一条带门禁的、安全的自我改进代码闭环**：

```
在隔离 worktree 里生成补丁  →  硬门禁(编译+import+测试套件全绿)
   →  沙箱里跑评估集回放打 fitness  →  人工审核晋升(看 diff + 评估结果)
   →  git 重定向 + 重启切换  →  随时 git 回滚
```

### 1.4 载重决策（已确认）：变异面 = 全仓 worktree 机制 + 受限默认演化域

机制上变体是**完整 git worktree**，任何文件都"可"改；但**自动变异默认只瞄准一个可配置的"演化域"**（path allowlist，如 selector/planner/tool 启发式），且**代码层 auto-promote 默认关闭**。即：**能力上限给满，安全靠硬门禁 + 人工审核 + 受限默认域，而非阉割机制**。后续放开演化域即可逐步加大胆量。

---

## 2. 概念与边界（核心铁律）

**一个代码变体位面 = 一份 git worktree（携带变体代码）+ 共享运行态 DB（但评估期隔离）。**

三条不变量（前两条继承 Phase 1，第三条是代码层新增的关键安全约束）：

1. **分叉"行为"（现在含代码），共享"知识 + 历史"**——记忆宫殿、edicts、memorials、cost、audit 仍是同一份 SQLite。代码变体只改"平台怎么干活"，不复制"它知道什么/干过什么"。
2. **非 champion 冻结，champion 是工作副本**——沿用 Phase 1 的 git 式模型（champion 同步 live 后再读，非 champion 为冻结快照）。
3. **🔑 评估期的变体跑在完全隔离的 DB 副本上，绝不碰生产 DB**。因为变体代码 untrusted，可能损坏/污染共享状态。只有**通过门禁 + 人工晋升后**，被信任的代码才接真实共享 DB。即"共享知识"这条不变量只对 champion/已晋升代码成立；评估中的变体恰因不可信而全隔离。

**与 Phase 1 数据层的关系（正交）**：v1 里一个代码变体 = **champion 的数据层（personas/skills/config）+ 变体的代码**。纯代码 delta → fitness 归因干净。仓内文件随 worktree 走；运行态数据（DB）按不变量 3 处理。数据层的分叉仍由 Phase 1 位面机制负责，与代码变体正交。

---

## 3. 目标与验收（按增量）

Phase 2 切成 4 个增量，每个走自己的 spec→plan→build。本设计文档覆盖**整个 Phase 2 架构**；实施先从 2a 开始。

### 3.1 增量 2a — 代码变体基底（先做，是 2b 的地基）

- `universes` 表新增 `code_ref` 列；`Universe` 模型新增 `code_ref: str | None`。
- `UniverseStore` 扩展 worktree 操作：`branch_code_variant`（`git worktree add` 新分支）、`worktree_dir(id)`、`diff`（`git diff`）、`gc_worktree`（删工作文件、留分支/commit）。
- `UniverseManager` 的 branch/switch/diff/archive/rollback 增加"代码变体"分支路径（git 语义而非文件复制）。
- 仅支持**手工**代码变体（手动指定一份分支/补丁），不自动生成。
- **验收**：能从 champion 分支出一个代码变体位面（worktree 落盘、`code_ref` 持久化）、能 `git diff` 看差异、能归档并回收 worktree、能恢复。

### 3.2 增量 2b — 沙箱运行器 + 评估底座（安全关键核心）

- `SandboxRunner`：从变体 worktree 拉起隔离子进程（临时端口 + 隔离 DB 副本 + 临时目录 + 资源闸 + `TIANSHU_EVAL_MODE=1`），健康检查，销毁。
- `Gate`：三关门禁——① 静态（compileall + import 冒烟 + ruff）② 测试（worktree 内全量 pytest 全绿，champion 基线即 0 失败）③ 通过后才进 fitness。
- `EvalHarness`：回放历史 memorial 评估集 → 复用 `compute_fitness` 算分；支持 cassette 回归（确定、零成本）与 live 评估（真调 LLM、出 fitness）。
- 新增 `variant_eval_runs` 表记录每次评估。
- **验收**：拉起一个 trivial 变体能拿到健康；测试失败/ import 崩的变体被门禁拒绝或快速失败；`EVAL_MODE` 下外发（邮件/webhook）被 stub；回放一个 fixture edict 能算出 fitness 并与 champion 比较。

### 3.3 增量 2c — 代码变异生成器

- `CodeMutator`：在变体 worktree（作为 CWD）里启动一个 agent，给定 fitness 痛点假设，用平台自身代码编辑工具改代码 → 产出变体分支上的 commit；失败安全（坏输出 → no-op）。
- **演化域 allowlist 强制**：CodeMutator 被指向演化域；门禁拒绝触碰 allowlist 之外的 diff（除非显式放宽）。
- `UniverseEvolver` 增加与人格路径并列的代码变体路径，复用 idle+lock 闸与 recommend 流程。
- **验收**：CodeMutator 在 worktree 产出可门禁的 commit；坏 LLM 输出不污染 worktree；越界 diff 被门禁拒；evolver 能在空闲窗口走完"选靶→分支→变异→门禁→评估→推荐"。

### 3.4 增量 2d — 门禁晋升 + 运维

- `Deployer`：`current_ref` 指针（entrypoint 解析它决定从哪份 worktree 启动）+ `os.execv` 重启 + 重启后健康检查 + 不健康自动回滚到 last-known-good。
- 人工审核 UI：展示 `git diff` + 门禁结果 + fitness 对比；人工批准方可晋升。
- 代码层 auto-promote 默认关闭且明确不推荐开。
- **验收**：晋升能翻转 `current_ref` 并 re-exec 到新代码；坏晋升触发健康检查→自动回滚；回滚瞬时（git）；未经人工审核不会晋升代码变体。

---

## 4. 非目标（本轮不做）

- **always-on 多进程常驻 + 前置路由网关**：评估用按需沙箱即可（并行评估 = 同时拉起多个临时子进程），不做 N 份平台 24h 常驻 + 反向代理。后续可作为扩展。
- **全自动选靶 + 全自动晋升**：v1 选靶从假设种子起步，晋升必经人工。
- **跨语言 / 前端代码变体**：v1 只覆盖后端 Python（`src/tianshu`）。
- **演化域开到全仓**：机制支持，但默认域受限；放开是后续策略调整，不是本轮目标。
- **分布式 / 多机沙箱**：v1 单机子进程隔离。

---

## 5. 设计

### 5.1 代码变体的表示与存储

- 一个代码变体位面映射到 **一条 git 分支 + 一份 worktree**。worktree 路径由 `id` 推导：`<root>/.universes/worktrees/<universe_id>`。
- `universes` 表新增 `code_ref TEXT NULL`（分支名 / commit SHA）；数据层位面该列为 `NULL`。
- `Universe` 模型新增 `code_ref: str | None`；`origin = UniverseOrigin.CODE_VARIANT`。
- 本仓库已在用 worktree（`tianshu-feishu-bot`），是已知可行模式。

### 5.2 生命周期：branch / switch / diff / archive / rollback

| 动作 | Phase 1（数据） | Phase 2（代码） |
|------|----------------|----------------|
| **branch** | 复制文件树 | `git worktree add` 新分支 |
| **switch** | 复制文件回 live | **重定向 `current_ref` + 重启进程** |
| **diff** | 文件 diff | `git diff` |
| **archive** | 标记 + 保留快照 | 标记 + 删 worktree 工作文件（保留分支/commit，可恢复） |
| **rollback** | 复制旧快照回 live | 重定向回旧 commit + 重启 |

`UniverseManager` 以"位面 = 一组可重定向的运行态"为抽象（Phase 1 §12 预留），代码变体作为新的重定向维度接入，不破坏 1a/1b 契约。

### 5.3 执行模型：按需评估沙箱

- **champion**：正常运行的主进程（uvicorn:8000，trusted，真实 DB），始终在线服务。
- **评估中的变体**：从其 worktree 拉起一个**子进程**——临时端口 + 隔离 DB 副本 + 临时目录 + 资源上限（wall timeout / 内存 rlimit / CPU）+ `TIANSHU_EVAL_MODE=1`，跑完即销毁。**并行评估 = 同时拉起多个**。
- 每个变体**确实"可独立运行"**（完整 worktree，可手动 `uvicorn` 起任意一个）；沙箱只是把这件事自动化并加隔离与资源闸。

### 5.4 评估与门禁

三道关，逐级加重、快速失败：

```
① 静态门禁（秒级）   compileall + import 冒烟 + ruff  →  连 import 都失败的直接毙
② 测试门禁（分钟级） 在 worktree 里跑全量 pytest（现有 ~1169 测试）
                     必须全绿（champion 基线即 0 失败）  ←  仓库现成的最强安全网
③ fitness 评估       只有过了 ①② 才配打分
```

**fitness 来源 = 回放历史 memorial**：平台已存真实 memorial（goal + 执行结果）。挑代表性子集做**回放评估集**，在沙箱里（隔离 DB + 注入 fixture）让变体跑一遍，量五维信号（success/cost/audit/retry/feedback）→ 直接喂现有 `fitness.py`。变体 fitness vs champion fitness 在**同一评估集**上比，超过 `universe_promote_margin` 才算赢。

**两种评估模式**：

| 模式 | 用途 | 成本 | 确定性 |
|------|------|------|--------|
| **cassette 回归** | 录制 champion 的 LLM 往返，回放给变体——查"有没有改坏既有行为" | 零（不调真 LLM） | 确定 |
| **live 评估** | 真调 LLM 跑评估集——查"新行为是不是更好" | 真金白银 | 需多跑取均值 |

cassette 适合做第 ② 关的补充回归；变体若改了 prompt/逻辑，cassette 录的是旧路径、测不准"更好"，故**"更好"必须靠 live 评估打 fitness**。

### 5.5 自动代码生成（CodeMutator）

把 Phase 1 的人格 mutator 升级成"在 worktree 里跑一个 agent 写补丁"：以变体 worktree 为 CWD 启动 agent，给它从 fitness 痛点推导的任务，它用平台**自身的代码编辑工具**改代码 → 产出变体分支上的一个 commit。失败安全（坏输出 → no-op，沿用 Phase 1 mutator 语义）。

- **目标选择**：由 fitness 信号驱动（成本高→优化 token；重试高→修重试逻辑；audit 失败→修对应行为）。v1 从"假设种子"（人工或"最差维度→候选模块"简单启发式）起步，全自动选靶后置。
- **演化域强制**：CodeMutator 被指向演化域 allowlist；门禁拒绝触碰 allowlist 之外的 diff。机制允许全仓，策略约束默认范围。

### 5.6 evolver 集成（代码路径）

`UniverseEvolver` 增加一条与人格路径并列的代码变体路径，复用同一套 idle+lock 闸：

```
空闲 → 从 fitness 信号选靶 → git worktree add 候选 → CodeMutator 写补丁
   → 门禁①② → live 评估 → 记 fitness → 若超 margin 且门禁绿 → 推荐(非自动晋升)
   → 人工在 UI 审核批准
```

### 5.7 晋升与回滚（Deployer）

- 晋升 = `current_ref` 指针翻到变体 commit → 进程 `os.execv` 重新 exec 指向新 worktree。Docker 下 PID 1 re-exec 容器存活，**不需要外部 supervisor**。
- 重启后查 `/health`；N 秒内不健康 → 指针翻回 last-known-good + 再 exec（自动回滚兜底）。坏晋升绝不让平台停摆。
- 回滚 = 重定向回旧 commit + 重启，瞬时（git）。
- 旧 champion 分支/commit 保留以备回滚。

### 5.8 适应度函数

直接复用 Phase 1 `fitness.compute_fitness`（五维加权 success/cost/audit/retry/feedback），不重造。代码变体与数据位面共用同一 fitness 语义，保证可比。

### 5.9 配置项（`config_manager.py`，沿用现有风格）

在 `AgentConfigState`（frozen dataclass，`dataclasses.replace` 更新）新增（默认值为推荐起点，实现期可调）：

- `code_variant_enabled: bool = False` — 代码变体总开关（opt-in）。
- `code_variant_evolvable_paths: tuple[str, ...] = ("src/tianshu/persona/selector.py", "src/tianshu/planner/", "src/tianshu/tools/")` — 演化域 allowlist（默认仅 selector/planner/tool 等低风险路径）。
- `code_variant_auto_promote: bool = False` — 代码层自动晋升（默认关，明确不推荐开）。
- `code_variant_sandbox_timeout_s: int = 900`（覆盖门禁+评估全程）/ `code_variant_sandbox_mem_mb: int = 2048` — 沙箱资源闸。
- `code_variant_eval_set_size: int = 20` — 回放评估集规模（与 Phase 1 `universe_min_samples` 同量级）。
- 晋升 margin / 样本量复用 Phase 1 的 `universe_promote_margin` / `universe_min_samples`。

环境侧 `TianshuSettings`（env_prefix `TIANSHU_`）新增 `code_variant_enabled` 与 `eval_mode`（沙箱内置 `TIANSHU_EVAL_MODE=1`）。

---

## 6. 组件改动清单（文件级）

**复用 / 扩展（不重造）**：

- `src/tianshu/universe/model.py` — `Universe` 加 `code_ref`；`UniverseOrigin.CODE_VARIANT` 已存在。
- `src/tianshu/universe/store.py` — 加 worktree 增删/分支/diff/GC（manifest 代码层段已预留）。
- `src/tianshu/universe/manager.py` — branch/switch/diff/archive/rollback 加 git 分支路径。
- `src/tianshu/universe/evolver.py` — 加代码变体提案路径，复用 idle+lock+recommend。
- `src/tianshu/universe/fitness.py` — 原样复用。
- `src/tianshu/storage.py` — `universes.code_ref` 列 + 新 `variant_eval_runs` 表 + 迁移。
- `src/tianshu/config_manager.py` / `src/tianshu/config.py` — 新增配置项（§5.9）。
- `src/tianshu/app.py` — 装配新组件；EVAL_MODE 下 stub 外发；`current_ref` 解析。
- `src/tianshu/gateway/api.py` — 代码变体相关端点（diff/eval 结果/晋升审核）。
- EventBus / synthesis lock / scheduler — 原样复用。

**新组件（各单一职责、可独立测试）**：

| 组件 | 职责 | 依赖 |
|------|------|------|
| `CodeVariantStore`（可并入 `UniverseStore`） | worktree 增删/分支/diff/GC | git |
| `SandboxRunner` | 拉起隔离子进程（临时端口+隔离DB+资源闸+EVAL_MODE）、健康检查、销毁 | subprocess、DB 快照 |
| `EvalHarness` | 回放评估集 → 算 fitness | SandboxRunner、memorial 仓库、`compute_fitness` |
| `Gate` | 静态 + 测试门禁 | subprocess（compileall/pytest） |
| `CodeMutator` | agent-in-worktree 写补丁 + 演化域 allowlist 强制 | executor/agent、llm |
| `Deployer` | `current_ref` 指针 + re-exec + 健康检查 + 自动回滚 | os.execv |

由 `UniverseEvolver`（代码路径）编排。建议新增 `src/tianshu/universe/code/` 子包收纳 SandboxRunner/EvalHarness/Gate/CodeMutator/Deployer，保持文件聚焦（参考 `.claude/rules` 的"many small files"）。

---

## 7. 错误处理与边界

- **资源闸**：沙箱子进程 wall timeout / 内存 rlimit / CPU 上限，杀死失控变体。
- **隔离 DB**（不变量 3）：评估跑在 DB 副本/临时库，损坏的变体污染不了生产。
- **副作用围栏**：`TIANSHU_EVAL_MODE=1` 下 notifier/webhook/外发渠道指向 null sink。
- **门禁前置**：①② 在变体跑评估前拦掉绝大多数损坏。
- **晋升重启失败 → 自动回滚**：health 检查不过即翻回 last-known-good 并 re-exec。
- **磁盘 GC**：归档变体删 worktree 工作文件（留分支/commit），并发 worktree 数设上限。
- **并发**：复用 Phase 1 synthesis lock + idle 闸；同一时刻只允许一个晋升重启。
- **CodeMutator 失败安全**：坏 LLM 输出 / 越界 diff → no-op，不污染 worktree。

---

## 8. 安全

自我修改代码是高风险面，安全设计分层且诚实：

- **测试门禁**是第一道也是最强的安全网（仓库现成 ~1169 测试）。
- **隔离 DB + 副作用围栏 + 资源闸**把评估期变体关在沙箱里。
- **🔴 密钥残余风险（摊开说）**：live 评估要 LLM key → untrusted 变体进程能拿到 key → 理论上可外泄。控制：(a) 可行则用受限/临时 key；(b) 评估出网 allowlist 只放 LLM 端点；(c) **晋升前人工审完整 diff**——抓后门/外泄的主防线；(d) 评估全程审计留痕。**结论：带 key 访问的自动生成代码本质有风险，人工审核 diff 是主控制；代码层 auto-promote 默认关且不推荐开。**
- **审计**：每次 变异/评估/晋升/回滚 发 EventBus 事件，进审计面板。
- 沿用项目安全基线：无硬编码密钥、输入校验、参数化查询（见 `.claude/rules/common/security.md`）。

---

## 9. 测试策略

按增量（沿用用户"功能优先、测试最后补"偏好，但 **Gate / Deployer / 隔离 三块安全件建议同步写测试**，其余功能后补）：

- **2a**：临时 git repo 内测 worktree branch/switch/diff/rollback/GC；`code_ref` 往返；manifest 代码段。
- **2b**：SandboxRunner 拉起 trivial 变体拿到健康；EvalHarness 回放 fixture edict 算 fitness；Gate 对"测试失败/ import 崩"的变体分别拒绝/快速失败；EVAL_MODE 下外发被 stub。
- **2c**：CodeMutator 在 worktree 产出 commit；坏输出 no-op；越界 diff 被门禁拒。
- **2d**：Deployer 指针翻转 + 回滚；健康检查触发自动回滚；晋升必经人工审核（auto-promote 默认关）。

覆盖率目标沿用项目 80%（见 `.claude/rules/common/testing.md`）。

---

## 10. 风险与权衡

- **自我修改代码的根本风险**：靠"门禁 + 隔离 + 人工审核 + 受限默认域 + 默认不自动晋升"五重控制把风险压到可接受；不追求全自动。
- **live 评估成本与非确定性**：用 cassette 做回归省成本，live 评估多跑取均值；评估集规模可配。
- **重启切换的可用性影响**：re-exec + 健康检查 + 自动回滚把停摆窗口压到最小；晋升默认人工触发、可选低峰执行。
- **演化域过窄 → 改进空间小 vs 过宽 → 风险大**：默认窄、可逐步放开，把胆量作为可调策略。
- **worktree/磁盘膨胀**：GC + 并发上限。
- **评估集代表性不足 → fitness 失真**：评估集可迭代扩充；以历史真实 memorial 为底盘提升代表性。

---

## 11. 命名

沿用 Phase 1 中文化术语：在役 / 候选 / 已归档；代码变体位面统称"代码位面"。新动作面向用户的措辞：评估（沙箱跑分）、晋升（切换到该代码）、回滚。

---

## 12. 增量切分与实施顺序

```
2a 代码变体基底（低风险，基础设施）
   └─> 2b 沙箱运行器 + 评估底座（高风险，安全关键核心）
          └─> 2c 代码变异生成器（高风险，自动生成）
                 └─> 2d 门禁晋升 + 运维（中风险）
```

今天的产物 = 本 Phase 2 架构 spec。随后 writing-plans **先针对 2a** 出实施计划；2b/2c/2d 各自再走 spec→plan→build 细化。

---

## 13. 与 Phase 1 的衔接（承接 §12 预留点）

Phase 1 §12 预留的三个扩展点在此全部兑现：

- `universes.origin` 的 `code_variant` 枚举 → 代码变体位面直接采用。
- manifest 的"代码层"段 → 收纳 worktree 路径 / `code_ref` / 评估元数据。
- `UniverseManager` 的"可重定向运行态"抽象 → 代码变体作为新的重定向维度接入，不改 1a/1b 契约。

Phase 1 的 `UniverseStore` / `UniverseManager` / `UniverseEvolver` / `fitness` / `mutator` 全部复用或平行扩展，Phase 2 不另起炉灶。
