# Eval Harness 与 Fitness 门禁

> 自改平台的命门：一份自动生成的代码变体在碰真实任务前，必须先在隔离沙箱里**回放历史目标**、按统一适应度**打分**、与现冠军**回归比对**，再过门禁才配被人工晋升。本篇讲「为什么这样评 + 机制怎么转」。
>
> **相关实现**：[../../impl/universe/README.md](../../impl/universe/README.md)
> **相关设计**：[./code-variant.md](./code-variant.md)、[./evolution.md](./evolution.md)

## 1. 为什么要专门的评估底座

代码变体是无界风险面（崩溃 / 死循环 / 泄漏 / 静默劣化），「择优晋升」需要 fitness，但**不能拿可能损坏的代码跑真实流量**。于是评估底座承担三件事：

| 诉求 | 机制 |
|---|---|
| 变体行为可量化 | 回放历史代表性目标 → 聚合五维信号 → 一个标量 score |
| 与冠军可比 | 变体和冠军共用同一 `compute_fitness` 语义、同一评估集，分数同尺度 |
| 评估期绝不伤生产 | 跑在隔离子进程 + 隔离 DB 副本 + `EVAL_MODE` 副作用围栏 + 资源闸里 |

`EvalHarness`（`eval_harness.py`）是回放打分主体，`compute_fitness`（`fitness.py`）是归一聚合纯函数，`Gate`（`gate.py`）是打分前的硬门禁，`Deployer`（`deployer.py`）管晋升落地。编排者是 `UniverseEvolver.propose_code_variant`（`evolver.py`）。

## 2. Eval Case：定义与历史目标选取

一个 eval case = **一条历史 goal 字符串**。评估集是这些 goal 的去重列表。

`EvalHarness.select_eval_set(size)`：

- 取 `storage.list_edicts(status="completed", limit=size*3)` —— 只选**已成功完成**的历史诏令，按创建时间倒序（最近优先）。
- 逐条取 `edict.goal.strip()`，**按文本去重**，攒满 `size` 条即止。

为什么用「历史已完成 goal」而非合成用例：这些是平台真实承接过、且当前冠军能跑通的任务 —— 它们天然构成一条**冠军基线为 100% 成功**的回归集。变体在同一集上若掉成功率，就是真实退化。

## 3. 沙箱变体回放与轮询至终态

`EvalHarness.evaluate(worktree, *, eval_set, seed_db, goal_timeout_s=300)`：

```
iso_db = worktree.parent / "_eval.db"        # 放 worktree 同级，保持 worktree 文件系统干净
若 seed_db: 拷贝为 iso_db                      # 可携带 persona / LLM 配置作初始数据
with sandbox.session(worktree, db_path=iso_db) as h:   # 隔离子进程拉起变体
    for goal in eval_set:
        _run_goal(h.base_url, goal, goal_timeout_s)     # 串行回放
    stats = aggregate_db_stats(h.db_path)               # 聚合沙箱 DB 全部 memorial
return {"fitness": score(stats), "stats": stats, "n": len(eval_set)}
```

**沙箱**（`SandboxRunner.session`）从变体 worktree 拉起隔离子进程：临时空闲端口 + `TIANSHU_DB_PATH` 指向隔离 DB + `TIANSHU_EVAL_MODE=1` + 内存 `RLIMIT_AS` 闸；`PYTHONPATH=<worktree>/src` 前置遮蔽，确保 editable 安装下跑的是**变体代码**而非主仓。等 `/health` 健康才返回，退出时 kill 进程 + 删 DB。

**回放单条 goal**（`_run_goal`）走的是真实 HTTP 路径，不偷工：

```
POST {base_url}/api/edicts  {"goal": goal}  → data.id           （提交失败仅 warning，eval 继续）
轮询 GET /api/edicts/{id}/memorial  每 2s    → data.status
  status ∈ {completed, approved, failed, rejected} → 终态，返回
  超 goal_timeout_s 仍未终态 → warning 放弃（不抛异常，不卡死整个 eval）
```

