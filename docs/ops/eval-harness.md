# 评估套件运维：跑评估 / 读 fitness / debug 回归

运维者视角：代码变体形成评估与推荐结论时，评估底座怎么跑、fitness 结果怎么读、
回归（变体跑挂或掉分）怎么排查。当前没有代码晋升或部署入口。

> **相关设计**：[../design/universe/eval.md](../design/universe/eval.md)（评估机制与适应度语义）、[../design/universe/code-variant.md](../design/universe/code-variant.md)（代码变体闭环）
> **相关实现**：[../impl/universe/README.md](../impl/universe/README.md)

## 1. 前置：开启代码变体

评估套件只在代码变体闭环里被触发，默认全关。需在 agent 配置打开：

| 配置 | 默认 | 说明 |
|---|---|---|
| `code_variant_enabled` | False | 代码变体总开关（opt-in），关着时 `propose_code_variant` 直接返回 `disabled` |
| `code_variant_evolvable_paths` | selector/planner/tools 等 | 演化域 allowlist，限制变异只能落低风险路径 |
| `code_variant_eval_set_size` | 20 | 回放评估集规模 |
| `code_variant_sandbox_timeout_s` | 900 | Gate 全程 + 沙箱单步超时 |
| `universe_promote_margin` | 0.05 | 变体须赢冠军此差距才被标 `recommended` |
| `code_variant_auto_promote` | False | 兼容保留字段；当前提案链路不读取它，也不存在自动部署入口 |

## 2. 怎么跑评估套件

评估**不是单独的命令**，是 `UniverseEvolver.propose_code_variant` 闭环的一环，触发途径：

- **Web UI**：代码变体位面页发起提案（`POST /api/universes/propose-code`），后端编排 `分支 → 变异 → 门禁 → 评估 → 记录`。
- **API 直调**：`POST /api/universes/propose-code`，body 带 `target_path` / `hypothesis` / 可选 `parent_id`。

一次提案内部按顺序跑：

```
branch_code_variant   起 worktree + 分支 universe/<id>
code_mutator.mutate   LLM 在 worktree 内改一个文件并 commit
Gate.run              ① compileall  ② import tianshu  ③ pytest -q   （fail-fast）
EvalHarness.evaluate  起受管评估进程 → 逐条回放历史 goal → 轮询至终态 → 聚合打分
save_variant_eval_run 落 variant_eval_runs 表
```

评估集来自历史：取最近若干条 **status=completed** 的 edict goal 去重（`select_eval_set`）。所以**评估只回放冠军已跑通的真实任务** —— 没有历史已完成诏令时评估集为空，fitness 各项归一为 0。

评估运行在受管子进程中：使用临时端口、独立 `_eval.db`（worktree 同级）和
`TIANSHU_EVAL_MODE=1`，受 wall timeout 约束，退出时收敛进程组并删除 DB 及
WAL/SHM。当前 `trusted-local` 宿主模式不提供可证明的内存、CPU、文件系统或网络
强隔离；`secure-remote` 在没有受验证后端时直接拒绝启动。

## 3. 怎么解读 fitness 结果

每次评估落一条 `variant_eval_runs`（按 `universe_id` 建索引），并把 fitness 写回该位面（Web UI 可见）。`status` 给出结论：

| status | 含义 | 运维动作 |
|---|---|---|
| `disabled` | `code_variant_enabled=False` | 先开开关 |
| `no_collaborators` | gate/eval/mutator/code_store 未装配 | 查 app lifespan 装配 |
| `no_champion` | 没有冠军位面作 parent | 先 `ensure_genesis` |
| `no_mutation` | LLM 没产出有效改动 | 变体已 archive，无需处理；可换 hypothesis 重试 |
| `gate_failed` | 三关之一红 | 见 §4，detail 字段给出 stage |
| `evaluated` | 过门禁、已打分，但**未超 margin** | 不形成推荐，分数仅供参考 |
| `recommended` | 超 margin，赢了冠军 | 人工审完整 diff + eval 记录，决定是否保留该推荐；当前不能部署 |
| `error` | 闭环异常 | 看日志 `[EVOLVER] propose_code_variant failed` |

**fitness 字段**（`compute_fitness` 输出，越高越好）：