终态集 `_TERMINAL_STATUSES = {completed, approved, failed, rejected}` —— 与下方成功口径一致。超时与提交失败都**不致命**：评估必须能跑完整集，单点卡死不能拖垮自我改进闭环。

## 4. 五维指标 + 权重 + 归一聚合

回放完毕，`aggregate_db_stats(db_path)` 直接读沙箱 DB 的全部 `memorials`，聚合成 7 个原始量（与 `storage.universe_memorial_stats` 同构，但不按 `universe_id` 过滤 —— 沙箱 DB 只有本次 eval 的数据）：

| 原始量 | 口径 |
|---|---|
| `total` | memorial 总数 |
| `success` | status ∈ {completed, approved} 的数量 |
| `retries` | Σ max(0, attempt-1) |
| `audited` / `audit_pass` | 有 audit 的数 / 其中 verdict=="pass" 的数 |
| `cost` | Σ usage_json.cost_cny |
| `feedback` | Σ feedback_score（累积整数，可正可负） |

`compute_fitness(stats, weights)` 把它们归一到 [0,1] 再加权。默认 **weights=(0.4, 0.15, 0.2, 0.1, 0.15)**，依次对应 (success, cost, audit, retry, feedback)：

| 维度 | 归一公式 | 方向 | 默认权重 |
|---|---|---|---|
| `success_rate` | success / total | 越高越好 | 0.40 |
| `cost_score` | `avg = (cost*1000)/total`；`1/(1+avg)`，无成本=1 | 越低越好（反向分） | 0.15 |
| `audit_rate` | audit_pass / audited | 越高越好 | 0.20 |
| `retry_score` | `max(0, 1 - (retries/total)/2)` | 越低越好（反向分） | 0.10 |
| `feedback`(fb_norm) | `0.5 + 0.5·fb/(1+|fb|)`，把累积整数 squash 到 (0,1) | 越高越好 | 0.15 |

```
score = 0.4·success_rate + 0.15·cost_score + 0.2·audit_rate
      + 0.1·retry_score + 0.15·fb_norm
```

返回 `{score, samples, success_rate, audit_rate, retry_score, cost_score, feedback}`，score 四位小数。所有分母为 0 时 `_safe_ratio` 返回 0.0，不会除零。

**设计取舍**：成功率独占 0.4 —— 跑通是底线；cost/retry 取反向分鼓励「又对又省」；feedback 用 squash 而非线性，避免单条极端反馈淹没其余维度。这套权重既给行为层位面（`universe_memorial_stats` → `compute_fitness`）用，也给代码变体（`aggregate_db_stats` → `compute_fitness`）用 —— **同一适应度语义保证两者可比**。

## 5. 变体 vs Champion 回归检测

评估完，`propose_code_variant` 做回归比对：

```
variant_score = fitness["score"]                              # 本次沙箱评估
champ_score   = champion.fitness["score"]  (无冠军 fitness 则 0)
margin        = cfg.universe_promote_margin  (默认 0.05)
variant_score >= champ_score + margin  →  status="recommended"
否则                                    →  status="evaluated"
```

`margin` 是**回归带宽**：变体必须赢冠军一个肉眼可辨的差距才算「值得晋升」，落在 margin 内的微小波动不构成晋升理由 —— 防止评估噪声驱动无意义的代码翻动。注意 `recommended` 也**只是推荐**，默认不自动晋升（见 §7）。

每次评估（无论结果）都落 `variant_eval_runs` 表（`save_variant_eval_run`），并 `update_universe_fitness(uid, fitness)` 把分写回该位面，供 Web UI 看历史。

## 6. 晋升门禁语义：Gate 三关 + margin

打分只是**晋升资格链**的最后一环。完整门禁是「三关硬门禁（fail-fast）→ 评估打分 → margin 回归 → 人工审 diff」。`Gate.run(worktree)` 在 `evaluate` **之前**跑，逐级短路：

| 关 | 命令（注入 `PYTHONPATH=<worktree>/src` + cwd=worktree） | 失败即毙 |
|---|---|---|
| ① static | `python -m compileall -q <worktree>/src` | 连编译都不过 |
| ② import | `python -c "import tianshu"` | import 冒烟失败 |
| ③ test | `python -m pytest -q`（worktree 内全量） | 任一测试红 |

`GateResult(passed, stage, detail)`：失败时 stage ∈ {static, import, test}，detail 取末尾输出（`_tail`，截 2000 字）；全过则 `stage="ok"`。任一关失败，`propose_code_variant` 落一条 `gate_passed=False` 的 eval run（detail 截 1000 字）并返回 `status="gate_failed"`，**根本不进评估**。整链超时由 `Gate` 的 `timeout_s`（默认 900s，对应配置 `code_variant_sandbox_timeout_s`）兜底。

晋升资格链：

```
gate ①②③ 全绿  →  沙箱回放打 fitness  →  variant ≥ champ + margin (recommended)
  →  人工审完整 diff + eval 记录  →  Deployer 晋升
```

**晋升落地**（`manager.promote_code_variant` + `Deployer`）：翻状态为冠军 + `deployer.stage` 只写 `current_ref` 指针**不重启**（重启是单独受控步骤）；真重启时 `os.execv` 自重启，重启后查 `/health`，不健康则指针翻回 previous 自动回滚 —— 坏晋升绝不让平台停摆。详见 [./code-variant.md](./code-variant.md) §7。

## 7. Worked Example：评估一个「persona 变异」代码变体

假设变体在演化域内改写了 selector 的人格选择逻辑（hypothesis：「对运维类 goal 更倾向选择稳健 persona」）。闭环（`propose_code_variant`）：

```
1. branch_code_variant(champion) → worktree + 分支 universe/<id>
2. code_mutator.mutate(worktree, target_path, hypothesis)  →  applied=True, 提交一个 commit
3. gate.run(worktree):  compileall ✓  →  import tianshu ✓  →  pytest -q ✓   (stage=ok)
4. select_eval_set(20):  取最近 20 条已完成 goal（含若干运维类 goal）
5. evaluate(worktree, eval_set):
     sandbox 起隔离进程(临时端口 + _eval.db + EVAL_MODE)
     逐条 POST /api/edicts 回放，轮询 memorial 至终态
     aggregate_db_stats:  total=20 success=20 audited=18 audit_pass=18
                          retries=3 cost=1.2 feedback=4
     compute_fitness → success_rate=1.0  audit_rate=1.0  retry_score≈0.925
                       cost_score=1/(1+(1.2*1000/20))≈0.0164  fb_norm≈0.9
                       score = 0.4·1 + 0.15·0.0164 + 0.2·1 + 0.1·0.925 + 0.15·0.9
                             ≈ 0.83
6. save_variant_eval_run(...) + update_universe_fitness(uid, fitness)
7. 冠军 score=0.80, margin=0.05  →  0.83 ≥ 0.80+0.05 ?  否（0.85>0.83）
     → status="evaluated"（达标但未过 margin，不推荐）
   若冠军 score=0.76 → 0.83 ≥ 0.81 → status="recommended"
8. 任一步异常 → status="error"，失败安全，不留半截状态
```

可见 cost_score 在这套权重下很小（0.15 权重 × 0.016），主导分的是成功率与审计率 —— 评估真正盯的是「跑得对、审得过」，成本只作微调。最终是否晋升仍由人看 §6 的完整 diff 拍板，平台默认 `code_variant_auto_promote=False`。

## 8. 配置项

| 配置 | 默认 | 作用 |
|---|---|---|
| `code_variant_eval_set_size` | 20 | 回放评估集规模（`select_eval_set` 上限）|
| `code_variant_sandbox_timeout_s` | 900 | Gate 全程 + 沙箱单步超时 |
| `code_variant_sandbox_mem_mb` | 2048 | 沙箱内存闸（`RLIMIT_AS`）|
| `universe_promote_margin` | 0.05 | 回归带宽：变体须赢冠军此差距才 `recommended` |
| `code_variant_auto_promote` | False | 代码层自动晋升（默认关，明确不推荐开）|

`goal_timeout_s`（单条回放轮询超时）默认 300s，由 `evaluate` 形参控制。