| 字段 | 读法 |
|---|---|
| `score` | 综合分（五维加权），跨变体 / 跟冠军直接比 |
| `samples` | 评估集实际样本数（= 回放的 memorial 总数）|
| `success_rate` | 成功率（completed/approved 占比），**首要看这个，掉了即真实退化** |
| `audit_rate` | 审计通过率（audit verdict=pass 占比）|
| `retry_score` | 重试反向分，越接近 1 越少重试 |
| `cost_score` | 成本反向分，越接近 1 越省（注意它在默认权重下占比很小）|
| `feedback` | 累积反馈分原始值 |

判断「是否达到推荐阈值」：
`variant.score >= champion.score + universe_promote_margin`。`samples` 太小（如 < 5）时
分数噪声大，不要据此形成未来受治理激活判断——调大 `code_variant_eval_set_size` 或先
积累更多历史诏令。

## 4. 回归 debug

### 4.1 Gate 失败（status=gate_failed）

eval run 的 `gate_detail` 给出 `{stage, detail}`，detail 是失败命令的末尾输出（截断）：

| stage | 含义 | 排查 |
|---|---|---|
| `static` | `compileall` 失败 | 变体有语法错；看 detail 里的文件 + 行号 |
| `import` | `import tianshu` 冒烟失败 | 变体破坏了模块顶层（坏 import / 顶层异常）|
| `test` | worktree 内 `pytest -q` 有红 | 变体破坏了既有行为；detail 给失败测试名 |

复现：进变体 worktree，注入 `PYTHONPATH=<worktree>/src` 后手跑同一命令（Gate 就是这么跑的）：

```bash
cd ~/.tianshu/universes/worktrees/<universe_id>
PYTHONPATH=$PWD/src .venv/bin/python -m compileall -q src
PYTHONPATH=$PWD/src .venv/bin/python -c "import tianshu"
PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q
```

看变体改了什么：`git diff <start_ref>`（start_ref 记在 `~/.tianshu/universes/worktrees/_meta/<id>.json`）。

### 4.2 评估跑不动 / 分数异常（status=evaluated 但 success_rate 低）

评估走真实 HTTP，单条 goal 失败只 warning 不中断。查日志关键字：

| 日志 | 含义 |
|---|---|
| `sandbox up: http://...` | 沙箱起来了（带 pid + db 路径）|
| `sandbox failed to become healthy` | 沙箱 60s 内未健康 —— 变体启动就崩，多半是运行期（非 import 期）错误；看子进程 stdout |
| `eval: failed to submit goal` | POST /api/edicts 失败 |
| `eval: timeout waiting for edict ...` | 单条 goal 超 `goal_timeout_s`（默认 300s）未到终态 —— 变体让任务卡死或变慢 |

`success_rate` 掉 = 变体让本来跑通的历史 goal 跑挂了。逐条对比：同一 goal 在冠军沙箱 vs 变体沙箱的 memorial 终态差异。`samples` 比 `code_variant_eval_set_size` 小，通常是历史已完成诏令不够。

### 4.3 受管评估进程的资源边界

`trusted-local` 的显式宿主回退不宣称内存或强沙箱隔离；执行收据会记录该能力缺口。
`secure-remote` 在没有可证明隔离的后端时直接拒绝启动，不会静默回退宿主。

### 4.4 安全提醒

live 评估需要 LLM key，untrusted 变体进程能拿到 key（理论可外泄）。因此当前闭环只允许
生成变体、跑门禁与评估并给出 `recommended`，没有晋升、回滚或部署代码的入口。变异与
评估事件可在当前进程的 EventBus 中观察，持久证据以 `variant_eval_runs` 和位面记录为准；
如果未来开放受治理部署，仍须先人工审完整 diff，并补齐独立的 promotion/rollback adapter
与可验证收据。

## 5. 相关落盘

| 路径 | 内容 |
|---|---|
| `~/.tianshu/universes/worktrees/<id>/` | 变体 worktree（git 分支 `universe/<id>`）|
| `~/.tianshu/universes/worktrees/_meta/<id>.json` | sidecar `{branch, start_ref}`，diff 起点 |
| `variant_eval_runs` 表 | 每次评估记录（gate 结果 / fitness / cost），按 `universe_id` 建索引 |
| `universes.fitness_json` | 该位面最近一次 fitness |
